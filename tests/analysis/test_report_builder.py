"""[C] LLM 호출 없이 테스트 가능한 부분 — 예시. LLM 부분은 eval/ 로 평가."""
from app.analysis.report_builder import CITATION, compute_stats
from app.core.models import Brand, ChangeEvent, ChangeType


def test_compute_stats_counts_by_brand_and_type():
    events = [ChangeEvent(brand=Brand.SPAO, change_type=ChangeType.PRICE_DOWN, title="t", url="u"),
              ChangeEvent(brand=Brand.SPAO, change_type=ChangeType.PRICE_DOWN, title="t", url="u")]
    assert compute_stats(events) == {"spao": {"price_down": 2}}


def test_citation_pattern():
    assert CITATION.search("히트텍 가격 인하 [#12]")
    assert not CITATION.search("근거 없는 문장")
