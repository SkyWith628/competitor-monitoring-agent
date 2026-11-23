"""Slack Incoming Webhook 발송. 담당: D

웹훅은 채널마다 하나씩 발급받는 "주소가 곧 비밀번호"인 URL이다. 두 개를 쓴다.
    - SLACK_WEBHOOK_REPORT: 주간 리포트·긴급 알림이 올라가는 채널 (팀이 읽는 곳)
    - SLACK_WEBHOOK_OPS: 수집 실패 같은 운영 경고가 올라가는 채널 (#monitor-ops, 담당자가 읽는 곳)

누가 부르나 (모두 `app/__main__.py` 의 CLI 명령)
    - report  → `send_report`        주간 리포트 (본 메시지 + 브랜드별 후속 메시지)
    - classify → `send_urgent`       중요도 5 이벤트의 긴급 알림
    - 모든 명령 → `send_ops_alert`    실패·이상 징후 경고. 절대 예외를 던지지 않는다
    - 설정 점검 → `webhook_problem`(보내지 않고 형식만 확인), `send_test`(실제로 한 건 보내 확인)
    - 오류를 화면·경고에 찍을 때 → `describe_error` (주소를 가린 설명)

데이터 흐름
    WeeklyReport / ChangeEvent ─ render.to_*_blocks() ─ send_blocks() ─ _post() ─ httpx.post(웹훅)
    메시지 모양은 render.py 가, 전송·재시도·오류 처리는 이 모듈이 맡는다.

실패 처리 원칙
    - 일시적 실패(429, 5xx, 연결 오류)는 잠깐 기다렸다 다시 보낸다. 그 밖의 4xx 는 바로 실패한다(`_retry_wait`).
    - 끝내 실패하면 예외를 올린다. 발송 기록·재실행은 호출한 쪽(CLI)이 맡는다. `send_ops_alert` 만 예외.
    - 웹훅 주소는 어떤 경로로도 로그·경고·예외 설명에 남기지 않는다.
"""
from __future__ import annotations

import re
import sys
import time
from collections.abc import Callable

import httpx

from app.core.config import env
from app.core.models import ChangeEvent, WeeklyReport
from app.infra.notify import render

REPORT_WEBHOOK_ENV = "SLACK_WEBHOOK_REPORT"
OPS_WEBHOOK_ENV = "SLACK_WEBHOOK_OPS"
# 스킴://… 꼴이면 무엇이든 주소로 본다 (https 웹훅, postgresql://user:pw@host, ?key= 가 붙은 API 주소)
_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s'\"<>]+")
# 슬랙이 발급하는 웹훅 주소 꼴. 형식 점검(`webhook_problem`)에만 쓰고 발송을 막지는 않는다
_WEBHOOK = re.compile(r"https://hooks\.slack(-gov)?\.com/services/[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*/?")

_TIMEOUT = 10  # 초. 요청 한 번의 제한 시간
_MAX_ATTEMPTS = 3  # 리포트·긴급 알림: 일시적 실패는 이 횟수까지 다시 시도
_OPS_MAX_ATTEMPTS = 2  # 운영 경고: 본 작업을 붙잡지 않도록 한 번만 더
_BACKOFF = 0.5  # 초. 재시도 대기 = _BACKOFF × 2^(시도-1) → 0.5초, 1초
_RETRY_AFTER_CAP = 30.0  # 초. 슬랙이 Retry-After 로 더 길게 요구해도 여기까지만 기다린다
_OPS_WAIT_CAP = 2.0  # 초. 운영 경고는 Retry-After 가 길어도 오래 기다리지 않는다
_TEXT_LIMIT = 3000  # 알림 미리보기·대체 텍스트 상한 (슬랙은 4만 자를 넘는 text 를 잘라낸다)

_sleep = time.sleep  # 테스트에서 바꿔 끼워 실제로 기다리지 않게 한다


