"""CLI 진입점. 담당: D

  python -m app init-db
  python -m app collect --source site --brand spao     # A
  python -m app collect --source news                  # B (브랜드 생략 시 전체)
  python -m app classify                               # C (매일) + 중요도 5 긴급 알림
  python -m app report --week 2026-W47 [--dry-run]     # C + D (매주)
  python -m app prune [--weeks 8]                      # 오래된 스냅샷 정리
  python -m app dashboard                              # 정적 대시보드 HTML 생성
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import typer

from app.core.models import Brand, SourceType

cli = typer.Typer(help="경쟁사 모니터링 Agent")
KST = ZoneInfo("Asia/Seoul")
SOURCES = ("site", "news", "sns")
# "파싱 0건" 점검 대상. news 는 새 기사만 저장하므로 0건이 정상일 수 있어 제외한다.
ZERO_CHECK: dict[str, list[SourceType]] = {
    "site": [SourceType.SITE_PRODUCT, SourceType.SITE_PAGE],
    "sns": [SourceType.SNS_POST],
}
WEEK_RE = re.compile(r"(\d{4})-W(\d{2})")


def last_week(now: datetime | None = None) -> str:
    """한국 시간 기준 지난주의 ISO 주차. (GitHub Actions 러너는 UTC라 date.today()를 쓰면 한 주 밀린다)"""
    day = (now or datetime.now(KST)).astimezone(KST).date() - timedelta(days=7)
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def _week_monday(week: str) -> date:
    """'2026-W47' → 그 주 월요일. 형식이 틀리거나 없는 주차면 BadParameter."""
    m = WEEK_RE.fullmatch(week)
    try:
        if not m:
            raise ValueError(week)
        return date.fromisocalendar(int(m[1]), int(m[2]), 1)
    except ValueError:
        raise typer.BadParameter(f"week 는 YYYY-Www 형식이어야 합니다 (예: 2026-W47), 입력값: {week!r}") from None


def prev_week(week: str) -> str:
    """주어진 ISO 주차의 바로 전 주차. (연초에는 전년도 W52/W53)"""
    y, w, _ = (_week_monday(week) - timedelta(days=7)).isocalendar()
    return f"{y}-W{w:02d}"


@cli.command("init-db")
def init_db() -> None:
    from app.infra.storage.db import init_db as _init

    _init()
    typer.echo("DB 초기화 완료")


@cli.command()
def collect(source: str = typer.Option(..., help="site | news | sns"),
            brand: Brand | None = typer.Option(None)) -> None:
    from app.infra.notify import slack
    from app.infra.storage import repo

    if source not in SOURCES:
        raise typer.BadParameter("source 는 site | news | sns")
    brands = [brand] if brand else list(Brand)
    failed: list[str] = []
    for b in brands:
        since = datetime.now()  # noqa: DTZ005 — collected_at 과 같은 기준(naive 로컬 시각)
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
            continue
        except Exception as e:  # 한 브랜드 실패가 전체를 멈추지 않게
            failed.append(b.value)
            slack.send_ops_alert(f"수집 실패 {source}/{b.value}: {e!r}")
            typer.echo(f"[{source}/{b.value}] 실패: {e!r}", err=True)
            continue
        if source not in ZERO_CHECK:
            continue
        try:  # 파이프라인은 이벤트만 돌려주므로, 이번 실행에서 저장된 항목 수는 DB에서 센다
            n_items = repo.count_items_since(b, ZERO_CHECK[source], since=since)
        except Exception as e:
            failed.append(b.value)
            slack.send_ops_alert(f"수집 건수 확인 실패 {source}/{b.value}: {e!r}")
            typer.echo(f"[{source}/{b.value}] 수집 건수 확인 실패: {e!r}", err=True)
            continue
        if n_items == 0:
            slack.send_ops_alert(f"파싱 0건 {source}/{b.value}: 수집된 항목이 없습니다 (셀렉터·API 변경 의심)")
            typer.echo(f"[{source}/{b.value}] 경고: 수집 0건", err=True)
    if failed:  # 모든 브랜드를 시도한 뒤에 실패로 끝낸다 (Actions 실행이 빨갛게 보이도록)
        typer.echo(f"[{source}] 실패한 브랜드: {', '.join(failed)}", err=True)
        raise typer.Exit(1)


@cli.command()
def classify() -> None:
    from app.analysis.classify import classify as _classify
    from app.infra.notify import slack
    from app.infra.storage import repo

    events = repo.unclassified_events()
    if not events:
        typer.echo("분류할 이벤트 없음")
        return
    try:
        classified = _classify(events)
    except Exception as e:
        slack.send_ops_alert(f"LLM 분류 실패 ({len(events)}건 대기): {e!r}")
        typer.echo(f"분류 실패: {e!r}", err=True)
        raise typer.Exit(1) from None
    repo.update_events(classified)
    typer.echo(f"분류 완료 {len(events)}건")

    urgent = [e for e in classified if e.importance == 5]
    if not urgent:
        return
    try:
        sent = slack.send_urgent(urgent)
    except Exception as e:  # 분류 결과는 이미 저장됨 — 알림만 실패
        slack.send_ops_alert(f"긴급 알림 발송 실패 ({len(urgent)}건): {e!r}")
        typer.echo(f"긴급 알림 발송 실패: {e!r}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"긴급 알림 {sent}건 발송")


@cli.command()
def report(week: str = typer.Option(None, help="예: 2026-W47 (기본: 지난주)"),
           dry_run: bool = typer.Option(False, help="발송하지 않고 HTML·Markdown만 저장")) -> None:
    from app.analysis.report_builder import build
    from app.core.config import ROOT, env
    from app.infra.notify import email, render, slack
    from app.infra.storage import repo

    if week is None:
        week = last_week()
    before = prev_week(week)  # 형식 검증도 겸한다
    try:
        r = build(week)
    except Exception as e:
        slack.send_ops_alert(f"주간 리포트 생성 실패 {week}: {e!r}")
        typer.echo(f"리포트 생성 실패: {e!r}", err=True)
        raise typer.Exit(1) from None
    prev = repo.get_report(before)
    prev_stats = prev.stats if prev else None  # 부록 수치표의 전주 대비
    html = render.to_html(r, prev_stats=prev_stats)
    markdown = render.to_markdown(r, prev_stats=prev_stats)
    out = ROOT / "data" / f"report-{week}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    out.with_suffix(".md").write_text(markdown, encoding="utf-8")
    typer.echo(f"리포트 생성: {out}")
    if dry_run:
        repo.save_report(r, sent=False)
        return

    channels = {
        "slack": lambda: slack.send_report(r, full_report_url=env("REPORT_URL") or None),
        "email": lambda: email.send_html(f"[경쟁사 주간 리포트] {week}", html, text=markdown),
    }
    failed: list[str] = []
    for name, send in channels.items():  # 한 채널 실패가 다른 채널 발송을 막지 않게
        try:
            send()
            typer.echo(f"[{name}] 발송 완료")
        except Exception as e:
            failed.append(name)
            slack.send_ops_alert(f"주간 리포트 발송 실패 {week}/{name}: {e!r}")
            typer.echo(f"[{name}] 발송 실패: {e!r}", err=True)
    repo.save_report(r, sent=len(failed) < len(channels))  # 한 채널이라도 나갔으면 발송으로 기록
    if failed:
        raise typer.Exit(1)


@cli.command()
def prune(weeks: int = typer.Option(8, min=1, help="보관 기간(주). 이보다 오래된 스냅샷을 지운다")) -> None:
    from app.infra.storage import repo

    typer.echo(f"스냅샷 {repo.prune_snapshots(weeks=weeks)}건 삭제 ({weeks}주 초과분)")


@cli.command()
def dashboard() -> None:
    from app.infra import dashboard as _dashboard

    typer.echo(f"대시보드 생성: {_dashboard.build()}")


if __name__ == "__main__":
    cli()
