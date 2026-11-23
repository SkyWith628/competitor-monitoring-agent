"""[B] 뉴스·SNS 파이프라인."""
from __future__ import annotations

from app.core.config import brands_config
from app.core.models import Brand, ChangeEvent, ChangeType
from app.infra.storage import repo
from app.media_monitor.dedup import dedup
from app.media_monitor.isolate import Isolated
from app.media_monitor.news.google_rss import GoogleNewsCollector
from app.media_monitor.news.naver import NaverNewsCollector
from app.media_monitor.sns.instagram import InstagramCollector
from app.media_monitor.sns.youtube import YoutubeCollector
from app.media_monitor.spike import detect_spikes


def run_news(brand: Brand) -> list[ChangeEvent]:
    """네이버·구글 뉴스를 수집한다.

    수집기 하나가 실패하거나 아직 구현 전이어도 나머지 수집기의 결과는 저장한다
    (예: 네이버 키가 만료돼도 구글은 계속 수집). 실패한 수집기는 저장을 마친 뒤
    예외로 알려서 CLI가 운영 경고로 보낸다.
    """
    cfg = brands_config()[brand.value]["news"]
    items = []
    collectors = Isolated(NaverNewsCollector, GoogleNewsCollector)
    for cls in collectors:
        items += cls(brand, cfg).collect()
    new_items = [i for i in dedup(items) if not repo.exists(i)]
    events = [ChangeEvent(brand=brand, change_type=ChangeType.NEWS, title=i.title, url=i.url,
                          detail={"source": i.source}) for i in new_items]
    repo.save_items(new_items)
    repo.save_events(events)
    collectors.raise_if_failed(f"뉴스 수집 일부 실패 (나머지 이벤트 {len(events)}건은 저장함)")
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
                               url=i.url) for i in items if not repo.exists(i)]
        repo.save_items(items)
    repo.save_events(events)
    return events
