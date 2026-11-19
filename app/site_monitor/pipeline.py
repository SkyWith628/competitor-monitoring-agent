"""[A] 사이트 파이프라인: 수집 → 저장 → 변화감지."""
from __future__ import annotations

from app.core.config import brands_config
from app.core.models import Brand, ChangeEvent
from app.infra.storage import repo
from app.site_monitor.collectors import SITE_COLLECTORS
from app.site_monitor.differ import diff


def run(brand: Brand) -> list[ChangeEvent]:
    collector = SITE_COLLECTORS[brand](brand, brands_config()[brand.value]["site"])
    items = collector.collect()
    prev = repo.latest_items(brand, source=collector.source)
    events = diff(prev, items)
    repo.save_items(items)
    repo.save_events(events)
    return events
