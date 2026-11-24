"""[B] 뉴스·SNS 파이프라인."""
from __future__ import annotations

from datetime import datetime, timedelta

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

# 뉴스(구글 when:7d, 네이버 pubDate)와 같은 기간. SNS 수집기는 계정당 최근 20개를 가져오는데
# 활동이 적은 계정은 몇 달~몇 년 전 글까지 오므로, '새 게시물' 이벤트는 이 기간 안의 글만 만든다.
RECENT_DAYS = 7


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
    # 이미 저장된 기사를 앞으로: 같은 소식의 새 기사가 와도 저장된 기사가 대표로 남아 다시 이벤트가 되지 않는다
    # (구글은 매일 최근 7일을 다시 가져오므로, 이어지는 기사가 대표가 되면 같은 소식이 날마다 새로 올라옴)
    # 그다음은 제목에 브랜드명이 있는 기사를 앞으로: 같은 소식이면 '[식음료 소식] KT&G·풀무원·농심' 같은
    # 묶음 기사보다 '농심, 무신사 스탠다드와 새우깡 티셔츠' 같은 제목이 대표가 된다
    keywords = [k.lower() for k in cfg["keywords"]]
    stored = {(i.source, i.external_id): repo.exists(i) for i in items}  # 기사당 DB 조회 한 번

    def is_stored(i) -> bool:
        return stored[(i.source, i.external_id)]

    items.sort(key=lambda i: (not is_stored(i), not any(k in i.title.lower() for k in keywords)))  # 안정 정렬
    new_items = [i for i in dedup(items, ignore=cfg["keywords"]) if not is_stored(i)]
    events = [ChangeEvent(brand=brand, change_type=ChangeType.NEWS, title=i.title, url=i.url,
                          detail={"source": i.source}) for i in new_items]
    # 중복으로 걸러진 기사도 (이벤트 없이) 저장한다. 저장하지 않으면 대표 기사가 7일 창에서 빠진 날,
    # 아직 창 안에 남은 같은 소식의 다른 기사가 '새 기사'가 되어 같은 소식이 다시 이벤트로 올라온다
    repo.save_items([i for i in items if not is_stored(i)])
    repo.save_events(events)
    collectors.raise_if_failed(f"뉴스 수집 일부 실패 (나머지 이벤트 {len(events)}건은 저장함)")
    return events


def run_sns(brand: Brand) -> list[ChangeEvent]:
    """인스타·유튜브를 차례로 수집한다.

    수집기 하나가 실패하거나 아직 구현 전이어도 나머지 수집기의 결과는 저장한다
    (예: 인스타 토큰이 만료돼도 유튜브는 계속 수집). 실패한 수집기는 저장을 마친 뒤
    예외로 알려서 CLI가 운영 경고로 보낸다.
    """
    cfg = brands_config()[brand.value]["sns"]
    events: list[ChangeEvent] = []
    collectors = Isolated(InstagramCollector, YoutubeCollector)  # run_news 와 같은 실패 격리
    for cls in collectors:
        collector = cls(brand, cfg)
        items = collector.collect()  # 실패했거나 구현 전이면 빈 목록
        history = repo.recent_posts(brand, source=collector.source, limit=30)
        events += detect_spikes(items, history)
        events += [ChangeEvent(brand=brand, change_type=ChangeType.SNS_NEW_POST, title=i.title,
                               url=i.url, detail={"source": i.source})
                   for i in items if _is_recent(i.published_at) and not repo.exists(i)]
        repo.save_items(items)  # 오래된 글도 저장은 한다 (급증 감지의 '평소' 비교 대상)
    repo.save_events(events)
    collectors.raise_if_failed(f"SNS 수집 일부 실패 (나머지 이벤트 {len(events)}건은 저장함)")
    return events


def _is_recent(published_at: datetime | None) -> bool:
    """올린 시각을 모르면 새 글로 본다 (놓치는 것보다 한 번 더 보는 편이 낫다)."""
    return published_at is None or published_at >= datetime.now() - timedelta(days=RECENT_DAYS)
