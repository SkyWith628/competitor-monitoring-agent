"""Slack Incoming Webhook 발송. 담당: D"""
from __future__ import annotations

import sys

import httpx

from app.core.config import env


def send_blocks(blocks: list[dict], fallback_text: str, webhook_env: str = "SLACK_WEBHOOK_REPORT") -> None:
    url = env(webhook_env)
    if not url:
        raise RuntimeError(f"{webhook_env} 가 .env 에 없습니다")
    httpx.post(url, json={"text": fallback_text, "blocks": blocks}, timeout=10).raise_for_status()


def send_ops_alert(message: str) -> None:
    """수집 실패·0건·LLM 오류 등 운영 경고 → #monitor-ops"""
    url = env("SLACK_WEBHOOK_OPS")
    if not url:
        return
    try:
        httpx.post(url, json={"text": f":warning: {message}"}, timeout=10)
    except httpx.HTTPError as e:  # 경고 발송 실패가 본 작업을 멈추면 안 된다
        print(f"운영 경고 발송 실패: {e!r}", file=sys.stderr)
