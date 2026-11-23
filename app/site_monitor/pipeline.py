"""[A] 사이트 파이프라인: 수집 → 저장 → 변화감지."""
from __future__ import annotations

from app.core.config import brands_config
from app.core.models import Brand, ChangeEvent
from app.infra.storage import repo
from app.site_monitor.collectors import SITE_COLLECTORS
from app.site_monitor.collectors.naver_shopping import NaverShoppingCollector
from app.site_monitor.differ import diff


def run(brand: Brand) -> list[ChangeEvent]:
    site = brands_config()[brand.value]["site"]
    # 공식몰이 봇을 막는 브랜드는 site.via: naver_shopping 으로 대체 소스 사용 (ROBOTS.md)
    via_api = site.get("via") == "naver_shopping"
    if not via_api and not site.get("urls"):
        # 직접 수집 대상이 아닌 브랜드(봇 차단). 실패·0건 알림 대신 CLI 가 '건너뜀'으로 처리하게 한다.
        # 이 브랜드들은 쇼핑인사이트 추이만 따로 수집한다: python -m app.site_monitor.trend
        raise NotImplementedError(f"{brand.value}: 사이트 직접 수집 대상 아님 (site.urls 비어 있음)")
    cls = NaverShoppingCollector if via_api else SITE_COLLECTORS[brand]
    collector = cls(brand, site)
    items = collector.collect()
    prev = repo.latest_items(brand, source=collector.source)
    events = diff(prev, items)
    repo.save_items(items)
    repo.save_events(events)
    return events
