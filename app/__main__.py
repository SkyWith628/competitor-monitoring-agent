"""CLI 진입점. 담당: D

모든 명령은 `python -m app <명령>` 으로 실행한다. 자동 실행(.github/workflows)도 같은 명령을 쓴다.

  python -m app init-db
  python -m app collect --source site --brand spao     # A
  python -m app collect --source news                  # B (브랜드 생략 시 전체)
  python -m app classify                               # C (매일) + 중요도 5 긴급 알림
  python -m app report --week 2026-W47 [--dry-run]     # C + D (매주)
  python -m app prune [--weeks 8]                      # 오래된 스냅샷 정리
  python -m app dashboard                              # 정적 대시보드 HTML 생성
  python -m app status [--strict]                      # 수집·분류·발송 현황
  python -m app doctor [--send-test]                   # 설정 점검 (키·DB·웹훅·메일)

이 파일이 하는 일과 하지 않는 일
    - 하는 일: 각 역할의 코드를 순서대로 부르고, 실패를 운영 채널에 알리고, 종료 코드를 정한다.
    - 하지 않는 일: 수집·변화 감지(A·B), 분류·요약(C)의 내용. 그건 각 폴더의 pipeline / classify /
      report_builder 가 한다. 여기서는 그 함수들을 호출만 한다.

A·B·C가 알아둘 약속
    - 수집 파이프라인(run, run_news, run_sns)은 ChangeEvent 리스트를 돌려주고, 실패하면 예외를 던진다.
      예외는 여기서 잡아 운영 경고로 보내고 다음 브랜드로 넘어간다. 직접 잡아서 삼키지 말 것.
    - 아직 구현 전이면 NotImplementedError 를 던진다. 실패로 치지 않고 "아직 구현되지 않음"만 출력한다.
    - 종료 코드: 0 = 정상, 1 = 일부라도 실패(Actions 에서 빨갛게 보인다), 2 = 명령 인자가 잘못됨.

import 를 함수 안에서 하는 이유
    명령 하나를 실행할 때 다른 역할의 모듈까지 전부 불러오지 않기 위해서다. 예를 들어 수집용 패키지가
    설치되지 않은 환경에서도 `report` 는 돌아야 한다. 테스트에서 일부만 가짜로 바꾸기도 쉽다.
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
CHANNELS = ("slack", "email")  # 주간 리포트 발송 채널
STALE_HOURS = 36  # 하루 한 번 수집하므로, 이보다 오래 소식이 없으면 수집이 멈춘 것으로 본다
WEEK_RE = re.compile(r"(\d{4})-W(\d{2})")


def last_week(now: datetime | None = None) -> str:
    """한국 시간 기준 지난주의 ISO 주차 문자열. 예: 월요일 2026-11-23 에 부르면 "2026-W47".

    weekly 워크플로는 일요일 23:00 UTC(= 월요일 08:00 KST)에 돈다. 러너의 날짜(UTC)로 계산하면 아직
    일요일이라 "지난주"가 한 주 더 밀리므로, 반드시 한국 시간으로 날짜를 구한다.
    now 는 테스트에서 시각을 고정할 때만 넘긴다.
    """
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


@cli.callback()
def _setup(ctx: typer.Context) -> None:
    """모든 명령 실행 전에 한 번 도는 준비 단계.

    1. 시간대를 한국 시간으로 고정한다 (수집·감지 시각과 주차 경계의 기준).
    2. 운영 워크플로(REQUIRE_DATABASE_URL=1)인데 DATABASE_URL 이 비어 있으면 여기서 멈춘다.
       그대로 두면 러너의 임시 SQLite 에 써서, 실행이 끝날 때 데이터가 사라진다.
    """
    from app.infra.notify import slack
    from app.infra.storage import db
    from app.infra.timeutil import use_korean_time

    use_korean_time()
    if ctx.invoked_subcommand == "doctor":  # 설정 점검은 설정이 틀렸을 때도 끝까지 돌아야 한다
        return
    try:
        db.database_url()
    except RuntimeError as e:
        slack.send_ops_alert(f"실행 중단: {slack.describe_error(e)}")
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None


@cli.command("init-db")
def init_db() -> None:
    """테이블을 만든다. 첫 연결 때 자동으로도 만들어지므로 꼭 실행할 필요는 없다."""
    from app.infra.storage.db import init_db as _init

    _init()
    typer.echo("DB 초기화 완료")


@cli.command()
def collect(source: str = typer.Option(..., help="site | news | sns"),
            brand: Brand | None = typer.Option(None)) -> None:
    """한 종류의 소스를 수집한다. --brand 를 생략하면 4개 브랜드를 차례로 돈다.

    브랜드마다: 파이프라인 실행 → 성공하면 "정말 뭔가 수집됐는지" 0건 검사.
    한 브랜드가 실패해도 나머지는 계속하고, 실패가 하나라도 있었으면 마지막에 종료 코드 1.
    """
    from app.infra.notify import slack
    from app.infra.storage import repo

    if source not in SOURCES:
        raise typer.BadParameter("source 는 site | news | sns")
    brands = [brand] if brand else list(Brand)
    failed: list[str] = []
    for b in brands:
        # 0건 검사의 기준 시각. 이 시각 이후에 저장된 항목 = 이번 실행에서 수집된 항목.
        # 모델이 collected_at 을 datetime.now() 로 찍으므로 같은 방식(시간대 없는 시각)으로 잡는다.
        since = datetime.now()
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
            slack.send_ops_alert(f"수집 실패 {source}/{b.value}: {slack.describe_error(e)}")
            typer.echo(f"[{source}/{b.value}] 실패: {slack.describe_error(e)}", err=True)
            continue
        # --- 여기부터 0건 검사: 예외 없이 끝났어도 아무것도 못 가져왔다면 셀렉터·API가 바뀐 것이다 ---
        if source not in ZERO_CHECK:
            continue
        try:  # 파이프라인은 이벤트만 돌려주므로, 이번 실행에서 저장된 항목 수는 DB에서 센다
            n_items = repo.count_items_since(b, ZERO_CHECK[source], since=since)
        except Exception as e:
            failed.append(b.value)
            slack.send_ops_alert(f"수집 건수 확인 실패 {source}/{b.value}: {slack.describe_error(e)}")
            typer.echo(f"[{source}/{b.value}] 수집 건수 확인 실패: {slack.describe_error(e)}", err=True)
            continue
        if n_items == 0:
            slack.send_ops_alert(f"파싱 0건 {source}/{b.value}: 수집된 항목이 없습니다 (셀렉터·API 변경 의심)")
            typer.echo(f"[{source}/{b.value}] 경고: 수집 0건", err=True)
            continue
        # 합계는 0이 아니어도 소스 하나가 죽었을 수 있다 (예: 유튜브는 들어오는데 인스타그램만 0건)
        for name in repo.silent_sources(b, ZERO_CHECK[source], since=since):
            slack.send_ops_alert(f"파싱 0건 {source}/{b.value} ({name}): 전에는 수집되던 소스가 이번에는 0건입니다")
            typer.echo(f"[{source}/{b.value}] 경고: {name} 수집 0건", err=True)
    if failed:  # 모든 브랜드를 시도한 뒤에 실패로 끝낸다 (Actions 실행이 빨갛게 보이도록)
        typer.echo(f"[{source}] 실패한 브랜드: {', '.join(failed)}", err=True)
        raise typer.Exit(1)


@cli.command()
def classify() -> None:
    """미분류 이벤트를 LLM으로 분류하고, 중요도 5 이벤트는 긴급 알림으로 보낸다.

    순서: 미분류 조회 → C의 classify() → 결과 저장 → 긴급 알림.
    분류가 실패하면 아무것도 저장하지 않으므로 다음 실행에서 같은 이벤트를 다시 시도한다.
    긴급 알림은 "보냈다"는 기록(urgent_alerts)이 없는 것만 보내므로, 실패한 알림은 다음 실행에서
    다시 나가고 성공한 알림은 중복되지 않는다.
    """
    from app.analysis.classify import classify as _classify
    from app.core.config import env
    from app.infra.notify import slack
    from app.infra.storage import repo

    events = repo.unclassified_events()
    if events:
        try:
            classified = _classify(events)
        except Exception as e:
            slack.send_ops_alert(f"LLM 분류 실패 ({len(events)}건 대기): {slack.describe_error(e)}")
            typer.echo(f"분류 실패: {slack.describe_error(e)}", err=True)
            raise typer.Exit(1) from None
        repo.update_events(classified)
        typer.echo(f"분류 완료 {len(events)}건")
    else:
        typer.echo("분류할 이벤트 없음")

    # 긴급 알림: 방금 분류한 것뿐 아니라, 지난 실행에서 발송에 실패한 것도 다시 시도한다
    urgent = repo.pending_urgent()
    if not urgent:
        return
    if not env(slack.REPORT_WEBHOOK_ENV):
        typer.echo(f"긴급 알림 {len(urgent)}건 대기 중 — {slack.REPORT_WEBHOOK_ENV} 미설정으로 보내지 않음", err=True)
        return
    sent = slack.send_urgent(urgent)
    repo.mark_urgent_sent([e.id for e in sent if e.id is not None])
    typer.echo(f"긴급 알림 {len(sent)}건 발송")
    if len(sent) < len(urgent):
        missed = len(urgent) - len(sent)
        slack.send_ops_alert(f"긴급 알림 {missed}건 발송 실패 — 다음 classify 실행에서 다시 시도합니다")
        typer.echo(f"긴급 알림 {missed}건 발송 실패", err=True)
        raise typer.Exit(1)


@cli.command()
def report(week: str = typer.Option(None, help="예: 2026-W47 (기본: 지난주)"),
           dry_run: bool = typer.Option(False, help="발송하지 않고 HTML·Markdown만 저장"),
           force: bool = typer.Option(False, help="이미 발송한 주차도 새로 만들어 모든 채널에 다시 보낸다")) -> None:
    """주간 리포트를 만들어 파일로 저장하고 슬랙·메일로 보낸다.

    순서: C의 build() → HTML·Markdown 렌더 → data/report-<주차>.* 저장 → 채널별 발송 → 발송 기록.

    다시 실행해도 안전하다:
      - 전부 나간 주차      → 아무것도 하지 않는다 (다시 보내려면 --force)
      - 일부만 나간 주차    → 처음 나간 내용 그대로(LLM 재생성 없음) 못 나간 부분만 보낸다
      - 아무것도 안 나간 주차 → 새로 만들어 보낸다
    --dry-run 은 파일만 만들고 보내지 않으며, 이미 발송된 기록을 덮어쓰지 않는다.
    """
    from app.analysis.report_builder import build
    from app.core.config import ROOT, env
    from app.infra.notify import email, render, slack
    from app.infra.storage import repo

    if week is None:
        week = last_week()
    before = prev_week(week)  # 형식 검증도 겸한다

    # 재실행 시 중복 발송 방지: 이미 나간 것은 건너뛰고, 나간 리포트와 같은 내용으로 나머지만 보낸다.
    # 기록 단위: "slack"·"email"(채널 전체 완료), "slack#0"·"slack#1"…(슬랙 메시지 하나하나)
    sent = set() if force or dry_run else repo.report_channels_sent(week)
    stored = repo.get_report(week) if sent else None
    if stored is not None and sent >= set(CHANNELS):
        typer.echo(f"{week} 리포트는 이미 발송됨 ({', '.join(CHANNELS)}). 다시 보내려면 --force")
        return
    if stored is not None:
        r = stored
        typer.echo(f"{week} 리포트는 일부 발송됨 — 같은 내용으로 나머지만 발송")
    else:
        sent = set()
        from app.infra import sample_data

        if sample_data.has_samples_in_week(week):
            # 데모 샘플이 섞인 채로 만들면 가짜 이벤트가 실제 리포트에 들어가 팀에 발송된다
            message = f"주간 리포트 {week} 중단: 데모 샘플 데이터가 섞여 있습니다. `python -m app seed --clear` 후 다시 실행하세요"
            if not dry_run:
                slack.send_ops_alert(message)
            typer.echo(message, err=True)
            raise typer.Exit(1)
        try:
            r = build(week)
        except Exception as e:
            slack.send_ops_alert(f"주간 리포트 생성 실패 {week}: {slack.describe_error(e)}")
            typer.echo(f"리포트 생성 실패: {slack.describe_error(e)}", err=True)
            raise typer.Exit(1) from None
    prev = repo.get_report(before)
    prev_stats = prev.stats if prev else None  # 부록 수치표의 전주 대비
    week_events = repo.events_of_week(week)
    # 요약·인사이트의 근거 표시 [#id] 를 원문 링크로 바꿀 때 쓴다 (인용되는 id 는 모두 그 주의 이벤트다)
    links = {e.id: e.url for e in week_events if e.id is not None}
    html = render.to_html(r, prev_stats=prev_stats, links=links)
    markdown = render.to_markdown(r, prev_stats=prev_stats, links=links)
    out = ROOT / "data" / f"report-{week}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    out.with_suffix(".md").write_text(markdown, encoding="utf-8")
    typer.echo(f"리포트 생성: {out}")

    # 수집·분류가 덜 끝난 채로 리포트를 만들면 그 이벤트는 조용히 빠진다 — 알린다
    unclassified = sum(1 for e in week_events if e.category is None)
    if unclassified:
        typer.echo(f"경고: {week} 이벤트 {unclassified}건이 미분류라 리포트에서 빠졌습니다", err=True)
    if dry_run:
        repo.save_report(r, sent=False)  # 이미 발송된 주차의 기록은 덮어쓰지 않는다
        return
    if unclassified and stored is None:
        slack.send_ops_alert(f"주간 리포트 {week}: 미분류 이벤트 {unclassified}건이 리포트에서 빠졌습니다 "
                             "(classify 가 끝나기 전에 report 가 실행됨)")

    def record(key: str) -> None:
        """발송 기록. 첫 발송 직후 리포트 내용을 확정 저장해, 재실행 때 같은 내용을 쓰게 한다."""
        if not sent:
            repo.save_report(r, sent=True)
        repo.mark_report_sent(week, key)
        sent.add(key)

    def send_slack() -> None:
        done = sum(1 for key in sent if key.startswith("slack#"))  # 지난 실행에서 이미 나간 메시지 수
        slack.send_report(r, full_report_url=env("REPORT_URL") or None, start=done, links=links,
                          on_sent=lambda i: record(f"slack#{i}"))

    senders = {
        "slack": send_slack,
        "email": lambda: email.send_html(f"[경쟁사 주간 리포트] {week}", html, text=markdown),
    }
    failed: list[str] = []
    for name in CHANNELS:  # 한 채널 실패가 다른 채널 발송을 막지 않게
        if name in sent:
            continue
        try:
            senders[name]()
            typer.echo(f"[{name}] 발송 완료")
        except Exception as e:
            failed.append(name)
            slack.send_ops_alert(f"주간 리포트 발송 실패 {week}/{name}: {slack.describe_error(e)}")
            typer.echo(f"[{name}] 발송 실패: {slack.describe_error(e)}", err=True)
            continue
        record(name)
    if not sent:
        repo.save_report(r, sent=False)  # 아무것도 못 나갔으면 초안으로만 남긴다
    if failed:
        typer.echo(f"같은 명령을 다시 실행하면 실패한 부분만 재발송됩니다: {', '.join(failed)}", err=True)
        raise typer.Exit(1)


@cli.command()
def seed(demo: bool = typer.Option(False, help="화면 확인용: 최근 30일에 분류까지 끝난 샘플을 넣는다"),
         clear: bool = typer.Option(False, help="샘플(리허설·데모)을 모두 지우기만 한다")) -> None:
    """샘플 데이터를 넣거나 지운다. 샘플에는 [샘플] 표식이 붙고, 지울 때 실제 데이터는 건드리지 않는다.

    (옵션 없음) 발송 리허설용: 과거 주차에 미분류 이벤트를 넣는다. 실제 주차와 섞이지 않는다.
                순서: seed → classify → report --week <리허설 주차> → seed --clear
    --demo      화면 확인용: 최근 30일에 이벤트·수집 항목·리포트·LLM 사용량을 넣어 웹 대시보드를 채운다.
                실제 데이터와 같은 기간에 섞이므로 운영 시작 전에 반드시 seed --clear 로 지운다.
    """
    from app.infra import sample_data

    if clear:
        gone = sample_data.clear_demo()
        sample_data.clear()  # 리허설 주차의 리포트·발송 기록
        typer.echo(f"샘플 삭제: 이벤트 {gone['events']}건, 수집 항목 {gone['items']}건, 리포트 {gone['reports']}건, "
                   f"LLM 호출 {gone['llm_calls']}건")
    elif demo:
        made = sample_data.seed_demo()
        typer.echo(f"데모 샘플 저장: 이벤트 {made['events']}건, 수집 항목 {made['items']}건, 리포트 {made['reports']}건")
        typer.echo("운영을 시작하기 전에 `python -m app seed --clear` 로 지우세요", err=True)
    else:
        typer.echo(f"샘플 이벤트 {sample_data.seed()}건 저장 (주차 {sample_data.REHEARSAL_WEEK})")


@cli.command()
def prune(weeks: int = typer.Option(8, min=1, help="보관 기간(주). 이보다 오래된 스냅샷을 지운다")) -> None:
    """오래된 수집 이력(snapshots)을 지운다. 항목의 최신 상태와 이벤트·리포트는 지우지 않는다."""
    from app.infra.storage import repo

    typer.echo(f"스냅샷 {repo.prune_snapshots(weeks=weeks)}건 삭제 ({weeks}주 초과분)")


@cli.command()
def dashboard() -> None:
    """DB 내용을 data/dashboard.html 한 파일로 만든다. 브라우저로 바로 열어 볼 수 있다."""
    from app.infra import dashboard as _dashboard

    typer.echo(f"대시보드 생성: {_dashboard.build()}")


def _age_text(hours: float) -> str:
    """경과 시간을 읽기 쉬운 말로: 0.5 → "방금", 5 → "5시간 전", 60 → "2일 전"."""
    if hours < 1:
        return "방금"
    return f"{int(hours)}시간 전" if hours < 48 else f"{int(hours // 24)}일 전"


@cli.command()
def status(strict: bool = typer.Option(False, help="멈춘 소스가 있으면 종료 코드 1")) -> None:
    """수집·분류·발송이 지금 어떤 상태인지 한 화면으로 보여준다."""
    from app.infra.notify.render import BRAND_LABEL
    from app.infra.storage import db, repo

    now = datetime.now()  # 저장된 시각과 같은 기준(시간대 없는 한국 시각)
    typer.echo(f"DB: {db.backend()}   기준 시각: {now:%Y-%m-%d %H:%M} KST")

    sources = repo.source_freshness()
    stale: list[str] = []
    typer.echo("\n[수집]")
    if not sources:
        typer.echo("  수집된 항목이 없습니다")
    for row in sources:
        hours = (now - datetime.fromisoformat(row["last_collected_at"])).total_seconds() / 3600
        is_stale = hours > STALE_HOURS
        if is_stale:
            stale.append(f"{row['brand']}/{row['source']}")
        label = BRAND_LABEL.get(row["brand"], row["brand"])
        typer.echo(f"  {'멈춤' if is_stale else '정상'}  {label} · {row['source']} ({row['source_type']}): "
                   f"{row['items']:,}건, 마지막 수집 {_age_text(hours)}")

    pending = repo.pending_urgent()
    typer.echo(f"\n[분류] 미분류 이벤트 {repo.unclassified_count():,}건, 발송 대기 중인 긴급 알림 {len(pending)}건")

    typer.echo("\n[주간 리포트]")
    reports = repo.report_status()
    if not reports:
        typer.echo("  저장된 리포트가 없습니다")
    for row in reports:
        if row["sent_at"]:
            missing = [c for c in CHANNELS if c not in row["channels"]]
            state = "발송 완료" if not missing else f"일부 발송 (미발송: {', '.join(missing)})"
        else:
            state = "미발송 (초안)"
        typer.echo(f"  {row['week']}  {state}")

    if stale:
        typer.echo(f"\n{STALE_HOURS}시간 넘게 수집이 없는 소스: {', '.join(stale)}", err=True)
        if strict:
            raise typer.Exit(1)


@cli.command()
def doctor(send_test: bool = typer.Option(False, help="운영 경고 채널과 메일로 확인 메시지를 실제로 보낸다")) -> None:
    """설정 점검: 무엇이 빠졌고 어떤 기능이 그 때문에 안 되는지 알려준다. 비밀값은 출력하지 않는다."""
    from app.core.config import env, env_key
    from app.infra.notify import email, slack
    from app.infra.storage import db

    failed = 0

    def line(level: str, name: str, detail: str) -> None:
        nonlocal failed
        if level == "실패":
            failed += 1
        typer.echo(f"  [{level}] {name}: {detail}", err=level == "실패")

    def need(keys: list[str], name: str, purpose: str, level: str = "실패") -> None:
        try:
            missing = [k for k in keys if not env_key(k)]
        except RuntimeError as e:  # 값이 여러 줄로 들어간 경우 (env 가 이름을 밝혀 알린다)
            line("실패", name, str(e))
            return
        if missing:
            line(level, name, f"{', '.join(missing)} 없음 — {purpose}")
        else:
            line("정상", name, "설정됨")

    typer.echo("[저장소]")
    try:
        db.ping()
        line("정상", "DB", f"{db.backend()} 연결됨")
        if db.backend() == "sqlite":
            line("참고", "DB", "SQLite 파일 사용 중 — GitHub Actions 에서는 DATABASE_URL(PostgreSQL)이 필요합니다")
    except Exception as e:
        line("실패", "DB", slack.describe_error(e))

    typer.echo("[분석]")
    need(["ANTHROPIC_API_KEY"], "LLM", "classify·report 가 실패합니다")

    typer.echo("[알림]")
    for key, purpose, level in ((slack.REPORT_WEBHOOK_ENV, "주간 리포트·긴급 알림이 슬랙으로 나가지 않습니다", "실패"),
                                ("SLACK_WEBHOOK_OPS", "운영 경고가 콘솔에만 남습니다", "경고")):
        problem = slack.webhook_problem(key)
        line(level if problem else "정상", key, f"{problem} — {purpose}" if problem else "형식 확인됨")
    missing_mail = email.missing_settings()
    if missing_mail:
        line("실패", "메일", f"{', '.join(missing_mail)} 없음 — 주간 리포트가 메일로 나가지 않습니다")
    else:
        line("정상", "메일", "설정됨")
    if not (env("REPORT_URL") or "").strip():
        line("참고", "REPORT_URL", "없음 — 슬랙 메시지에 \"전체 리포트 보기\" 버튼이 빠집니다")

    typer.echo("[수집 API]")
    need(["NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"], "네이버 뉴스", "네이버 뉴스 수집이 실패합니다", "경고")
    # 뉴스용 NAVER API HUB 키로는 쇼핑 검색을 쓸 수 없다 (API HUB 에 쇼핑 검색이 없음) → 전용 키가 있어야 한다
    need(["NAVER_SHOP_CLIENT_ID", "NAVER_SHOP_CLIENT_SECRET"], "네이버 쇼핑",
         "상품·가격 수집이 실패합니다 (개발자센터 키 필요, 뉴스용 API HUB 키로는 안 됨)", "경고")
    need(["YOUTUBE_API_KEY"], "유튜브", "유튜브 수집이 실패합니다", "경고")
    need(["IG_ACCESS_TOKEN", "IG_BUSINESS_ACCOUNT_ID"], "인스타그램", "인스타그램 수집이 실패합니다", "경고")

    typer.echo("[시간대]")
    offset = datetime.now().astimezone().utcoffset()
    if offset == timedelta(hours=9):
        line("정상", "시간대", "한국 시간")
    else:
        line("실패", "시간대", "한국 시간이 아닙니다 — 주차 경계가 어긋납니다. PC 시간대를 서울로 맞추세요")

    if send_test:
        typer.echo("[확인 메시지 발송]")
        for name, send in (("슬랙 운영 채널", lambda: slack.send_test("SLACK_WEBHOOK_OPS")),
                           ("메일", email.send_test)):
            try:
                send()
                line("정상", name, "발송됨 — 받은 쪽에서 확인하세요")
            except Exception as e:
                line("실패", name, slack.describe_error(e))

    if failed:
        typer.echo(f"\n실패 {failed}건. 위 항목을 채운 뒤 다시 실행하세요", err=True)
        raise typer.Exit(1)
    typer.echo("\n필수 설정이 모두 갖춰졌습니다")


if __name__ == "__main__":
    cli()
