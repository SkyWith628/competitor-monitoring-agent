"""[D] 통합 테스트 — 실제 repo·render·slack·email·dashboard 를 CLI로 이어서 돌린다.

가짜로 바꾸는 것은 바깥 경계뿐: A의 수집 파이프라인, C의 LLM 호출, HTTP(httpx.post), SMTP.
"""
from datetime import datetime, timedelta

import httpx
import pytest
from typer.testing import CliRunner

from app.__main__ import KST, cli, last_week
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

    def starttls(self):
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
                          detected_at=datetime.now(KST).replace(tzinfo=None) - timedelta(days=7))]
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

    # 수집: 스파오는 저장됨, 나머지 세 브랜드는 0건 경고
    result = runner.invoke(cli, ["collect", "--source", "site"])
    assert result.exit_code == 0, result.output
    zero_alerts = [body["text"] for url, body in posts if url.endswith("/ops")]
    assert len(zero_alerts) == 3 and all("파싱 0건" in t for t in zero_alerts)
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
    week = last_week()
    result = runner.invoke(cli, ["report"])
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
