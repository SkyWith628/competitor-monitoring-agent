"""[D] CLI 테스트. 다른 모듈(repo·notify·dashboard·pipeline)은 전부 가짜로 바꿔 CLI 흐름만 검증한다."""
import sys
import types
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

import app.infra
from app.__main__ import cli, last_week, prev_week
from app.core.models import Brand, Category, ChangeEvent, ChangeType, SourceType, WeeklyReport
from app.infra.notify import email, render, slack
from app.infra.storage import repo

runner = CliRunner()


def _event(id_: int, importance: int | None = None) -> ChangeEvent:
    return ChangeEvent(id=id_, brand=Brand.SPAO, change_type=next(iter(ChangeType)), title=f"이벤트{id_}",
                       url="https://example.com", importance=importance)


def _report(week: str, **kw) -> WeeklyReport:
    return WeeklyReport(week=week, tldr="요약", top_events=[], brand_summaries=[], insights=[], actions=[], **kw)


@pytest.fixture
def alerts(monkeypatch):
    """운영 경고를 실제로 보내지 않고 모은다."""
    sent: list[str] = []
    monkeypatch.setattr(slack, "send_ops_alert", sent.append)
    return sent


# ---------- last_week / prev_week ----------
def test_last_week_uses_korean_time():
    # weekly.yml 실행 시각: 일요일 23:00 UTC = 월요일 08:00 KST(W48) → 지난주는 W47
    assert last_week(datetime(2026, 11, 22, 23, 0, tzinfo=UTC)) == "2026-W47"


def test_prev_week_crosses_year_boundary():
    assert prev_week("2026-W47") == "2026-W46"
    assert prev_week("2027-W01") == "2026-W53"  # 2026년은 53주차까지 있다


# ---------- collect ----------
@pytest.fixture
def counts(monkeypatch):
    """count_items_since 호출을 기록하고, 브랜드별로 정해 둔 건수를 돌려준다(기본 3건)."""
    state = types.SimpleNamespace(calls=[], by_brand={}, silent={})

    def count_items_since(brand, source_types, since):
        state.calls.append((brand, source_types, since))
        return state.by_brand.get(brand, 3)

    monkeypatch.setattr(repo, "count_items_since", count_items_since, raising=False)
    monkeypatch.setattr(repo, "silent_sources", lambda brand, source_types, since: state.silent.get(brand, []))
    return state


def test_collect_rejects_unknown_source_without_alert(alerts):
    result = runner.invoke(cli, ["collect", "--source", "blog"])
    assert result.exit_code == 2 and alerts == []


def test_collect_continues_after_one_brand_fails_then_exits_nonzero(monkeypatch, alerts, counts):
    from app.site_monitor import pipeline

    def run(brand):
        if brand == Brand.SPAO:
            raise RuntimeError("파싱 실패")
        return []

    monkeypatch.setattr(pipeline, "run", run)
    result = runner.invoke(cli, ["collect", "--source", "site"])
    assert result.exit_code == 1  # 끝까지 돌고 나서 실패로 종료
    assert len(alerts) == 1 and "site/spao" in alerts[0]
    assert "[site/uniqlo] 이벤트 0건" in result.output
    assert Brand.SPAO not in [c[0] for c in counts.calls]  # 실패한 브랜드는 0건 점검을 하지 않는다
    assert len(counts.calls) == len(Brand) - 1


def test_collect_site_alerts_when_zero_items(monkeypatch, alerts, counts):
    from app.site_monitor import pipeline

    monkeypatch.setattr(pipeline, "run", lambda brand: [])
    counts.by_brand[Brand.SPAO] = 0
    started = datetime.now()
    result = runner.invoke(cli, ["collect", "--source", "site"])
    assert result.exit_code == 0, result.output  # 0건은 경고일 뿐 실패가 아니다
    assert len(alerts) == 1 and "파싱 0건" in alerts[0] and "site/spao" in alerts[0]
    _, source_types, since = counts.calls[0]
    assert source_types == [SourceType.SITE_PRODUCT, SourceType.SITE_PAGE]
    assert since.tzinfo is None and started <= since <= datetime.now()  # 실행 직전의 naive 시각


