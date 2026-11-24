"""[A] 상품 파이프라인: 수집 → 변화 감지 → 저장."""
from __future__ import annotations

from app.core.config import brands_config
from app.core.models import Brand, ChangeEvent
from app.infra.storage import repo
from app.site_monitor.differ import diff
from app.site_monitor.shopping import NaverShoppingCollector


def run(brand: Brand) -> list[ChangeEvent]:
    """네이버 쇼핑 검색으로 브랜드 상품을 받아 지난번 상태와 비교한다.

    첫 수집(저장된 상품이 하나도 없음)은 기준선만 저장하고 이벤트를 만들지 않는다. 그러지 않으면
    처음 본 상품 수백 개가 전부 '신상품' 이벤트가 되어 LLM 분류와 주간 리포트로 들어간다.
    """
    collector = NaverShoppingCollector(brand, brands_config()[brand.value]["site"])
    items = collector.collect()
    prev = repo.latest_items(brand, source=collector.source)
    events = diff(prev, items) if prev else []
    repo.save_items(items)
    repo.save_events(events)
    return events
