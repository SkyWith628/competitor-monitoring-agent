"""Slack Incoming Webhook 발송. 담당: D"""
from __future__ import annotations

import sys

import httpx

from app.core.config import env
from app.core.models import ChangeEvent, WeeklyReport
from app.infra.notify import render

REPORT_WEBHOOK_ENV = "SLACK_WEBHOOK_REPORT"


def send_blocks(blocks: list[dict], fallback_text: str, webhook_env: str = "SLACK_WEBHOOK_REPORT") -> None:
    url = env(webhook_env)
    if not url:
        raise RuntimeError(f"{webhook_env} 가 .env 에 없습니다")
    httpx.post(url, json={"text": fallback_text, "blocks": blocks}, timeout=10).raise_for_status()


def send_report(report: WeeklyReport, full_report_url: str | None = None) -> None:
    """주간 리포트 발송: 본 메시지 → 브랜드별 후속 메시지.

    Incoming Webhook은 스레드 답글을 달 수 없어서, 후속 메시지는 같은 채널에 연달아 보낸다.
    """
    send_blocks(render.to_slack_blocks(report, full_report_url),
                fallback_text=f"경쟁사 주간 리포트 {report.week}: {report.tldr}")
    for blocks in render.to_slack_brand_blocks(report):
        send_blocks(blocks, fallback_text=f"경쟁사 주간 리포트 {report.week} 브랜드별 요약")


def send_urgent(events: list[ChangeEvent]) -> int:
    """긴급 알림을 이벤트마다 한 건씩 리포트 채널로 보낸다. 보낸 건수를 돌려준다.

    웹훅이 설정되지 않았으면 0을 돌려주고, 한 건이 실패해도 나머지는 계속 보낸다.
    """
    if not env(REPORT_WEBHOOK_ENV):
        return 0
    sent = 0
    for e in events:
        try:
            send_blocks(render.to_urgent_blocks(e),
                        fallback_text=f"[긴급] {render.BRAND_LABEL.get(e.brand.value, e.brand.value)} {e.title}",
                        webhook_env=REPORT_WEBHOOK_ENV)
            sent += 1
        except Exception as exc:  # noqa: BLE001 — 한 건의 실패가 나머지 알림을 막으면 안 된다
            print(f"긴급 알림 발송 실패 (event id={e.id}): {exc!r}", file=sys.stderr)
    return sent


def send_ops_alert(message: str) -> None:
    """수집 실패·0건·LLM 오류 등 운영 경고 → #monitor-ops"""
    url = env("SLACK_WEBHOOK_OPS")
    if not url:
        return
    try:
        httpx.post(url, json={"text": f":warning: {message}"}, timeout=10)
    except httpx.HTTPError as e:  # 경고 발송 실패가 본 작업을 멈추면 안 된다
        print(f"운영 경고 발송 실패: {e!r}", file=sys.stderr)
