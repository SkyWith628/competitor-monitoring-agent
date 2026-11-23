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
    # 이미 저장된 기사를 앞으로: 같은 소식의 새 기사가 와도 저장된 기사가 대표로 남아 다시 이벤트가 되지 않는다
    # (구글은 매일 최근 7일을 다시 가져오므로, 이어지는 기사가 대표가 되면 같은 소식이 날마다 새로 올라옴)
    # 그다음은 제목에 브랜드명이 있는 기사를 앞으로: 같은 소식이면 '[식음료 소식] KT&G·풀무원·농심' 같은
    # 묶음 기사보다 '농심, 무신사 스탠다드와 새우깡 티셔츠' 같은 제목이 대표가 된다
    keywords = [k.lower() for k in cfg["keywords"]]
    items.sort(key=lambda i: (not repo.exists(i), not any(k in i.title.lower() for k in keywords)))  # 안정 정렬
    new_items = [i for i in dedup(items, ignore=cfg["keywords"]) if not repo.exists(i)]
    events = [ChangeEvent(brand=brand, change_type=ChangeType.NEWS, title=i.title, url=i.url,
                          detail={"source": i.source}) for i in new_items]
    repo.save_items(new_items)
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
    failures: list[str] = []
    not_implemented = 0
    collectors = (InstagramCollector, YoutubeCollector)
    for cls in collectors:
        collector = cls(brand, cfg)
        try:
            items = collector.collect()
        except NotImplementedError:
            not_implemented += 1
            continue
        except Exception as e:  # noqa: BLE001 — 한 수집기 실패가 다른 수집기를 멈추지 않게
            failures.append(f"{collector.source}: {e!r}")
            continue
        history = repo.recent_posts(brand, source=collector.source, limit=30)
        events += detect_spikes(items, history)
        events += [ChangeEvent(brand=brand, change_type=ChangeType.SNS_NEW_POST, title=i.title,
                               url=i.url) for i in items if not repo.exists(i)]
        repo.save_items(items)
    repo.save_events(events)
    if failures:
        raise RuntimeError(f"SNS 수집 일부 실패 (나머지 이벤트 {len(events)}건은 저장함): "
                           + "; ".join(failures))
    if not_implemented == len(collectors):
        raise NotImplementedError  # 전부 구현 전이면 CLI가 '아직 구현되지 않음'으로 표시
    return events
