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


# ── 리뷰(#6)에서 나온 경계 사례 ─────────────────────────────────────────
def _titles(brand: Brand, titles: list[str]) -> list[RawItem]:
    return [RawItem(brand=brand, source_type=SourceType.NEWS, source="google_news", external_id=str(i),
                    url=f"https://news{i}.example.com/a", title=t) for i, t in enumerate(titles)]


def test_same_frame_different_place_is_kept():
    items = _titles(Brand.MUSINSA_STANDARD, ["무신사 스탠다드, 제주 첫 매장 오픈", "무신사 스탠다드, 부산 첫 매장 오픈"])
    assert len(dedup(items, ignore=MUSINSA)) == 2


def test_different_numbers_are_kept():
    items = _titles(Brand.SPAO, ["스파오, 3분기 매출 12% 증가", "스파오, 4분기 매출 12% 감소"])
    assert len(dedup(items, ignore=["스파오", "SPAO"])) == 2


UNIQLO_TITLES = ["유니클로 신상 출시",
                 "유니클로, 히트텍 겨울 신상 출시…라인업 확대",
                 "유니클로 키즈 신상 출시 기념 팝업 연다"]


def test_short_title_does_not_swallow_other_stories():
    # 브랜드명을 빼면 "신상 출시"만 남는 제목이 그 말이 들어간 다른 기사들을 삼키면 안 됨
    items = _titles(Brand.UNIQLO, UNIQLO_TITLES)
    assert len(dedup(items, ignore=["유니클로", "UNIQLO"])) == 3


def test_result_does_not_depend_on_order():
    forward = dedup(_titles(Brand.UNIQLO, UNIQLO_TITLES), ignore=["유니클로"])
    backward = dedup(_titles(Brand.UNIQLO, UNIQLO_TITLES[::-1]), ignore=["유니클로"])
    assert len(forward) == len(backward)


# ── 실데이터(2026-11-23 구글 뉴스): 표현만 다른 같은 소식은 계속 합쳐져야 함 ─────────────
# 리뷰 경계 사례를 막느라 기준을 너무 엄격하게 하면 이런 기사들이 따로 남아 리포트에 같은 소식이 반복된다
def test_real_paraphrases_of_same_story_are_merged():
    musinsa = ["무신사 스탠다드, 제주 첫 매장 연다…국내 45번째 점포",
               "무신사 스탠다드, 제주 첫 매장 개점…전국 45번째 점포",
               "농심, 무신사 스탠다드와 새우깡 55주년 기념 티셔츠 협업",
               "농심, 무신사 스탠다드와 '새우깡' 55주년 기념 '커스텀 티셔츠' 선봬",
               "농심 새우깡, 무신사 스탠다드와 협업…55주년 기념 커스텀 티셔츠 출시"]
    assert len(dedup(_titles(Brand.MUSINSA_STANDARD, musinsa), ignore=MUSINSA)) == 2

    uniqlo = ["유니클로, 저소득 독거노인에 히트텍 7만장 기부…11억원 규모",
              "유니클로, 국내 저소득층 독거노인에 히트텍 7만장 기부",
              "유니클로, 독거노인 3만5000명에 히트텍 7만 장 지원",
              "유니클로, 독거노인 3만5천 명 '히트텍' 7만 장 지원",  # '3만5천' = '3만5000'
              "희망브리지·유니클로, 거제시 감사패 수상",
              "희망브리지·유니클로, 거제시 수해 복구·재난 예방 공로 감사패"]
    assert len(dedup(_titles(Brand.UNIQLO, uniqlo), ignore=["유니클로", "UNIQLO"])) == 3