def test_collect_sns_checks_sns_posts(monkeypatch, alerts, counts):
    from app.media_monitor import pipeline

    monkeypatch.setattr(pipeline, "run_sns", lambda brand: [])
    counts.by_brand[Brand.SPAO] = 0
    result = runner.invoke(cli, ["collect", "--source", "sns", "--brand", "spao"])
    assert result.exit_code == 0, result.output
    assert counts.calls[0][:2] == (Brand.SPAO, [SourceType.SNS_POST])
    assert len(alerts) == 1 and "파싱 0건 sns/spao" in alerts[0]


def test_collect_alerts_when_one_of_several_sources_goes_silent(monkeypatch, alerts, counts):
    from app.media_monitor import pipeline

    monkeypatch.setattr(pipeline, "run_sns", lambda brand: [])
    counts.silent[Brand.SPAO] = ["instagram"]  # 유튜브는 들어와서 합계는 0이 아니다
    result = runner.invoke(cli, ["collect", "--source", "sns"])
    assert result.exit_code == 0, result.output
    assert len(alerts) == 1 and "파싱 0건 sns/spao (instagram)" in alerts[0]


def test_collect_news_skips_zero_check(monkeypatch, alerts, counts):
    from app.media_monitor import pipeline

    monkeypatch.setattr(pipeline, "run_news", lambda brand: [])
    result = runner.invoke(cli, ["collect", "--source", "news"])
    assert result.exit_code == 0, result.output
    assert counts.calls == [] and alerts == []


def test_collect_not_implemented_is_not_a_failure(monkeypatch, alerts, counts):
    from app.site_monitor import pipeline

    def run(brand):
        raise NotImplementedError

    monkeypatch.setattr(pipeline, "run", run)
    result = runner.invoke(cli, ["collect", "--source", "site", "--brand", "spao"])
    assert result.exit_code == 0, result.output
    assert "아직 구현되지 않음" in result.output
    assert alerts == [] and counts.calls == []


def test_collect_count_failure_alerts_and_exits_nonzero(monkeypatch, alerts):
    from app.site_monitor import pipeline

    def boom(brand, source_types, since):
        raise RuntimeError("DB 연결 실패")

    monkeypatch.setattr(pipeline, "run", lambda brand: [])
    monkeypatch.setattr(repo, "count_items_since", boom, raising=False)
    result = runner.invoke(cli, ["collect", "--source", "site", "--brand", "spao"])
    assert result.exit_code == 1
    assert len(alerts) == 1 and "수집 건수 확인 실패 site/spao" in alerts[0]


# ---------- classify ----------
@pytest.fixture
def classify_env(monkeypatch):
    """미분류 이벤트 3건 + 가짜 분류기(중요도 5·3·5). 저장·긴급 알림 호출을 기록한다.

    pending_urgent 는 실제처럼 "분류된 중요도 5 중 아직 알림이 안 나간 것"을 돌려준다.
    """
    from app.analysis import classify as classify_mod

    state = types.SimpleNamespace(pending=[_event(1), _event(2), _event(3)], updated=[], urgent=[],
                                  marked=[], fail_ids=set())

    def fake_classify(events):
        for e, importance in zip(events, (5, 3, 5), strict=False):
            e.importance = importance
        return events

    def pending_urgent(days=7):
        saved = [e for batch in state.updated for e in batch]
        return [e for e in saved if e.importance == 5 and e.id not in state.marked]

    def send_urgent(events):
        state.urgent.append([e.id for e in events])
        return [e for e in events if e.id not in state.fail_ids]

    monkeypatch.setenv("SLACK_WEBHOOK_REPORT", "https://hooks.example/report")
    monkeypatch.setattr(repo, "unclassified_events", lambda: state.pending)
    monkeypatch.setattr(repo, "update_events", state.updated.append)
    monkeypatch.setattr(repo, "pending_urgent", pending_urgent)
    monkeypatch.setattr(repo, "mark_urgent_sent", state.marked.extend)
    monkeypatch.setattr(classify_mod, "classify", fake_classify)
    monkeypatch.setattr(slack, "send_urgent", send_urgent)
    return state


