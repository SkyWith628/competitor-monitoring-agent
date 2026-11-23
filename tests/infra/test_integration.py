"""[D] 통합 테스트 — 실제 repo·render·slack·email·dashboard 를 CLI로 이어서 돌린다.

가짜로 바꾸는 것은 바깥 경계뿐: A의 수집 파이프라인, C의 LLM 호출, HTTP(httpx.post), SMTP.
"""
from datetime import datetime

import httpx
import pytest
from typer.testing import CliRunner

from app.__main__ import KST, cli
from app.core import config
from app.core.models import (
    Brand,
    BrandSummary,
    Category,
    ChangeEvent,
    ChangeType,
    RawItem,
    SourceType,
    WeeklyReport,
)
from app.infra.storage import db, repo

runner = CliRunner()


class _Response:
    def raise_for_status(self) -> None:
        pass


class _SMTP:
    sent: list = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        _SMTP.sent.append(msg)


@pytest.fixture
def world(tmp_db, tmp_path, monkeypatch):
    """빈 DB + 임시 출력 폴더 + 슬랙·메일 호출 기록."""
    posts: list[tuple[str, dict]] = []
    _SMTP.sent = []
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(httpx, "post", lambda url, json, **kw: posts.append((url, json)) or _Response())
    monkeypatch.setattr("smtplib.SMTP", _SMTP)
    for key, value in {"SLACK_WEBHOOK_REPORT": "https://hooks.example/report",
                       "SLACK_WEBHOOK_OPS": "https://hooks.example/ops", "SMTP_USER": "bot@example.com",
                       "SMTP_PASSWORD": "pw", "MAIL_TO": "team@example.com"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("REPORT_URL", raising=False)
    return posts


def _site_pipeline(brand: Brand) -> list[ChangeEvent]:
    """스파오만 상품 1개를 수집하고, 나머지 브랜드는 0건."""
    if brand != Brand.SPAO:
        return []
    item = RawItem(brand=brand, source_type=SourceType.SITE_PRODUCT, source="spao.com", external_id="p1",
                   url="https://spao.com/p1", title="히트텍 <신상>", price=29900)
    events = [ChangeEvent(brand=brand, change_type=ChangeType.PRICE_DOWN, title="히트텍 <신상> 할인",
                          url=item.url, detail={"before": 39900, "after": 29900},
                          detected_at=datetime.now(KST).replace(tzinfo=None))]
    repo.save_items([item])
    repo.save_events(events)
    return events


def _classify(events: list[ChangeEvent]) -> list[ChangeEvent]:
    for e in events:
        e.category, e.importance, e.llm_note = Category.PRICING, 5, "시즌 초 25% 인하"
    return events


def _build(week: str) -> WeeklyReport:
    events = repo.events_of_week(week)
    return WeeklyReport(
        week=week, tldr="스파오가 가격을 내렸다", top_events=events,
        brand_summaries=[BrandSummary(brand=Brand.SPAO, highlights=["히트텍 인하 [#1]"], event_ids=[1])],
        insights=["가격 경쟁 심화"], actions=["대응 프로모션 검토"],
        stats={"spao": {"price_down": len(events)}})


def test_daily_then_weekly_flow(world, tmp_path, monkeypatch):
    posts = world
    monkeypatch.setattr("app.site_monitor.pipeline.run", _site_pipeline)
    monkeypatch.setattr("app.analysis.classify.classify", _classify)
    monkeypatch.setattr("app.analysis.report_builder.build", _build)

    # 수집: 스파오는 저장됨, 나머지 브랜드는 0건 경고
    result = runner.invoke(cli, ["collect", "--source", "site"])
    assert result.exit_code == 0, result.output
    zero_alerts = [body["text"] for url, body in posts if url.endswith("/ops")]
    assert len(zero_alerts) == len(Brand) - 1 and all("파싱 0건" in t for t in zero_alerts)
    assert not any("spao" in t for t in zero_alerts)
    assert repo.item_counts() == {"spao": {"site_product": 1}}

    # 분류: 중요도 5 → 리포트 채널로 긴급 알림
    posts.clear()
    result = runner.invoke(cli, ["classify"])
    assert result.exit_code == 0, result.output
    assert repo.unclassified_events() == []
    assert [url for url, _ in posts] == ["https://hooks.example/report"]
    assert "blocks" in posts[0][1]

    # 주간 리포트: 슬랙(본문 + 브랜드 후속) + 메일, 파일 저장, 발송 기록
    posts.clear()
    y, w, _ = datetime.now(KST).isocalendar()
    week = f"{y}-W{w:02d}"  # 방금 감지한 이벤트가 들어 있는 이번 주
    result = runner.invoke(cli, ["report", "--week", week])
    assert result.exit_code == 0, result.output
    assert len(posts) == 2 and all(url.endswith("/report") for url, _ in posts)
    assert len(_SMTP.sent) == 1 and _SMTP.sent[0].is_multipart()
    html = (tmp_path / "data" / f"report-{week}.html").read_text(encoding="utf-8")
    assert "히트텍 &lt;신상&gt; 할인" in html and "<신상>" not in html
    assert "스파오가 가격을 내렸다" in (tmp_path / "data" / f"report-{week}.md").read_text(encoding="utf-8")
    with db.connect() as conn:
        assert conn.execute("SELECT sent_at FROM reports").fetchone()[0] is not None
    assert repo.get_report(week).stats == {"spao": {"price_down": 1}}

    # 대시보드·정리
    result = runner.invoke(cli, ["dashboard"])
    assert result.exit_code == 0, result.output
    page = (tmp_path / "data" / "dashboard.html").read_text(encoding="utf-8")
    assert "스파오가 가격을 내렸다" in page and "히트텍 &lt;신상&gt; 할인" in page
    assert "운영 상태" in page and "spao.com" in page and "운영 상태 정보 없음" not in page  # 실제 DB 값으로 채워짐
    result = runner.invoke(cli, ["status", "--strict"])
    assert result.exit_code == 0, result.output
    assert "정상  스파오 · spao.com" in result.output and f"{week}  발송 완료" in result.output
    result = runner.invoke(cli, ["prune"])
    assert result.exit_code == 0 and "0건 삭제" in result.output


def test_report_survives_one_channel_failing(world, monkeypatch):
    monkeypatch.setattr("app.analysis.report_builder.build", _build)
    monkeypatch.delenv("MAIL_TO")
    result = runner.invoke(cli, ["report", "--week", "2026-W47"])
    assert result.exit_code == 1  # 메일 실패를 알리되
    assert any(url.endswith("/report") for url, _ in world)  # 슬랙은 나갔고
    assert any("발송 실패" in body["text"] for url, body in world if url.endswith("/ops"))
    with db.connect() as conn:  # 발송된 것으로 기록된다
        assert conn.execute("SELECT sent_at FROM reports").fetchone()[0] is not None
    # 한 채널만 나간 주차는 status 와 대시보드 양쪽에서 "일부 발송"으로 보인다
    assert "2026-W47  일부 발송 (미발송: email)" in runner.invoke(cli, ["status"]).output
    assert runner.invoke(cli, ["dashboard"]).exit_code == 0
    assert "일부 발송" in (config.ROOT / "data" / "dashboard.html").read_text(encoding="utf-8")


def test_report_rerun_does_not_resend_or_rewrite(world, monkeypatch):
    """메일만 실패 → 고친 뒤 재실행: 슬랙은 다시 나가지 않고, 메일은 처음 나간 내용 그대로."""
    builds = []

    def build(week):
        builds.append(week)
        report = _build(week)
        report.tldr = f"{len(builds)}번째로 만든 요약"
        return report

    monkeypatch.setattr("app.analysis.report_builder.build", build)
    monkeypatch.delenv("MAIL_TO")
    assert runner.invoke(cli, ["report", "--week", "2026-W47"]).exit_code == 1
    slack_posts = [body for url, body in world if url.endswith("/report")]

    monkeypatch.setenv("MAIL_TO", "team@example.com")
    result = runner.invoke(cli, ["report", "--week", "2026-W47"])
    assert result.exit_code == 0, result.output
    assert [body for url, body in world if url.endswith("/report")] == slack_posts
    assert builds == ["2026-W47"] and len(_SMTP.sent) == 1
    assert "1번째로 만든 요약" in _SMTP.sent[0].get_body(("plain",)).get_content()
    assert repo.report_channels_sent("2026-W47") >= {"slack", "email"}

    # 다 나간 뒤에는 다시 실행해도, dry-run 을 해도 발송 기록이 그대로다
    assert "이미 발송됨" in runner.invoke(cli, ["report", "--week", "2026-W47"]).output
    assert runner.invoke(cli, ["report", "--week", "2026-W47", "--dry-run"]).exit_code == 0
    assert repo.get_report("2026-W47").tldr == "1번째로 만든 요약"
    assert len(_SMTP.sent) == 1


def test_urgent_alert_is_retried_after_slack_outage(world, monkeypatch):
    monkeypatch.setattr("app.site_monitor.pipeline.run", _site_pipeline)
    monkeypatch.setattr("app.analysis.classify.classify", _classify)
    assert runner.invoke(cli, ["collect", "--source", "site", "--brand", "spao"]).exit_code == 0

    def down(url, json, **kw):
        world.append((url, json))
        return httpx.Response(503, request=httpx.Request("POST", url))

    world.clear()
    monkeypatch.setattr(httpx, "post", down)
    result = runner.invoke(cli, ["classify"])
    assert result.exit_code == 1  # 슬랙 장애를 숨기지 않는다
    assert repo.unclassified_events() == [] and len(repo.pending_urgent()) == 1
    assert "hooks.example" not in result.output  # 웹훅 주소는 로그에 남기지 않는다

    world.clear()
    monkeypatch.setattr(httpx, "post", lambda url, json, **kw: world.append((url, json)) or _Response())
    result = runner.invoke(cli, ["classify"])  # 슬랙 복구 후: 새로 분류할 것은 없지만 알림은 다시 나간다
    assert result.exit_code == 0, result.output
    assert [url for url, _ in world] == ["https://hooks.example/report"]
    assert repo.pending_urgent() == []
    world.clear()
    assert runner.invoke(cli, ["classify"]).exit_code == 0 and world == []  # 한 번만


def test_slack_followup_failure_does_not_resend_main_message(world, monkeypatch):
    """본 메시지는 나갔는데 브랜드별 후속 메시지에서 실패 → 재실행 때 후속 메시지만 나간다."""
    monkeypatch.setattr("app.analysis.report_builder.build", _build)
    outage = {"on": True}
    report_posts_tried = []

    def flaky(url, json, **kw):
        if url.endswith("/report"):
            report_posts_tried.append(json)
            # 장애 중에는 본 메시지(첫 번째)만 나가고, 그 뒤 메시지는 재시도해도 계속 500
            if outage["on"] and len(report_posts_tried) >= 2:
                return httpx.Response(500, request=httpx.Request("POST", url))
        world.append((url, json))
        return _Response()

    monkeypatch.setattr(httpx, "post", flaky)
    assert runner.invoke(cli, ["report", "--week", "2026-W47"]).exit_code == 1
    first = [body for url, body in world if url.endswith("/report")]
    assert len(first) == 1 and len(_SMTP.sent) == 1  # 본 메시지와 메일은 나갔다

    outage["on"] = False  # 슬랙 복구
    result = runner.invoke(cli, ["report", "--week", "2026-W47"])
    assert result.exit_code == 0, result.output
    report_posts = [body for url, body in world if url.endswith("/report")]
    assert len(report_posts) == 2 and report_posts[0] == first[0]  # 후속 메시지 1건만 추가
    assert "브랜드별 요약" in report_posts[1]["text"] and len(_SMTP.sent) == 1


def test_workflows_share_one_concurrency_group_and_pin_dependencies():
    """weekly 는 daily(수집·분류)가 끝난 뒤에 돌아야 하고, 설치는 검증된 버전으로 한다."""
    from pathlib import Path

    import yaml

    root = Path(__file__).parents[2]
    daily, weekly = (yaml.safe_load((root / ".github" / "workflows" / f).read_text(encoding="utf-8"))
                     for f in ("daily.yml", "weekly.yml"))
    assert daily["concurrency"]["group"] == weekly["concurrency"]["group"]
    assert not daily["concurrency"]["cancel-in-progress"] and not weekly["concurrency"]["cancel-in-progress"]
    for workflow in (root / ".github" / "workflows").glob("*.yml"):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            if "pip install" in line:
                assert "-c constraints.txt" in line, f"{workflow.name}: {line.strip()}"
    pins = [line for line in (root / "constraints.txt").read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")]
    assert pins and all("==" in line for line in pins)


def test_weekly_workflow_publishes_no_artifacts():
    """공개 저장소라서 리포트·대시보드를 Actions 산출물로 올리면 누구나 내려받을 수 있다."""
    from pathlib import Path

    for workflow in (Path(__file__).parents[2] / ".github" / "workflows").glob("*.yml"):
        assert "upload-artifact" not in workflow.read_text(encoding="utf-8"), workflow.name


def test_rehearsal_seed_classify_report_clear(world, monkeypatch):
    """발송 리허설: 샘플을 넣어 실제 발송 경로를 돌리고, 지운 뒤에는 흔적이 남지 않는다."""
    from app.infra.sample_data import REHEARSAL_WEEK

    posts = world
    monkeypatch.setattr("app.analysis.classify.classify", _classify)  # 전부 중요도 5로 분류
    monkeypatch.setattr("app.analysis.report_builder.build", _build)
    real = ChangeEvent(brand=Brand.SPAO, change_type=ChangeType.NEWS, title="실제 이벤트", url="https://a.com/1",
                       detected_at=datetime.now(KST).replace(tzinfo=None))
    repo.save_events([real])

    assert "14건 저장" in runner.invoke(cli, ["seed"]).output
    assert "14건 저장" in runner.invoke(cli, ["seed"]).output  # 다시 넣어도 중복되지 않는다
    assert len(repo.events_of_week(REHEARSAL_WEEK)) == 14

    assert runner.invoke(cli, ["classify"]).exit_code == 0
    urgent = [body for url, body in posts if url.endswith("/report")]
    assert len(urgent) == 1  # 실제 이벤트만 긴급 알림 — 2020년 샘플은 "최근"이 아니라 나가지 않는다

    posts.clear()
    result = runner.invoke(cli, ["report", "--week", REHEARSAL_WEEK, "--force"])
    assert result.exit_code == 0, result.output
    assert posts and len(_SMTP.sent) == 1
    assert repo.report_channels_sent(REHEARSAL_WEEK) >= {"slack", "email"}

    assert "이벤트 14건" in runner.invoke(cli, ["seed", "--clear"]).output
    assert repo.events_of_week(REHEARSAL_WEEK) == [] and repo.get_report(REHEARSAL_WEEK) is None
    assert repo.report_channels_sent(REHEARSAL_WEEK) == set()
    assert repo.last_report_tldr("2026-W47") is None  # 리허설 리포트가 '지난주 요약'으로 새지 않는다
    assert [e.title for e in repo.unclassified_events()] == []
    assert repo.recent_events(1)[0].title == "실제 이벤트"
