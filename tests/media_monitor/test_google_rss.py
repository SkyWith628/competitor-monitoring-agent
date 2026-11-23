"""[B] Google News RSS 수집기 테스트 — 인터넷 없이 가짜 기사 목록으로."""
import pytest

from app.core.models import Brand, SourceType
from app.media_monitor.news.google_rss import RSS_LIMIT, GoogleNewsCollector, _strip_press

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
    assert len(items) == 2  # 블로그 글, 제목에 브랜드 없는 기사는 빠지고 2건
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
    """구글은 요약이 없어 제목만으로 판단 — 네이버와 같은 공통 기준(rules.py)."""
    titles = [i.title for i in _collector().collect()]
    assert "파자마가 뜬다" not in titles


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


def test_press_name_is_stripped_only_when_it_matches():
    assert _strip_press("스파오, 겨울 신상 출시 - 머니투데이", "머니투데이") == "스파오, 겨울 신상 출시"
    # 언론사 이름이 안 맞으면 제목을 그대로 둔다 (예전엔 마지막 ' - ' 뒤를 지워서 '스파오'만 남았음)
    assert _strip_press("스파오 - 산리오 협업 컬렉션 공개", None) == "스파오 - 산리오 협업 컬렉션 공개"
    assert _strip_press("스파오 - 산리오 협업 컬렉션 공개", "연합뉴스") == "스파오 - 산리오 협업 컬렉션 공개"


def _feed(n: int) -> str:
    items = "".join(f"<item><title>스파오 소식 {i} - 언론</title><link>https://news.google.com/{i}</link>"
                    f'<source url="https://press.example.com">언론</source></item>' for i in range(n))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>q</title>{items}</channel></rss>'


def test_warns_when_feed_hits_rss_limit(caplog):
    c = _collector()
    c._fetch = lambda query: _feed(RSS_LIMIT)
    assert len(c.collect()) == RSS_LIMIT  # 수집은 그대로 하고
    assert "RSS 한도" in caplog.text      # 잘렸을 수 있다고 경고만 남긴다


def test_no_warning_below_rss_limit(caplog):
    c = _collector()
    c._fetch = lambda query: _feed(RSS_LIMIT - 1)
    c.collect()
    assert "RSS 한도" not in caplog.text


def test_minor_feed_problem_with_articles_is_not_an_error():
    """정의 안 된 엔티티 하나로 feedparser 가 bozo 를 켜도, 기사가 파싱됐으면 수집한다."""
    c = _collector()
    c._fetch = lambda query: SAMPLE.replace("<channel>", "<channel><title>a &nbsp; b</title>")
    assert len(c.collect()) == 2