def test_classify_sends_urgent_only_for_importance_5(classify_env, alerts):
    result = runner.invoke(cli, ["classify"])
    assert result.exit_code == 0, result.output
    assert [e.id for e in classify_env.updated[0]] == [1, 2, 3]
    assert classify_env.urgent == [[1, 3]] and classify_env.marked == [1, 3]
    assert "분류 완료 3건" in result.output and "긴급 알림 2건" in result.output
    assert alerts == []


def test_classify_without_urgent_events_sends_nothing(classify_env, monkeypatch, alerts):
    from app.analysis import classify as classify_mod

    monkeypatch.setattr(classify_mod, "classify", lambda events: events)  # 중요도 없음
    result = runner.invoke(cli, ["classify"])
    assert result.exit_code == 0, result.output
    assert classify_env.urgent == [] and alerts == []


def test_classify_nothing_to_do(classify_env, alerts):
    classify_env.pending = []
    result = runner.invoke(cli, ["classify"])
    assert result.exit_code == 0 and "분류할 이벤트 없음" in result.output
    assert classify_env.updated == [] and classify_env.urgent == []


def test_classify_llm_failure_alerts_and_exits_nonzero(classify_env, monkeypatch, alerts):
    from app.analysis import classify as classify_mod

    def boom(events):
        raise RuntimeError("LLM 529")

    monkeypatch.setattr(classify_mod, "classify", boom)
    result = runner.invoke(cli, ["classify"])
    assert result.exit_code == 1
    assert len(alerts) == 1 and "LLM 분류 실패" in alerts[0]
    assert classify_env.updated == [] and classify_env.urgent == []


def test_classify_failed_urgent_alert_is_reported_and_retried(classify_env, alerts):
    classify_env.fail_ids = {3}  # 3번 이벤트의 긴급 알림만 실패
    result = runner.invoke(cli, ["classify"])
    assert result.exit_code == 1  # 조용히 넘어가지 않는다
    assert classify_env.marked == [1]
    assert len(alerts) == 1 and "긴급 알림 1건 발송 실패" in alerts[0]

    # 다음 실행: 새로 분류할 것이 없어도 실패했던 3번만 다시 보낸다 (1번은 중복 발송하지 않는다)
    classify_env.pending, classify_env.fail_ids = [], set()
    result = runner.invoke(cli, ["classify"])
    assert result.exit_code == 0, result.output
    assert classify_env.urgent == [[1, 3], [3]] and classify_env.marked == [1, 3]


def test_classify_without_report_webhook_keeps_urgent_pending(classify_env, monkeypatch, alerts):
    monkeypatch.delenv("SLACK_WEBHOOK_REPORT")
    result = runner.invoke(cli, ["classify"])
    assert result.exit_code == 0, result.output
    assert classify_env.urgent == [] and classify_env.marked == []  # 보낸 것으로 기록하지 않는다
    assert "긴급 알림 2건 대기 중" in result.output


# ---------- report ----------
@pytest.fixture
def report_env(tmp_path, monkeypatch):
    """리포트 생성·렌더·발송·저장을 전부 가짜로 바꾸고 호출 내용을 기록한다."""
    from app.analysis import report_builder
    from app.core import config

    state = types.SimpleNamespace(
        root=tmp_path, prev={}, get_report=[], render=[], slack=[], email=[], saved=[],
        slack_error=None, email_error=None, sent_channels=set(), builds=[],
        slack_parts=[], slack_fail_at=None, week_events=[],
    )

    def build(week):
        state.builds.append(week)
        r = _report(week, stats={"new_products": 7})
        if len(state.builds) > 1:  # 다시 만들면 LLM이 다른 문장을 쓴다
            r.tldr = f"요약 v{len(state.builds)}"
        return r

    def save_report(report, sent=True):
        state.saved.append((report.week, sent))
        if sent or report.week not in state.prev:
            state.prev[report.week] = report

    def get_report(week):
        state.get_report.append(week)
        return state.prev.get(week)

    def to_html(report, prev_stats=None):
        state.render.append(("html", prev_stats))
        return f"<html>{report.tldr}</html>"

    def to_markdown(report, prev_stats=None):
        state.render.append(("md", prev_stats))
        return f"# {report.tldr}"

    def send_report(report, full_report_url=None, start=0, on_sent=None):
        """본 메시지(0번) + 후속 메시지 1개(1번). slack_fail_at 번째 메시지에서 실패할 수 있다."""
        if state.slack_error:
            raise state.slack_error
        for i in range(start, 2):
            if state.slack_fail_at == i:
                raise RuntimeError(f"슬랙 {i}번 메시지 실패")
            state.slack_parts.append(i)
            if on_sent:
                on_sent(i)
        state.slack.append((report.week, full_report_url))

    def send_html(subject, html, text=None):
        if state.email_error:
            raise state.email_error
        state.email.append((subject, html, text))

    monkeypatch.delenv("REPORT_URL", raising=False)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(report_builder, "build", build)
    monkeypatch.setattr(repo, "events_of_week", lambda week: state.week_events)
    monkeypatch.setattr(repo, "report_channels_sent", lambda week: set(state.sent_channels))
    monkeypatch.setattr(repo, "mark_report_sent", lambda week, channel: state.sent_channels.add(channel))
    monkeypatch.setattr(repo, "get_report", get_report, raising=False)
    monkeypatch.setattr(repo, "save_report", save_report)
    monkeypatch.setattr(render, "to_html", to_html)
    monkeypatch.setattr(render, "to_markdown", to_markdown, raising=False)
    monkeypatch.setattr(slack, "send_report", send_report, raising=False)
    monkeypatch.setattr(email, "send_html", send_html)
    return state


