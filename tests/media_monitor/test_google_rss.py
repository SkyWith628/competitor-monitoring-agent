"""[B] Google News RSS 수집기 테스트 — 인터넷 없이 가짜 기사 목록으로."""
import pytest

from app.core.models import Brand, SourceType
from app.media_monitor.news.google_rss import GoogleNewsCollector

SAMPLE = """<?xml version="1.0"?><rss><channel>
<item><title>스파오, 겨울 신상 출시 - 머니투데이</title><link>https://news.google.com/a</link>
<pubDate>Wed, 18 Nov 2026 02:00:00 GMT</pubDate><source url="https://mt.co.kr">머니투데이</source></item>
<item><title>스파오 인천 공장 화재 - 연합뉴스</title><link>https://news.google.com/b</link>
<pubDate>Wed, 18 Nov 2026 03:00:00 GMT</pubDate><source url="https://yna.co.kr">연합뉴스</source></item>
<item><title>파자마가 뜬다 - 헤럴드경제</title><link>https://news.google.com/d</link>
<pubDate>Wed, 18 Nov 2026 05:00:00 GMT</pubDate><source url="https://biz.heraldcorp.com">헤럴드경제</source></item>
<item><title>스파오 잠옷 후기 : 네이버 블로그</title><link>https://news.google.com/c</link>
<pubDate>Wed, 18 Nov 2026 04:00:00 GMT</pubDate><source url="https://blog.naver.com">Naver Blog</source></item>
</channel></rss>"""


def _collector(exclude=None):
    c = GoogleNewsCollector(Brand.SPAO, {"keywords": ["스파오"], "exclude": exclude or []})
    c._fetch = lambda query: SAMPLE  # 진짜 구글 대신 가짜 기사 목록을 돌려줌
    return c


def test_parses_items_and_strips_press_name():
    items = _collector().collect()
    assert len(items) == 2  # 블로그 글·제목에 브랜드 없는 기사는 빠지고 2건만
    assert items[0].title == "스파오, 겨울 신상 출시"
    assert items[0].source_type == SourceType.NEWS
    assert items[0].published_at is not None


def test_exclude_keyword_filters_out():
    items = _collector(exclude=["화재"]).collect()
    assert [i.title for i in items] == ["스파오, 겨울 신상 출시"]


def test_non_news_sites_are_skipped():
    titles = [i.title for i in _collector().collect()]
    assert not any("블로그" in t for t in titles)


def test_title_without_brand_is_skipped():
    titles = [i.title for i in _collector().collect()]
    assert "파자마가 뜬다" not in titles


def test_keyword_match_ignores_case():
    c = GoogleNewsCollector(Brand.SPAO, {"keywords": ["SPAO"], "exclude": []})
    c._fetch = lambda query: SAMPLE.replace("스파오, 겨울", "spao, 겨울")
    assert [i.title for i in c.collect()] == ["spao, 겨울 신상 출시"]


def test_html_instead_of_rss_raises():
    """구글이 동의·차단 페이지(HTML)를 200으로 주면 조용히 0건이 아니라 오류로 알린다."""
    c = _collector()
    c._fetch = lambda query: "<html><body>consent</body></html>"
    with pytest.raises(RuntimeError, match="RSS가 아닙니다"):
        c.collect()


def test_valid_feed_with_no_articles_is_ok():
    """새 기사가 없는 날(정상 RSS, 기사 0건)은 오류가 아니다."""
    c = _collector()
    c._fetch = lambda query: '<?xml version="1.0"?><rss version="2.0"><channel><title>q</title></channel></rss>'
    assert c.collect() == []
