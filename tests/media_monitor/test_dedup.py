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


MUSINSA = ["무신사 스탠다드", "무신사스탠다드"]


def _musinsa(i: int, title: str) -> RawItem:
    return RawItem(brand=Brand.MUSINSA_STANDARD, source_type=SourceType.NEWS, source="google_news",
                   external_id=str(i), url=f"https://news{i}.example.com/a", title=title)


def test_same_story_with_different_wording_is_duplicate():
    # 실제 수집된 제목들 — 언론사마다 표현만 다른 같은 소식
    titles = ["무신사 스탠다드, 제주 첫 매장",
              "무신사 스탠다드, 제주 첫 매장 연다…전국 45번째",
              "무신사 스탠다드, 제주 첫 매장 오픈…전국 오프라인 거점 확대"]
    items = [_musinsa(i, t) for i, t in enumerate(titles)]
    assert len(dedup(items, ignore=MUSINSA)) == 1


def test_different_stories_of_same_brand_are_kept():
    titles = ["무신사 스탠다드, 제주 첫 매장 오픈",
              "무신사 스탠다드, 상반기 일본 매출 7배",
              "농심, 무신사 스탠다드와 새우깡 55주년 기념 티셔츠"]
    items = [_musinsa(i, t) for i, t in enumerate(titles)]
    assert len(dedup(items, ignore=MUSINSA)) == 3


def test_very_short_core_is_not_over_merged():
    # 브랜드명을 빼면 "매장"만 남는 제목이 다른 매장 기사를 삼키면 안 됨
    items = [_musinsa(0, "무신사 스탠다드 매장"), _musinsa(1, "무신사 스탠다드, 강남 신규 매장 오픈")]
    assert len(dedup(items, ignore=MUSINSA)) == 2
