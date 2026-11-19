"""전일 스냅샷과 오늘 수집 결과를 비교해 ChangeEvent 생성 (규칙 기반, LLM 미사용). 담당: A"""
from __future__ import annotations

import hashlib

from app.core.models import ChangeEvent, ChangeType, RawItem, SourceType

PRICE_CHANGE_THRESHOLD = 0.05  # ±5% 이상만 이벤트로


def _effective_price(item: RawItem) -> int | None:
    return item.sale_price or item.price


def page_hash(text: str | None) -> str:
    normalized = " ".join((text or "").split())
    return hashlib.sha256(normalized.encode()).hexdigest()


def diff(prev: dict[str, RawItem], curr: list[RawItem]) -> list[ChangeEvent]:
    """prev: external_id → 어제 항목, curr: 오늘 수집 결과."""
    events: list[ChangeEvent] = []
    for item in curr:
        old = prev.get(item.external_id)
        base = {"brand": item.brand, "url": item.url, "title": item.title}

        if item.source_type == SourceType.SITE_PAGE:
            if old and page_hash(old.body) != page_hash(item.body):
                events.append(ChangeEvent(change_type=ChangeType.PROMO_CHANGED, **base,
                                          detail={"before": old.body, "after": item.body}))
            continue

        if old is None:
            events.append(ChangeEvent(change_type=ChangeType.NEW_PRODUCT, **base,
                                      detail={"price": _effective_price(item)}))
            continue

        before, after = _effective_price(old), _effective_price(item)
        if before and after and abs(after - before) / before >= PRICE_CHANGE_THRESHOLD:
            kind = ChangeType.PRICE_DOWN if after < before else ChangeType.PRICE_UP
            events.append(ChangeEvent(change_type=kind, **base,
                                      detail={"before": before, "after": after}))

        if old.in_stock and item.in_stock is False:
            events.append(ChangeEvent(change_type=ChangeType.SOLD_OUT, **base))
        elif old.in_stock is False and item.in_stock:
            events.append(ChangeEvent(change_type=ChangeType.RESTOCK, **base))
    return events
