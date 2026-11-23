"""[B] 뉴스 공통 관련성 기준 테스트."""
from app.media_monitor.news.rules import has_excluded, mentions_brand

KW = ["스파오", "SPAO"]


def test_brand_in_title():
    assert mentions_brand(KW, "스파오, 겨울 신상 출시")


def test_brand_only_in_summary_is_kept():
    assert mentions_brand(KW, "블프에 크리스마스까지… 유통가", "스파오는 29일까지 블랙프라이데이 프로모션을")


def test_brand_nowhere_is_dropped():
    assert not mentions_brand(["에잇세컨즈", "8seconds"], "8 Myths About Watches", "Watches are ...")


def test_brand_match_ignores_case():
    assert mentions_brand(KW, "spao 겨울 신상")


def test_excluded_word_in_title_or_summary():
    assert has_excluded(["화재"], "스파오 인천 공장 화재")
    assert has_excluded(["화재"], "스파오 소식", "인천 공장에서 화재가")
    assert not has_excluded(["화재"], "스파오 겨울 신상", None)