def describe_error(e: BaseException) -> str:
    """경고·로그에 써도 안전한 오류 설명.

    예외 메시지에는 요청 주소가 통째로 들어가는 경우가 많다(httpx). 웹훅 주소, `?key=` API 키,
    비밀번호가 든 DB 주소는 그 자체가 비밀값이므로 주소는 모두 가린다.

    Args:
        e: 아무 예외.

    Returns:
        "예외이름: 메시지" 한 줄. 메시지 안의 주소는 "[주소 가림]" 으로 바뀐다. HTTP 상태 오류는 메시지를
        버리고 상태 코드만 남긴다("HTTPStatusError (HTTP 404)"). 예외를 던지지 않는다.

    예:
        except Exception as e:
            slack.send_ops_alert(f"수집 실패 site/spao: {slack.describe_error(e)}")
    """
    name = type(e).__name__
    response = getattr(e, "response", None)
    if isinstance(e, httpx.HTTPStatusError) and response is not None:
        return f"{name} (HTTP {response.status_code})"
    message = _URL.sub("[주소 가림]", str(e)).strip()
    return f"{name}: {message}" if message else name


def webhook_problem(webhook_env: str) -> str | None:
    """웹훅 설정에 문제가 있으면 한 줄 설명, 없으면 None. 실제로 보내 보지는 않는다(→ `send_test`).

    설명에는 주소를 넣지 않는다(웹훅 주소는 그 자체가 비밀값).

    Args:
        webhook_env: 확인할 환경 변수 이름. "SLACK_WEBHOOK_REPORT" 또는 "SLACK_WEBHOOK_OPS".

    Returns:
        값이 비었거나 `https://hooks.slack.com/services/...` 꼴이 아니면 사람에게 보여줄 한 줄, 정상이면 None.
        형식만 본다 — None 이어도 폐기된 웹훅일 수 있으니 실제 확인은 `send_test` 로 한다.

    예:
        if problem := slack.webhook_problem("SLACK_WEBHOOK_REPORT"):
            typer.echo(problem, err=True)
    """
    url = (env(webhook_env) or "").strip()
    if not url:
        return f"{webhook_env} 가 설정되지 않았습니다"
    if not _WEBHOOK.fullmatch(url):
        return (f"{webhook_env} 값이 Slack Incoming Webhook 주소 형식이 아닙니다 "
                "(https://hooks.slack.com/services/ 로 시작해야 합니다)")
    return None


