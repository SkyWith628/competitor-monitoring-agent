"""[B] Google News RSS 수집기 테스트 — 인터넷 없이 가짜 기사 목록으로."""
from app.core.models import Brand, SourceType
from app.media_monitor.news.google_rss import GoogleNewsCollector

SAMPLE = """<?xml version="1.0"?><rss><channel>
<item><title>스파오, 겨울 신상 출시 - 머니투데이</title><link>https://news.google.com/a</link>
<pubDate>Wed, 18 Nov 2026 02:00:00 GMT</pubDate><source url="https://mt.co.kr">머니투데이</source></item>
<item><title>스파오 인천 공장 화재 - 연합뉴스</title><link>https://news.google.com/b</link>
<pubDate>Wed, 18 Nov 2026 03:00:00 GMT</pubDate><source url="https://yna.co.kr">연합뉴스</source></item>
<item><title>스파오 잠옷 후기 : 네이버 블로그</title><link>https://news.google.com/c</link>
<pubDate>Wed, 18 Nov 2026 04:00:00 GMT</pubDate><source url="https://blog.naver.com">Naver Blog</source></item>
</channel></rss>"""


def _collector(exclude=None):
    c = GoogleNewsCollector(Brand.SPAO, {"keywords": ["스파오"], "exclude": exclude or []})
    c._fetch = lambda query: SAMPLE  # 진짜 구글 대신 가짜 기사 목록을 돌려줌
    return c


def test_parses_items_and_strips_press_name():
    items = _collector().collect()
    assert len(items) == 2  # 블로그 글은 빠지고 뉴스 2건만
    assert items[0].title == "스파오, 겨울 신상 출시"
    assert items[0].source_type == SourceType.NEWS
    assert items[0].published_at is not None


def test_exclude_keyword_filters_out():
    items = _collector(exclude=["화재"]).collect()
    assert [i.title for i in items] == ["스파오, 겨울 신상 출시"]


def test_non_news_sites_are_skipped():
    titles = [i.title for i in _collector().collect()]
    assert not any("블로그" in t for t in titles)
