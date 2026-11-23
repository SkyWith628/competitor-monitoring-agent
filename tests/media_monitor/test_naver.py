"""[B] 네이버 뉴스 수집기 테스트 — 인터넷·API 키 없이 가짜 응답으로."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from app.core.models import Brand, SourceType
from app.media_monitor.news.naver import NaverNewsCollector


def _ago(days: float) -> str:
    """며칠 전 시각을 네이버 pubDate 형식으로 ('Wed, 18 Nov 2026 11:00:00 +0900')
    — 날짜를 고정해 두면 시간이 지나 7일 필터에 걸려 테스트가 깨지므로 '지금 기준'으로 만든다."""
    return format_datetime(datetime.now(timezone(timedelta(hours=9))) - timedelta(days=days))


# 네이버 검색 API가 돌려주는 items 모양 그대로 (제목에 <b> 태그, &quot; 같은 HTML 문자 포함)
FAKE = {
    "스파오": [
        {"title": "<b>스파오</b>, 겨울 &quot;웜테크&quot; 출시", "originallink": "https://mt.co.kr/1",
         "link": "https://n.news.naver.com/1", "description": "<b>스파오</b>가 신상을 냈다",
         "pubDate": _ago(1)},
        {"title": "<b>스파오</b> 인천 공장 화재", "originallink": "https://yna.co.kr/2",
         "link": "https://n.news.naver.com/2", "description": "화재 소식",
         "pubDate": _ago(2)},
        {"title": "<b>스파오</b> 여름 세일", "originallink": "https://old.co.kr/9",  # 7일 넘은 기사
         "link": "https://n.news.naver.com/9", "description": "", "pubDate": _ago(30)},
    ],
    "SPAO": [
        # 위와 같은 기사 → 한 번만 들어가야 함
        {"title": "<b>SPAO</b>, 겨울 &quot;웜테크&quot; 출시", "originallink": "https://mt.co.kr/1",
         "link": "https://n.news.naver.com/1", "description": "",
         "pubDate": _ago(1)},
    ],
}


def _collector(exclude=None):
    c = NaverNewsCollector(Brand.SPAO, {"keywords": ["스파오", "SPAO"], "exclude": exclude or []})
    c._fetch = lambda keyword: FAKE[keyword]  # 진짜 네이버 대신 가짜 응답
    return c


def test_cleans_html_and_skips_same_url():
    items = _collector().collect()
    assert len(items) == 2  # SPAO 검색의 같은 기사, 30일 전 기사는 빠짐
    assert items[0].title == '스파오, 겨울 "웜테크" 출시'  # <b> 태그·&quot; 정리됨
    assert items[0].url == "https://mt.co.kr/1"  # 언론사 원문 주소
    assert items[0].source_type == SourceType.NEWS
    assert items[0].published_at is not None


def test_brand_nowhere_is_skipped():
    """제목·요약 어디에도 브랜드가 없는 기사(본문 깊숙이 스친 기사·스팸)는 버림 — 구글과 같은 기준."""
    c = _collector()
    c._fetch = lambda keyword: [{"title": "8 Myths About Watches", "originallink": "https://x.com/1",
                                 "link": "https://x.com/1", "description": "Watches are ...",
                                 "pubDate": _ago(1)},
                                {"title": "블프에 크리스마스까지", "originallink": "https://y.com/2",
                                 "link": "https://y.com/2", "description": "<b>스파오</b>는 29일까지 블프",
                                 "pubDate": _ago(1)}]
    assert [i.title for i in c.collect()] == ["블프에 크리스마스까지"]  # 요약에 브랜드 있으면 통과


def test_older_than_7_days_is_skipped():
    titles = [i.title for i in _collector().collect()]
    assert "스파오 여름 세일" not in titles


def test_exclude_keyword_filters_out():
    items = _collector(exclude=["화재"]).collect()
    assert [i.title for i in items] == ['스파오, 겨울 "웜테크" 출시']
