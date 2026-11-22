"""CLI 진입점. 담당: D

  python -m app init-db
  python -m app collect --source site --brand spao     # A
  python -m app collect --source news                  # B (브랜드 생략 시 전체)
  python -m app classify                               # C (매일)
  python -m app report --week 2026-W47 [--dry-run]     # C + D (매주)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import typer

from app.core.models import Brand

cli = typer.Typer(help="경쟁사 모니터링 Agent")
KST = ZoneInfo("Asia/Seoul")
SOURCES = ("site", "news", "sns")


def last_week(now: datetime | None = None) -> str:
    """한국 시간 기준 지난주의 ISO 주차. (GitHub Actions 러너는 UTC라 date.today()를 쓰면 한 주 밀린다)"""
    day = (now or datetime.now(KST)).astimezone(KST).date() - timedelta(days=7)
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


@cli.command("init-db")
def init_db() -> None:
    from app.infra.storage.db import init_db as _init

    _init()
    typer.echo("DB 초기화 완료")


@cli.command()
def collect(source: str = typer.Option(..., help="site | news | sns"),
            brand: Brand | None = typer.Option(None)) -> None:
    from app.infra.notify.slack import send_ops_alert

    if source not in SOURCES:
        raise typer.BadParameter("source 는 site | news | sns")
    brands = [brand] if brand else list(Brand)
    for b in brands:
        try:
            if source == "site":
                from app.site_monitor.pipeline import run
                events = run(b)
            elif source == "news":
                from app.media_monitor.pipeline import run_news
                events = run_news(b)
            else:
                from app.media_monitor.pipeline import run_sns
                events = run_sns(b)
            typer.echo(f"[{source}/{b.value}] 이벤트 {len(events)}건")
        except NotImplementedError:
            typer.echo(f"[{source}/{b.value}] 아직 구현되지 않음 (TODO)")
        except Exception as e:  # 한 브랜드 실패가 전체를 멈추지 않게
            send_ops_alert(f"수집 실패 {source}/{b.value}: {e!r}")
            typer.echo(f"[{source}/{b.value}] 실패: {e!r}", err=True)


@cli.command()
def classify() -> None:
    from app.analysis.classify import classify as _classify
    from app.infra.storage import repo

    events = repo.unclassified_events()
    if not events:
        typer.echo("분류할 이벤트 없음")
        return
    repo.update_events(_classify(events))
    typer.echo(f"분류 완료 {len(events)}건")


@cli.command()
def report(week: str = typer.Option(None, help="예: 2026-W47 (기본: 지난주)"),
           dry_run: bool = typer.Option(False, help="발송하지 않고 HTML만 저장")) -> None:
    from app.analysis.report_builder import build
    from app.core.config import ROOT
    from app.infra.notify import email, render, slack
    from app.infra.storage import repo

    if week is None:
        week = last_week()
    r = build(week)
    html = render.to_html(r)
    out = ROOT / "data" / f"report-{week}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    if not dry_run:
        slack.send_blocks(render.to_slack_blocks(r), fallback_text=r.tldr)
        email.send_html(f"[경쟁사 주간 리포트] {week}", html)
    repo.save_report(r, sent=not dry_run)
    typer.echo(f"리포트 생성: {out}")


if __name__ == "__main__":
    cli()
