"""[C] LLM 호출 없이 테스트 가능한 부분. LLM 응답은 가짜로 바꿔 끼운다. 실제 품질은 eval/ 로 평가."""
from app.analysis import cost, insight, report_builder, summarize
from app.analysis.citations import CITATION
from app.analysis.insight import Insight
from app.core.models import Brand, BrandSummary, Category, ChangeEvent, ChangeType


def _ev(i, brand=Brand.SPAO, ct=ChangeType.PRICE_DOWN, cat=Category.PRICING, imp=3, **detail):
    return ChangeEvent(id=i, brand=brand, change_type=ct, title=f"t{i}", url=f"u{i}", detail=detail,
                       category=cat, importance=imp)


def test_compute_stats_counts_by_brand_and_type():
    events = [ChangeEvent(brand=Brand.SPAO, change_type=ChangeType.PRICE_DOWN, title="t", url="u"),
              ChangeEvent(brand=Brand.SPAO, change_type=ChangeType.PRICE_DOWN, title="t", url="u")]
    assert report_builder.compute_stats(events) == {"spao": {"price_down": 2}}


def test_compute_stats_discounts():
    events = [_ev(1, before=10000, after=8000), _ev(2, before=10000, after=6000),
              _ev(3, ct=ChangeType.PRICE_UP, before=10000, after=11000)]
    s = report_builder.compute_stats(events)["spao"]
    assert s == {"price_down": 2, "price_up": 1, "평균 할인율(%)": 30.0, "최대 할인율(%)": 40.0}


def test_citation_pattern():
    assert CITATION.search("히트텍 가격 인하 [#12]")
    assert not CITATION.search("근거 없는 문장")


def test_reportable_drops_irrelevant_and_unclassified():
    evs = [_ev(1), _ev(2, cat=Category.IRRELEVANT), ChangeEvent(brand=Brand.SPAO, change_type=ChangeType.NEWS,
                                                               title="t", url="u")]
    assert [e.id for e in report_builder.reportable(evs)] == [1]


def test_build_assembles_report(monkeypatch):
    week_events = [_ev(1, imp=5), _ev(2, brand=Brand.UNIQLO, imp=2), _ev(3, cat=Category.IRRELEVANT, imp=1)]
    seen = {}

    def fake_summarize(brand, events, stats):
        seen[brand] = [e.id for e in events]
        ids = [e.id for e in events]
        return BrandSummary(brand=brand, highlights=[f"변화 [#{i}]" for i in ids], event_ids=ids)

    monkeypatch.setattr(report_builder.repo, "events_of_week", lambda w: week_events)
    monkeypatch.setattr(report_builder.repo, "last_report_tldr", lambda w: None)
    monkeypatch.setattr(summarize, "summarize", fake_summarize)
    monkeypatch.setattr(insight, "generate", lambda s, t: Insight(tldr="요약", insights=["흐름 [#1]"], actions=["a"]))
    monkeypatch.setattr(cost, "weekly_cost", lambda: 0.42)

    r = report_builder.build("2026-W47")
    assert seen[Brand.SPAO] == [1] and seen[Brand.UNIQLO] == [2] and seen[Brand.MUSINSA_STANDARD] == []
    assert [e.id for e in r.top_events] == [1, 2]  # 무관 제외, 중요도순
    assert r.stats[cost.COST_KEY] == 0.42
    assert r.tldr == "요약" and r.actions == ["a"]