def test_report_dry_run_writes_html_and_md_and_sends_nothing(report_env, alerts):
    result = runner.invoke(cli, ["report", "--week", "2026-W47", "--dry-run"])
    assert result.exit_code == 0, result.output
    data = report_env.root / "data"
    assert "요약" in (data / "report-2026-W47.html").read_text(encoding="utf-8")
    assert (data / "report-2026-W47.md").read_text(encoding="utf-8") == "# 요약"
    assert report_env.slack == [] and report_env.email == [] and alerts == []
    assert report_env.saved == [("2026-W47", False)]


def test_report_sends_both_channels_with_prev_week_stats(report_env, monkeypatch, alerts):
    monkeypatch.setenv("REPORT_URL", "https://example.com/dashboard")
    report_env.prev["2026-W46"] = _report("2026-W46", stats={"new_products": 4})
    result = runner.invoke(cli, ["report", "--week", "2026-W47"])
    assert result.exit_code == 0, result.output
    assert report_env.get_report == ["2026-W46"]
    assert report_env.render == [("html", {"new_products": 4}), ("md", {"new_products": 4})]
    assert report_env.slack == [("2026-W47", "https://example.com/dashboard")]
    assert report_env.email == [("[경쟁사 주간 리포트] 2026-W47", "<html>요약</html>", "# 요약")]
    assert report_env.saved == [("2026-W47", True)] and alerts == []


def test_report_without_prev_week_or_url(report_env, alerts):
    result = runner.invoke(cli, ["report", "--week", "2027-W01"])
    assert result.exit_code == 0, result.output
    assert report_env.get_report == ["2026-W53"]
    assert report_env.render == [("html", None), ("md", None)]
    assert report_env.slack == [("2027-W01", None)]


