"""CLI 진입점. 담당: D

  python -m app init-db
  python -m app collect --source site --brand spao     # A
  python -m app collect --source news                  # B (브랜드 생략 시 전체)
  python -m app classify                               # C (매일)
  python -m app report --week 2026-W47 [--dry-run]     # C + D (매주)
  python -m app cleanup                                # D (매일, 8주 지난 스냅샷 삭제)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from enum import Enum

import typer

from app.core.models import Brand, SourceType

cli = typer.Typer(help="경쟁사 모니터링 Agent")
WEEK_FORMAT = re.compile(r"\d{4}-W\d{2}")


NEWS_STALE_DAYS = 3  # 뉴스는 하루 신규 0건이 정상일 수 있어, 이 기간 내내 0건일 때만 경고


class Source(str, Enum):
    SITE = "site"
    NEWS = "news"
    SNS = "sns"


SOURCE_TYPES = {
    Source.SITE: [SourceType.SITE_PRODUCT, SourceType.SITE_PAGE],
    Source.NEWS: [SourceType.NEWS],
    Source.SNS: [SourceType.SNS_POST],
}


@cli.command("init-db")
def init_db() -> None:
    from app.infra.storage.db import init_db as _init

    _init()
    typer.echo("DB 초기화 완료")


@cli.command()
def collect(source: Source = typer.Option(..., help="site | news | sns"),
            brand: Brand | None = typer.Option(None)) -> None:
    from app.infra.notify.slack import send_ops_alert
    from app.infra.storage import repo

    brands = [brand] if brand else list(Brand)
    failed = 0
    for b in brands:
        before = repo.snapshot_counts(b, SOURCE_TYPES[source])
        try:
            if source is Source.SITE:
                from app.site_monitor.pipeline import run
                events = run(b)
            elif source is Source.NEWS:
                from app.media_monitor.pipeline import run_news
                events = run_news(b)
            else:
                from app.media_monitor.pipeline import run_sns
                events = run_sns(b)
            typer.echo(f"[{source.value}/{b.value}] 이벤트 {len(events)}건")
            if warning := _empty_collection(source, b, before):
                _alert(send_ops_alert, f"수집 0건 {source.value}/{b.value}: {warning}")
                typer.echo(f"[{source.value}/{b.value}] 경고: {warning}", err=True)
        except NotImplementedError:
            typer.echo(f"[{source.value}/{b.value}] 아직 구현되지 않음 (TODO)")
        except Exception as e:  # 한 브랜드 실패가 전체를 멈추지 않게
            failed += 1
            _alert(send_ops_alert, f"수집 실패 {source.value}/{b.value}: {e!r}")
            typer.echo(f"[{source.value}/{b.value}] 실패: {e!r}", err=True)
    if failed:  # 나머지 브랜드는 끝까지 돌리되, 스케줄 실행에서 실패가 보이도록
        raise typer.Exit(1)


@cli.command()
def classify() -> None:
    from app.analysis.classify import classify as _classify
    from app.infra.notify.slack import send_ops_alert
    from app.infra.storage import repo

    events = repo.unclassified_events()
    if not events:
        typer.echo("분류할 이벤트 없음")
        return
    try:
        repo.update_events(_classify(events))
    except Exception as e:
        _alert(send_ops_alert, f"분류 실패 ({len(events)}건): {e!r}")
        raise
    typer.echo(f"분류 완료 {len(events)}건")


@cli.command()
def report(week: str = typer.Option(None, help="예: 2026-W47 (기본: 지난주)"),
           dry_run: bool = typer.Option(False, help="발송하지 않고 HTML만 저장")) -> None:
    from app.analysis.report_builder import build
    from app.core import config
    from app.infra.notify import email, render, slack
    from app.infra.storage import repo

    if week is None:
        from app.infra import KST

        y, w, _ = (datetime.now(KST).date() - timedelta(days=7)).isocalendar()
        week = f"{y}-W{w:02d}"
    elif not WEEK_FORMAT.fullmatch(week):
        raise typer.BadParameter("week 는 2026-W47 형식", param_hint="--week")

    if not any(e.category for e in repo.events_of_week(week)):
        # 분류된 이벤트가 없으면 LLM이 근거 없이 리포트를 지어내므로 만들지 않는다
        msg = f"{week} 리포트 생략: 분류된 이벤트 0건 (수집·분류 실행 여부 확인)"
        if not dry_run:
            _alert(slack.send_ops_alert, msg)
        typer.echo(msg, err=True)
        raise typer.Exit(1)

    try:
        r = build(week)
    except Exception as e:
        if not dry_run:
            _alert(slack.send_ops_alert, f"{week} 리포트 생성 실패: {e!r}")
        raise
    html = render.to_html(r)
    out = config.ROOT / "data" / f"report-{week}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    out.with_suffix(".md").write_text(render.to_markdown(r), encoding="utf-8")
    typer.echo(f"리포트 생성: {out}")
    if dry_run:
        repo.save_report(r, sent=False)
        return

    # 채널 하나가 실패해도 나머지 채널은 발송한다
    channels = {
        "slack": lambda: slack.send_blocks(render.to_slack_blocks(r), fallback_text=r.tldr),
        "email": lambda: email.send_html(f"[경쟁사 주간 리포트] {week}", html),
    }
    errors = {}
    for name, send in channels.items():
        try:
            send()
        except Exception as e:
            errors[name] = e
            typer.echo(f"{name} 발송 실패: {e!r}", err=True)
    repo.save_report(r, sent=len(errors) < len(channels))
    if errors:
        _alert(slack.send_ops_alert, f"{week} 리포트 발송 실패: " + ", ".join(f"{k}: {v!r}" for k, v in errors.items()))
        raise typer.Exit(1)


@cli.command()
def cleanup(keep_weeks: int = typer.Option(8, min=1, help="스냅샷 보관 주 수")) -> None:
    from app.infra.storage import repo

    typer.echo(f"스냅샷 {repo.purge_snapshots(keep_weeks)}건 삭제 ({keep_weeks}주 초과분)")


def _empty_collection(source: Source, brand: Brand, before: dict[str, int]) -> str | None:
    """수집기가 예외 없이 빈 결과만 돌려준 경우(셀렉터 깨짐, 키 만료 등)를 찾아 사유를 돌려준다."""
    from app.infra import KST
    from app.infra.storage import repo

    after = repo.snapshot_counts(brand, SOURCE_TYPES[source])
    saved = {src: n - before.get(src, 0) for src, n in after.items()}
    if source is Source.NEWS:  # 신규 기사만 저장하므로 0건이 곧 장애는 아니다
        if sum(saved.values()) > 0:
            return None
        latest = repo.latest_collected_at(brand, SourceType.NEWS)
        stale_before = datetime.now(KST).replace(tzinfo=None) - timedelta(days=NEWS_STALE_DAYS)
        if latest is None or latest.replace(tzinfo=None) < stale_before:
            return f"최근 {NEWS_STALE_DAYS}일간 신규 기사 없음"
        return None
    if sum(saved.values()) == 0:
        return "저장된 항목 없음"
    empty = sorted(src for src, n in saved.items() if n == 0)
    return f"{', '.join(empty)} 에서 저장된 항목 없음" if empty else None


def _alert(send, message: str) -> None:
    """운영 경고 자체가 실패해도 원래 오류 처리를 가리지 않게 한다."""
    try:
        send(message)
    except Exception as e:
        typer.echo(f"운영 경고 발송 실패: {e!r}", err=True)


if __name__ == "__main__":
    cli()
