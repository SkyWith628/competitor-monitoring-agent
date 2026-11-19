"""[B] 중복 기사 제거 테스트 — 예시."""
from app.core.models import Brand, RawItem, SourceType
from app.media_monitor.dedup import dedup


def _news(url: str, title: str) -> RawItem:
    return RawItem(brand=Brand.UNIQLO, source_type=SourceType.NEWS, source="naver_news",
                   external_id=url, url=url, title=title)


def test_same_url_with_query_is_duplicate():
    items = [_news("https://news.example.com/a?utm=x", "유니클로 신규 매장"),
             _news("https://news.example.com/a", "유니클로 신규 매장 오픈")]
    assert len(dedup(items)) == 1


def test_similar_title_is_duplicate():
    items = [_news("https://a.com/1", "유니클로, 강남에 대형 매장 오픈"),
             _news("https://b.com/2", "유니클로 강남에 대형 매장 오픈")]
    assert len(dedup(items)) == 1