def test_report_default_week_is_last_week(report_env, alerts):
    result = runner.invoke(cli, ["report", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert report_env.saved == [(last_week(), False)]


def test_report_slack_failure_still_sends_email(report_env, alerts):
    report_env.slack_error = RuntimeError("웹훅 404")
    result = runner.invoke(cli, ["report", "--week", "2026-W47"])
    assert result.exit_code == 1
    assert len(report_env.email) == 1
    assert len(alerts) == 1 and "2026-W47/slack" in alerts[0]
    assert report_env.saved == [("2026-W47", True)]  # 한 채널은 나갔다


def test_report_email_failure_still_sends_slack(report_env, alerts):
    report_env.email_error = RuntimeError("SMTP 인증 실패")
    result = runner.invoke(cli, ["report", "--week", "2026-W47"])
    assert result.exit_code == 1
    assert len(report_env.slack) == 1
    assert len(alerts) == 1 and "2026-W47/email" in alerts[0]
    assert report_env.saved == [("2026-W47", True)]


def test_report_both_channels_fail_saved_as_unsent(report_env, alerts):
    report_env.slack_error = RuntimeError("웹훅 404")
    report_env.email_error = RuntimeError("SMTP 인증 실패")
    result = runner.invoke(cli, ["report", "--week", "2026-W47"])
    assert result.exit_code == 1
    assert len(alerts) == 2
    assert report_env.saved == [("2026-W47", False)]
    assert (report_env.root / "data" / "report-2026-W47.html").exists()  # 파일은 남는다


def test_report_rerun_sends_only_the_failed_channel_with_same_content(report_env, alerts):
    report_env.email_error = RuntimeError("SMTP 인증 실패")
    assert runner.invoke(cli, ["report", "--week", "2026-W47"]).exit_code == 1
    assert len(report_env.slack) == 1 and report_env.email == []

    report_env.email_error = None  # 메일 설정을 고친 뒤 같은 명령을 다시 실행
    result = runner.invoke(cli, ["report", "--week", "2026-W47"])
    assert result.exit_code == 0, result.output
    assert len(report_env.slack) == 1  # 슬랙은 다시 나가지 않는다
    assert report_env.builds == ["2026-W47"]  # LLM으로 다시 만들지 않는다
    assert report_env.email == [("[경쟁사 주간 리포트] 2026-W47", "<html>요약</html>", "# 요약")]
    assert report_env.sent_channels >= {"slack", "email"}


def test_report_rerun_after_full_send_does_nothing(report_env, alerts):
    assert runner.invoke(cli, ["report", "--week", "2026-W47"]).exit_code == 0
    result = runner.invoke(cli, ["report", "--week", "2026-W47"])
    assert result.exit_code == 0 and "이미 발송됨" in result.output
    assert len(report_env.slack) == 1 and len(report_env.email) == 1
    assert report_env.builds == ["2026-W47"] and alerts == []


def test_report_force_rebuilds_and_resends_everything(report_env, alerts):
    assert runner.invoke(cli, ["report", "--week", "2026-W47"]).exit_code == 0
    result = runner.invoke(cli, ["report", "--week", "2026-W47", "--force"])
    assert result.exit_code == 0, result.output
    assert len(report_env.slack) == 2 and len(report_env.email) == 2
    assert report_env.builds == ["2026-W47", "2026-W47"]
    assert report_env.prev["2026-W47"].tldr == "요약 v2"  # 다시 보낸 내용이 기록으로 남는다


def test_report_dry_run_after_send_keeps_the_sent_record(report_env, alerts):
    assert runner.invoke(cli, ["report", "--week", "2026-W47"]).exit_code == 0
    result = runner.invoke(cli, ["report", "--week", "2026-W47", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert len(report_env.slack) == 1 and len(report_env.email) == 1  # dry-run 은 보내지 않는다
    assert "요약 v2" in (report_env.root / "data" / "report-2026-W47.md").read_text(encoding="utf-8")
    assert report_env.prev["2026-W47"].tldr == "요약"  # 실제로 나간 내용은 그대로


def test_report_failure_alert_does_not_leak_webhook_url(report_env, alerts):
    import httpx

    url = "https://hooks.slack.com/services/T000/B000/SECRETTOKEN"
    try:
        httpx.Response(404, request=httpx.Request("POST", url)).raise_for_status()
    except httpx.HTTPStatusError as e:
        report_env.slack_error = e
    result = runner.invoke(cli, ["report", "--week", "2026-W47"])
    assert result.exit_code == 1
    assert "2026-W47/slack" in alerts[0] and "HTTP 404" in alerts[0]
    assert "SECRETTOKEN" not in alerts[0] + result.output and "hooks.slack.com" not in alerts[0] + result.output


def test_report_slack_followup_failure_resumes_without_resending_main(report_env, alerts):
    report_env.slack_fail_at = 1  # 본 메시지는 나가고 브랜드별 후속 메시지에서 실패
    assert runner.invoke(cli, ["report", "--week", "2026-W47"]).exit_code == 1
    assert report_env.slack_parts == [0] and "slack" not in report_env.sent_channels

    report_env.slack_fail_at = None
    result = runner.invoke(cli, ["report", "--week", "2026-W47"])
    assert result.exit_code == 0, result.output
    assert report_env.slack_parts == [0, 1]  # 본 메시지(0번)는 다시 나가지 않는다
    assert len(report_env.email) == 1 and report_env.builds == ["2026-W47"]


def test_report_warns_when_week_has_unclassified_events(report_env, alerts):
    report_env.week_events = [_event(1), _event(2, importance=3)]
    report_env.week_events[1].category = next(iter(Category))
    result = runner.invoke(cli, ["report", "--week", "2026-W47"])
    assert result.exit_code == 0, result.output
    assert len(alerts) == 1 and "미분류 이벤트 1건" in alerts[0]
    assert "1건이 미분류" in result.output


def test_report_build_failure_alerts_and_exits_nonzero(report_env, monkeypatch, alerts):
    from app.analysis import report_builder

    def boom(week):
        raise RuntimeError("LLM 529")

    monkeypatch.setattr(report_builder, "build", boom)
    result = runner.invoke(cli, ["report", "--week", "2026-W47"])
    assert result.exit_code == 1
    assert len(alerts) == 1 and "주간 리포트 생성 실패" in alerts[0]
    assert report_env.saved == [] and report_env.slack == [] and report_env.email == []


@pytest.mark.parametrize("week", ["2026-47", "2026-W7", "2026-W00", "2025-W53", "W47", "2026-W47 "])
def test_report_rejects_bad_week_format(report_env, alerts, week):
    result = runner.invoke(cli, ["report", "--week", week, "--dry-run"])
    assert result.exit_code == 2
    assert "YYYY-Www" in result.output
    assert report_env.saved == [] and alerts == []


# ---------- prune / dashboard ----------
def test_prune_defaults_to_8_weeks(monkeypatch):
    calls = []

    def prune_snapshots(weeks=8):
        calls.append(weeks)
        return 12

    monkeypatch.setattr(repo, "prune_snapshots", prune_snapshots, raising=False)
    result = runner.invoke(cli, ["prune"])
    assert result.exit_code == 0, result.output
    assert calls == [8] and "12건" in result.output
    assert runner.invoke(cli, ["prune", "--weeks", "4"]).exit_code == 0 and calls == [8, 4]
    assert runner.invoke(cli, ["prune", "--weeks", "0"]).exit_code == 2 and calls == [8, 4]


def test_dashboard_prints_output_path(tmp_path, monkeypatch):
    out = tmp_path / "data" / "dashboard.html"
    fake = types.ModuleType("app.infra.dashboard")
    fake.build = lambda out_path=None: out
    monkeypatch.setitem(sys.modules, "app.infra.dashboard", fake)
    monkeypatch.setattr(app.infra, "dashboard", fake, raising=False)
    result = runner.invoke(cli, ["dashboard"])
    assert result.exit_code == 0, result.output
    assert "dashboard.html" in result.output.replace("\n", "")


# ---------- 공통 준비: 한국 시간, 운영 DB 필수 ----------
def test_cli_runs_in_korean_time_even_on_utc_runner(monkeypatch):
    import time
    from datetime import timedelta

    monkeypatch.setenv("TZ", "UTC")  # GitHub Actions 러너
    time.tzset()
    assert runner.invoke(cli, ["prune", "--help"]).exit_code == 0  # --help 는 콜백 전에 끝난다
    monkeypatch.setattr(repo, "prune_snapshots", lambda weeks=8: 0)
    assert runner.invoke(cli, ["prune"]).exit_code == 0
    assert datetime.now().astimezone().utcoffset() == timedelta(hours=9)


def test_cli_refuses_to_fall_back_to_sqlite_when_database_required(monkeypatch, alerts):
    monkeypatch.setenv("REQUIRE_DATABASE_URL", "1")
    monkeypatch.setenv("DATABASE_URL", "")  # Secret 을 등록하지 않으면 Actions 는 빈 문자열을 넘긴다
    called = []
    monkeypatch.setattr(repo, "prune_snapshots", lambda weeks=8: called.append(weeks) or 0)
    result = runner.invoke(cli, ["prune"])
    assert result.exit_code == 1 and called == []
    assert "DATABASE_URL" in result.output
    assert len(alerts) == 1 and "실행 중단" in alerts[0]
