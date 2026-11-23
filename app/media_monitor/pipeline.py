"""[B] 뉴스·SNS 파이프라인."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.core.config import brands_config
from app.core.models import Brand, ChangeEvent, ChangeType
from app.infra.storage import repo
from app.media_monitor.dedup import dedup
from app.media_monitor.news.google_rss import GoogleNewsCollector
from app.media_monitor.news.naver import NaverNewsCollector
from app.media_monitor.sns.instagram import InstagramCollector
from app.media_monitor.sns.youtube import YoutubeCollector
from app.media_monitor.spike import detect_spikes

# 뉴스(구글 when:7d, 네이버 pubDate)와 같은 기간. SNS 수집기는 계정당 최근 20개를 가져오는데
# 활동이 적은 계정은 몇 달~몇 년 전 글까지 오므로, '새 게시물' 이벤트는 이 기간 안의 글만 만든다.
RECENT_DAYS = 7


def run_news(brand: Brand) -> list[ChangeEvent]:
    cfg = brands_config()[brand.value]["news"]
    items = []
    for cls in (NaverNewsCollector, GoogleNewsCollector):
        items += cls(brand, cfg).collect()
    new_items = [i for i in dedup(items) if not repo.exists(i)]
    events = [ChangeEvent(brand=brand, change_type=ChangeType.NEWS, title=i.title, url=i.url,
                          detail={"source": i.source}) for i in new_items]
    repo.save_items(new_items)
    repo.save_events(events)
    return events


def run_sns(brand: Brand) -> list[ChangeEvent]:
    cfg = brands_config()[brand.value]["sns"]
    events: list[ChangeEvent] = []
    for cls in (InstagramCollector, YoutubeCollector):
        collector = cls(brand, cfg)
        items = collector.collect()
        history = repo.recent_posts(brand, source=collector.source, limit=30)
        events += detect_spikes(items, history)
        events += [ChangeEvent(brand=brand, change_type=ChangeType.SNS_NEW_POST, title=i.title,
                               url=i.url) for i in items if _is_recent(i.published_at) and not repo.exists(i)]
        repo.save_items(items)  # 오래된 글도 저장은 한다 (급증 감지의 '평소' 비교 대상)
    repo.save_events(events)
    return events


def _is_recent(published_at: datetime | None) -> bool:
    """올린 시각을 모르면 새 글로 본다 (놓치는 것보다 한 번 더 보는 편이 낫다)."""
    return published_at is None or published_at >= datetime.now() - timedelta(days=RECENT_DAYS)
