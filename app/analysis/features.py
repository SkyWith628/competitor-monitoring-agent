"""LLM에 넘기기 전에 코드로 계산하는 수치. 담당: C

규칙: 숫자(가격·할인율·증가 배수)는 여기서 계산해 프롬프트에 넣는다. LLM이 계산하게 하지 않는다.
"""
from __future__ import annotations

import json

from app.core.models import ChangeEvent, ChangeType

_PRICE_TYPES = {ChangeType.PRICE_DOWN, ChangeType.PRICE_UP}


def _num(v: object) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def price_change_pct(e: ChangeEvent) -> float | None:
    """가격 변화율(%). 인하는 음수, 인상은 양수. before/after 가 숫자가 아니면 None."""
    if e.change_type not in _PRICE_TYPES:
        return None
    before, after = _num(e.detail.get("before")), _num(e.detail.get("after"))
    if not before or after is None or before <= 0:
        return None
    return round((after - before) / before * 100, 1)


def discount_pct(e: ChangeEvent) -> float | None:
    """할인율(%, 양수). 가격 인하가 아니면 None."""
    pct = price_change_pct(e)
    return -pct if pct is not None and pct < 0 else None


def spike_ratio(e: ChangeEvent) -> float | None:
    """SNS 급증 배수 (평소 대비 몇 배).

    B의 급증 감지(media_monitor/spike.py)는 detail["spikes"] = {"likes": {"value", "usual", "record", "ratio"}, ...}
    로 지표별 배수를 이미 계산해 준다. 여러 지표가 급증했으면 가장 큰 배수를 쓴다.
    """
    spikes = e.detail.get("spikes")
    if not isinstance(spikes, dict):
        return None
    ratios = [r for s in spikes.values() if isinstance(s, dict) and (r := _num(s.get("ratio"))) is not None]
    return max(ratios) if ratios else None


def computed(e: ChangeEvent) -> dict:
    """이벤트별로 코드가 계산한 값. 값이 없는 항목은 넣지 않는다."""
    out = {"price_change_pct": price_change_pct(e), "spike_ratio": spike_ratio(e)}
    return {k: v for k, v in out.items() if v is not None}


def event_line(e: ChangeEvent, *, with_labels: bool = False) -> str:
    """프롬프트에 넣는 이벤트 한 줄(JSON). 원본 필드 + 코드 계산값."""
    row: dict = {"id": e.id, "brand": e.brand.value, "change_type": e.change_type.value,
                 "title": e.title, "detail": e.detail}
    if calc := computed(e):
        row["computed"] = calc
    if with_labels:
        row |= {"category": e.category.value if e.category else None,
                "importance": e.importance, "note": e.llm_note}
    return json.dumps(row, ensure_ascii=False, default=str)
