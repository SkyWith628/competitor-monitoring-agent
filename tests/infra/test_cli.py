"""[D] CLI 테스트."""
from datetime import UTC, datetime

from typer.testing import CliRunner

from app.__main__ import cli, last_week
from app.core.models import Brand, WeeklyReport
from app.infra.notify import slack

runner = CliRunner()


def test_last_week_uses_korean_time():
    # weekly.yml 실행 시각: 일요일 23:00 UTC = 월요일 08:00 KST(W48) → 지난주는 W47
    assert last_week(datetime(2026, 11, 22, 23, 0, tzinfo=UTC)) == "2026-W47"


def test_collect_rejects_unknown_source_without_alert(monkeypatch):
    alerts = []
    monkeypatch.setattr(slack, "send_ops_alert", alerts.append)
    result = runner.invoke(cli, ["collect", "--source", "blog"])
    assert result.exit_code == 2 and alerts == []


def test_collect_continues_after_one_brand_fails(monkeypatch):
    from app.site_monitor import pipeline

    alerts = []

    def run(brand):
        if brand == Brand.SPAO:
            raise RuntimeError("파싱 실패")
        return []

    monkeypatch.setattr(pipeline, "run", run)
    monkeypatch.setattr(slack, "send_ops_alert", alerts.append)
    result = runner.invoke(cli, ["collect", "--source", "site"])
    assert result.exit_code == 0
    assert len(alerts) == 1 and "site/spao" in alerts[0]
    assert "[site/uniqlo] 이벤트 0건" in result.output


def test_report_dry_run_writes_html_and_sends_nothing(tmp_db, tmp_path, monkeypatch):
    from app.analysis import report_builder
    from app.core import config

    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(report_builder, "build", lambda week: WeeklyReport(
        week=week, tldr="요약", top_events=[], brand_summaries=[], insights=[], actions=[]))
    monkeypatch.setattr(slack, "send_blocks", lambda *a, **k: (_ for _ in ()).throw(AssertionError("발송됨")))
    result = runner.invoke(cli, ["report", "--week", "2026-W47", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "요약" in (tmp_path / "data" / "report-2026-W47.html").read_text(encoding="utf-8")
