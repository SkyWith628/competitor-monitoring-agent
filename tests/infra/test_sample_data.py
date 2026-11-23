"""[D] 화면 확인용 데모 샘플 — 넣고 지우는 동안 실제 데이터와 실제 알림을 건드리지 않는지."""
from datetime import datetime, timedelta

import pytest
from typer.testing import CliRunner

from app.__main__ import KST, cli, last_week
from app.core.models import Brand, ChangeEvent, ChangeType, RawItem, SourceType, WeeklyReport
from app.infra import sample_data
from app.infra.storage import repo

runner = CliRunner()


@pytest.fixture
def ops(tmp_db, monkeypatch):
    alerts: list[str] = []
    monkeypatch.setattr("app.infra.notify.slack.send_ops_alert", alerts.append)
    return alerts


def _now() -> datetime:
    return datetime.now(KST).replace(tzinfo=None)


def _real_data() -> None:
    repo.save_events([ChangeEvent(brand=Brand.SPAO, change_type=ChangeType.NEWS, title="실제 이벤트",
                                  url="https://news.example.com/1", detected_at=_now())])
    repo.save_items([RawItem(brand=Brand.SPAO, source_type=SourceType.SNS_POST, source="instagram",
                             external_id="1", url="https://instagram.example.com/1", title="실제 게시물",
                             collected_at=_now())])
    repo.save_llm_call("real-model", 10, 2)


def test_demo_fills_the_dashboard_tables(ops):
    result = runner.invoke(cli, ["seed", "--demo"])
    assert result.exit_code == 0, result.output

    events = repo.recent_events(30)
    assert len(events) > 100 and all(sample_data.is_sample(e) and "[샘플]" in e.title for e in events)
    assert {e.brand for e in events} == set(Brand)
    assert all(e.detected_at <= _now() + timedelta(minutes=1) for e in events)
    sources = {(r["brand"], r["source"]) for r in repo.source_freshness()}
    assert len(sources) == 3 * len(Brand) and all(source.startswith("sample-") for _, source in sources)
    assert {u["model"] for u in repo.llm_usage()} == {"sample-classify-model", "sample-report-model"}
    report = repo.get_report(last_week())
    assert report is not None and report.tldr.startswith("[샘플]") and report.top_events
    assert repo.report_status()[0]["sent_at"] is None  # 발송된 적 없는 초안으로 남는다


def test_demo_triggers_no_llm_and_no_alerts(ops, monkeypatch):
    def no_llm(events):
        raise AssertionError(f"샘플 {len(events)}건이 LLM 분류로 넘어감")

    posts: list = []
    monkeypatch.setattr("app.analysis.classify.classify", no_llm)
    monkeypatch.setattr("httpx.post", lambda *a, **kw: posts.append(a))
    monkeypatch.setenv("SLACK_WEBHOOK_REPORT", "https://hooks.example/report")
    runner.invoke(cli, ["seed", "--demo"])

    assert any(e.importance == 5 for e in repo.recent_events(7))
    assert repo.unclassified_events() == [] and repo.pending_urgent() == []
    assert runner.invoke(cli, ["classify"]).exit_code == 0
    assert posts == []
    # 샘플 소스는 수집기가 없다 — "전에는 수집되던 소스가 멈췄다" 경고 대상이 아니어야 한다
    later = _now() + timedelta(hours=1)
    assert repo.silent_sources(Brand.SPAO, [SourceType.SNS_POST, SourceType.SITE_PRODUCT], since=later) == []


def test_seeding_twice_does_not_duplicate(ops):
    runner.invoke(cli, ["seed", "--demo"])
    first = (len(repo.recent_events(30)), repo.item_counts(), repo.llm_usage())
    runner.invoke(cli, ["seed", "--demo"])
    assert (len(repo.recent_events(30)), repo.item_counts(), repo.llm_usage()) == first


def test_clear_removes_samples_and_keeps_real_data(ops):
    _real_data()
    runner.invoke(cli, ["seed", "--demo"])
    runner.invoke(cli, ["seed"])  # 리허설 샘플도 함께 지워져야 한다

    result = runner.invoke(cli, ["seed", "--clear"])
    assert result.exit_code == 0, result.output
    assert [e.title for e in repo.recent_events(30)] == ["실제 이벤트"]
    assert repo.events_of_week(sample_data.REHEARSAL_WEEK) == []
    assert repo.item_counts() == {"spao": {"sns_post": 1}}
    assert {u["model"] for u in repo.llm_usage()} == {"real-model"}
    assert repo.get_report(last_week()) is None
    assert runner.invoke(cli, ["seed", "--clear"]).exit_code == 0  # 지울 것이 없어도 안전하다


def test_demo_does_not_overwrite_a_real_report(ops):
    week = last_week()
    repo.save_report(WeeklyReport(week=week, tldr="실제 리포트", top_events=[], brand_summaries=[], insights=[],
                                  actions=[]), sent=True)
    assert "리포트 0건" in runner.invoke(cli, ["seed", "--demo"]).output
    assert repo.get_report(week).tldr == "실제 리포트"
    runner.invoke(cli, ["seed", "--clear"])
    assert repo.get_report(week).tldr == "실제 리포트"


def test_report_refuses_a_week_that_contains_samples(ops, monkeypatch):
    def no_build(week):
        raise AssertionError("샘플이 섞인 주차로 리포트를 만들려 함")

    monkeypatch.setattr("app.analysis.report_builder.build", no_build)
    runner.invoke(cli, ["seed", "--demo"])
    y, w, _ = datetime.now(KST).isocalendar()
    this_week = f"{y}-W{w:02d}"  # 오늘 샘플이 들어 있는 주 (지난주에는 샘플 리포트 초안이 있다)

    result = runner.invoke(cli, ["report", "--week", this_week])
    assert result.exit_code == 1 and "seed --clear" in result.output
    assert len(ops) == 1 and "샘플" in ops[0]
    assert runner.invoke(cli, ["report", "--week", this_week, "--dry-run"]).exit_code == 1 and len(ops) == 1
