"""LLM 비용 추정 — 리포트 하단 표기용 (DESIGN 6.3 비용 관리). 담당: C

llm_calls 의 입력 토큰에는 캐시 읽기/쓰기 토큰이 합쳐져 있어(llm_client.log_usage) 모두 일반 입력 단가로
계산한다. 캐시 읽기는 실제로 훨씬 싸므로 이 값은 **상한에 가까운 보수적 추정**이다.
"""
from __future__ import annotations

import logging

# USD / 1M tokens (input, output). Anthropic 1st-party API 정가, 2026-11 기준.
# Haiku 5.5 는 프롬프트 10만 토큰 이하 단가 — 이 프로젝트의 배치는 그보다 훨씬 작다.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
COST_KEY = "LLM 비용 추정(USD, 최근 7일)"


def price_of(model: str) -> tuple[float, float] | None:
    """정확히 일치하지 않으면 접두어로 찾는다 (대체 모델 응답 등에서 접미사가 붙어도 잡히도록)."""
    if model in PRICES:
        return PRICES[model]
    return next((p for m, p in PRICES.items() if model.startswith(m)), None)


def estimate_usd(usage: list[dict]) -> float | None:
    """repo.llm_usage() 결과 → 달러. 단가를 모르는 모델이 섞여 있으면 그 모델은 빼고 계산한다.
    호출 기록이 하나도 없으면 None."""
    if not usage:
        return None
    total = 0.0
    for row in usage:
        if (p := price_of(row["model"])) is None:
            continue
        total += row["input_tokens"] / 1e6 * p[0] + row["output_tokens"] / 1e6 * p[1]
    return round(total, 4)


def weekly_cost() -> float | None:
    """최근 7일 LLM 비용 추정. DB 조회가 실패해도 리포트는 나가야 하므로 None 으로 넘긴다."""
    try:
        from app.infra.storage import repo

        return estimate_usd(repo.llm_usage(days=7))
    except Exception:
        logging.getLogger(__name__).warning("LLM 비용 조회 실패", exc_info=True)
        return None