def _clip(text: str, limit: int = _TEXT_LIMIT) -> str:
    """limit 자를 넘으면 잘라내고 말줄임표를 붙인다 (결과 길이는 limit 이하)."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _retry_wait(e: Exception, attempt: int, wait_cap: float) -> float | None:
    """다시 시도해 볼 만한 실패면 기다릴 시간(초), 아니면 None.

    - 429: 슬랙이 알려준 Retry-After 만큼(상한 있음) 기다린다.
    - 5xx·전송 오류(연결 실패, 타임아웃): 짧게 기다렸다가 다시 보낸다.
    - 그 밖의 4xx: 웹훅 주소나 payload 문제라 다시 보내도 같은 결과다 → 바로 실패.
    """
    backoff = min(_BACKOFF * 2 ** (attempt - 1), wait_cap)
    if isinstance(e, httpx.TransportError):
        return backoff
    # 응답이 없는 상태 오류는 원인을 알 수 없어 다시 보내지 않는다. httpx.InvalidURL 처럼 주소 자체가 잘못된
    # 경우와 그 밖의 예외도 마찬가지다.
    response = getattr(e, "response", None)
    if not isinstance(e, httpx.HTTPStatusError) or response is None:
        return None
    if response.status_code == 429:
        # 슬랙은 Retry-After 를 초 단위 숫자로 준다. 없거나 읽을 수 없으면 기본 대기 시간을 쓴다
        try:
            wanted = float(response.headers.get("Retry-After", ""))
        except (TypeError, ValueError):
            return backoff
        return min(max(wanted, 0.0), wait_cap)
    return backoff if response.status_code >= 500 else None


def _post(url: str, payload: dict, attempts: int = _MAX_ATTEMPTS, wait_cap: float = _RETRY_AFTER_CAP) -> None:
    """웹훅으로 POST. 일시적 실패는 attempts 번까지 시도하고, 끝내 실패하면 마지막 예외를 그대로 올린다.

    응답을 받지 못한 타임아웃 뒤의 재시도는 드물게 같은 메시지를 두 번 올릴 수 있다. 알림이 빠지는 것보다
    낫다고 보고 다시 보낸다.
    """
    for attempt in range(1, attempts + 1):
        try:
            httpx.post(url, json=payload, timeout=_TIMEOUT).raise_for_status()
            return
        except Exception as e:  # 재시도 여부만 판단하고, 대상이 아니면 그대로 다시 올린다
            wait = _retry_wait(e, attempt, wait_cap)
            if wait is None or attempt == attempts:
                raise
        _sleep(wait)


def _webhook_url(webhook_env: str) -> str:
    """환경 변수에서 웹훅 주소를 읽는다. 비어 있으면 변수 이름만 담은 RuntimeError."""
    url = (env(webhook_env) or "").strip()  # Secret 끝에 줄바꿈이 섞여 들어오는 경우가 있다
    if not url:
        raise RuntimeError(f"{webhook_env} 가 .env 에 없습니다")
    return url


def send_blocks(blocks: list[dict], fallback_text: str, webhook_env: str = "SLACK_WEBHOOK_REPORT") -> None:
    """Block Kit 메시지 한 건 발송. fallback_text 는 알림 미리보기와 블록을 못 그리는 화면에 쓰인다.

    429·5xx·전송 오류는 최대 3번까지 시도한다. 그 밖의 4xx 는 바로 실패한다.

    Args:
        blocks: Block Kit 블록 목록 (`render.to_slack_blocks` 등의 결과).
        fallback_text: 알림 미리보기 문구. 3000자를 넘으면 잘라낸다.
        webhook_env: 웹훅 주소가 든 환경 변수 이름. 기본은 리포트 채널.

    Raises:
        RuntimeError: 환경 변수가 비어 있다.
        httpx.HTTPStatusError: 슬랙이 4xx·5xx 로 거절했다 (재시도 후에도).
        httpx.TransportError: 연결 실패·타임아웃 (재시도 후에도).
        예외 메시지에는 웹훅 주소가 들어 있으니 출력할 때는 `describe_error` 를 거칠 것.

    예:
        send_blocks(render.to_urgent_blocks(event), fallback_text="[긴급] 스파오 가격 인하")
    """
    _post(_webhook_url(webhook_env), {"text": _clip(fallback_text), "blocks": blocks})


def send_report(report: WeeklyReport, full_report_url: str | None = None, start: int = 0,
                on_sent: Callable[[int], None] | None = None) -> None:
    """주간 리포트 발송: 본 메시지(0번) → 브랜드별 후속 메시지(1번~).

    Incoming Webhook은 스레드 답글을 달 수 없어서, 후속 메시지는 같은 채널에 연달아 보낸다.
    메시지가 하나 나갈 때마다 on_sent(번호)를 부른다. 중간에 실패해 다시 보낼 때는 이미 나간 개수를
    start 로 넘기면 그 다음 메시지부터 보낸다(본 메시지 중복 방지).

    Args:
        report: 보낼 리포트.
        full_report_url: 본 메시지의 "전체 리포트 보기" 버튼 주소. 없으면 버튼을 뺀다.
        start: 건너뛸 메시지 수(= 지난 실행에서 이미 나간 개수). 0 이면 처음부터.
        on_sent: 메시지 하나가 나갈 때마다 그 번호(0부터)로 불린다. CLI 가 여기서 진행 상황을 DB 에
            기록해 두기 때문에, 중간에 실패해도 다음 실행이 이어서 보낼 수 있다.

    Raises:
        `send_blocks` 와 같다. 실패한 메시지에서 멈추고 그 뒤 메시지는 보내지 않는다.

    예:
        sent: list[int] = []
        send_report(report, full_report_url=env("REPORT_URL"), on_sent=sent.append)
    """
    tldr = " ".join((report.tldr or "").split())
    title = f"경쟁사 주간 리포트 {report.week}"
    messages = [(render.to_slack_blocks(report, full_report_url), f"{title}: {tldr}" if tldr else title)]
    messages += [(blocks, f"{title} 브랜드별 요약") for blocks in render.to_slack_brand_blocks(report)]
    for i, (blocks, fallback) in enumerate(messages):
        if i < start:
            continue
        send_blocks(blocks, fallback_text=fallback)
        if on_sent:
            on_sent(i)


def send_urgent(events: list[ChangeEvent]) -> list[ChangeEvent]:
    """긴급 알림을 이벤트마다 한 건씩 리포트 채널로 보낸다. **실제로 나간 이벤트**를 돌려준다.

    한 건이 실패해도 나머지는 계속 보낸다. 돌려준 목록이 입력보다 짧으면 실패한 건이 있다는 뜻이므로
    호출한 쪽이 발송 기록·재시도·운영 경고를 처리한다. 웹훅이 설정되지 않았으면 아무것도 보내지 않는다.

    Args:
        events: 알릴 이벤트 목록 (보통 중요도 5).

    Returns:
        발송에 성공한 이벤트 목록(입력 순서 유지). 예외를 던지지 않고, 실패한 건은 이벤트 id 와 주소를 가린
        오류 설명을 stderr 에 남긴다.

    예:
        sent = send_urgent(urgent)
        repo.mark_urgent_sent([e.id for e in sent])  # 빠진 이벤트는 다음 실행에서 다시 시도
    """
    if not env(REPORT_WEBHOOK_ENV):
        return []
    sent: list[ChangeEvent] = []
    for e in events:
        try:
            send_blocks(render.to_urgent_blocks(e),
                        fallback_text=f"[긴급] {render.BRAND_LABEL.get(e.brand.value, e.brand.value)} {e.title}",
                        webhook_env=REPORT_WEBHOOK_ENV)
            sent.append(e)
        except Exception as exc:  # noqa: BLE001 — 한 건의 실패가 나머지 알림을 막으면 안 된다
            print(f"긴급 알림 발송 실패 (event id={e.id}): {describe_error(exc)}", file=sys.stderr)
    return sent


def send_ops_alert(message: str) -> None:
    """수집 실패·0건·LLM 오류 등 운영 경고 → #monitor-ops

    경고 발송이 실패해도 본 작업을 멈추지 않는다. 어떤 예외도 밖으로 내보내지 않고(잘못된 웹훅 주소
    포함) stderr에만 남긴다. 슬랙이 4xx·5xx로 거절한 경우도 실패로 기록한다. 일시적 실패는 한 번만 더
    시도하고, 그 사이 대기는 2초를 넘기지 않는다.

    Args:
        message: 경고 문구. 앞에 :warning: 이모지를 붙여 보낸다. 오류 내용을 넣을 때는 주소가 새지 않게
            `describe_error(e)` 를 쓸 것.

    SLACK_WEBHOOK_OPS 가 없으면 조용히 아무것도 하지 않는다(로컬 실행). 반환값 없음.

    예:
        slack.send_ops_alert("파싱 0건 site/spao: 수집된 항목이 없습니다")
    """
    # env() 호출까지 try 안에 둔다 — 여기서 나는 예외도 본 작업으로 번지면 안 된다
    try:
        url = (env(OPS_WEBHOOK_ENV) or "").strip()  # Secret 끝에 줄바꿈이 섞여 들어오는 경우가 있다
        if not url:
            return
        _post(url, {"text": _clip(f":warning: {message}")}, attempts=_OPS_MAX_ATTEMPTS, wait_cap=_OPS_WAIT_CAP)
    except Exception as e:  # noqa: BLE001 — 경고 발송 실패가 본 작업을 멈추면 안 된다
        print(f"운영 경고 발송 실패: {describe_error(e)}", file=sys.stderr)


def send_test(webhook_env: str = "SLACK_WEBHOOK_OPS") -> None:
    """연결 확인 메시지를 한 건 보낸다. 실패하면 예외를 그대로 올린다(호출한 쪽이 사용자에게 알린다).

    설정이 없으면 RuntimeError, 슬랙이 거절하거나 연결이 안 되면 httpx 예외. 결과를 빨리 알려야 하므로
    다시 시도하지 않는다. 오류를 출력할 때는 `describe_error` 로 주소를 가릴 것.

    Args:
        webhook_env: 확인할 웹훅의 환경 변수 이름. 기본은 운영 경고 채널(팀 채널을 시끄럽게 하지 않도록).

    Raises:
        RuntimeError: 환경 변수가 비어 있다.
        httpx.HTTPStatusError: 슬랙이 거절했다 (404 = 폐기되었거나 잘못된 웹훅).
        httpx.TransportError: 연결 실패·타임아웃.

    예:
        try:
            slack.send_test("SLACK_WEBHOOK_REPORT")
        except Exception as e:
            typer.echo(f"슬랙 연결 실패: {slack.describe_error(e)}", err=True)
    """
    text = f":white_check_mark: 경쟁사 모니터링 연결 확인 — {webhook_env} 웹훅이 정상 동작합니다."
    _post(_webhook_url(webhook_env), {"text": text}, attempts=1)
