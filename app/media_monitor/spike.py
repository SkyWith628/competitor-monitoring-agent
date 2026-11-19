"""SNS 반응 급증 감지. 담당: B"""
from __future__ import annotations

from statistics import mean

from app.core.models import ChangeEvent, ChangeType, RawItem

SPIKE_RATIO = 2.0


def engagement(item: RawItem) -> int:
    return sum(item.metrics.values())


def detect_spikes(recent: list[RawItem], history: list[RawItem]) -> list[ChangeEvent]:
    """history: 같은 계정의 최근 30개 게시물 (평균 기준선)."""
    if not history:
        return []
    baseline = mean(engagement(h) for h in history) or 1
    return [
        ChangeEvent(brand=i.brand, change_type=ChangeType.SNS_SPIKE, title=i.title, url=i.url,
                    detail={"engagement": engagement(i), "baseline": round(baseline)})
        for i in recent
        if engagement(i) >= baseline * SPIKE_RATIO
    ]
