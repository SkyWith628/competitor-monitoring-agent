"""[C] 분류·요약·인사이트 단계 — ask_json 을 가짜로 바꿔 코드 쪽 규칙(검증·필터·재시도)을 확인한다."""
import json

import pytest

from app.analysis import classify as classify_mod
from app.analysis import cost, features, insight, summarize
from app.analysis.citations import keep_grounded
from app.core.models import Brand, BrandSummary, Category, ChangeEvent, ChangeType


def _ev(i, ct=ChangeType.NEWS, imp=None, cat=None, **detail):
    return ChangeEvent(id=i, brand=Brand.SPAO, change_type=ct, title=f"t{i}", url="u", detail=detail,
                       importance=imp, category=cat)


class FakeLLM:
    """ask_json 대용. replies 를 차례로 돌려주고, 받은 입력을 기록한다."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, model, system, user, schema, **kw):
        self.calls.append(user)
        return schema.model_validate(self.replies.pop(0))


# ---------- features ----------
def test_price_change_and_discount():
    down, up = _ev(1, ChangeType.PRICE_DOWN, before=20000, after=15000), _ev(2, ChangeType.PRICE_UP, before=100, after=110)
    assert features.price_change_pct(down) == -25.0 and features.discount_pct(down) == 25.0
    assert features.price_change_pct(up) == 10.0 and features.discount_pct(up) is None
    assert features.price_change_pct(_ev(3, ChangeType.PRICE_DOWN, before="F/W", after="세일")) is None


def test_spike_ratio_and_event_line():
    e = _ev(1, ChangeType.SNS_SPIKE, likes=18400, avg_likes=4100)
    row = json.loads(features.event_line(e))
    assert row["computed"] == {"spike_ratio": 4.5}
    assert "computed" not in json.loads(features.event_line(_ev(2)))


# ---------- citations ----------
def test_keep_grounded():
    s = ["가격 인하 [#1]", "근거 없음", "지어낸 근거 [#99]", "섞인 근거 [#1][#99]", "가격 인하 [#1]", "  묶음  [#1][#2] "]
    assert keep_grounded(s, {1, 2}) == ["가격 인하 [#1]", "묶음 [#1][#2]"]


# ---------- classify ----------
def test_classify_fills_labels_and_forces_irrelevant_importance(monkeypatch):
    fake = FakeLLM({"labels": [
        {"id": 1, "category": "가격전략", "importance": 4, "note": "n"},
        {"id": 2, "category": "무관", "importance": 3, "note": "n"},
        {"id": 77, "category": "협업", "importance": 5, "note": "입력에 없는 id"},
    ]})
    monkeypatch.setattr(classify_mod, "ask_json", fake)
    evs = classify_mod.classify([_ev(1, ChangeType.PRICE_DOWN, before=100, after=70), _ev(2)])
    assert (evs[0].category, evs[0].importance) == (Category.PRICING, 4)
    assert (evs[1].category, evs[1].importance) == (Category.IRRELEVANT, 1)
    assert '"price_change_pct": -30.0' in fake.calls[0]  # 할인율은 코드가 계산해 넣는다


def test_classify_retries_missing_ids_once(monkeypatch):
    fake = FakeLLM({"labels": [{"id": 1, "category": "신제품", "importance": 2, "note": "n"}]},
                   {"labels": []})
    monkeypatch.setattr(classify_mod, "ask_json", fake)
    evs = classify_mod.classify([_ev(1), _ev(2)])
    assert len(fake.calls) == 2 and '"id": 2' in fake.calls[1] and '"id": 1' not in fake.calls[1]
    assert evs[1].category is None  # 남은 것은 다음 실행에서 재시도


# ---------- summarize ----------
def test_summarize_skips_llm_when_no_events(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(summarize, "ask_json", fake)
    s = summarize.summarize(Brand.SPAO, [], {})
    assert s.highlights == [] and fake.calls == []


def test_summarize_drops_ungrounded_and_sets_event_ids(monkeypatch):
    fake = FakeLLM({"highlights": ["패딩 40% 인하 [#2]", "추측 문장", "없는 근거 [#9]", "신상 2종 [#1][#2]"]})
    monkeypatch.setattr(summarize, "ask_json", fake)
    s = summarize.summarize(Brand.SPAO, [_ev(1, imp=2, cat=Category.PRODUCT), _ev(2, imp=4, cat=Category.PRICING)],
                            {"price_down": 1})
    assert s.highlights == ["패딩 40% 인하 [#2]", "신상 2종 [#1][#2]"]
    assert s.event_ids == [1, 2] and s.brand == Brand.SPAO
    assert fake.calls[0].index('"id": 2') < fake.calls[0].index('"id": 1')  # 중요도순 입력


# ---------- insight ----------
def test_insight_skips_llm_when_no_highlights(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(insight, "ask_json", fake)
    res = insight.generate([BrandSummary(brand=Brand.SPAO, highlights=[], event_ids=[])], None)
    assert res.tldr == "" and fake.calls == []


def test_insight_filters_and_caps(monkeypatch):
    fake = FakeLLM({"tldr": " 이번 주는\n할인 ", "insights": ["동시 할인 [#1][#5]", "근거 없음", "지어냄 [#8]",
                                                            "a [#1]", "b [#5]", "c [#1]"],
                    "actions": ["x", " ", "y", "z", "w"]})
    monkeypatch.setattr(insight, "ask_json", fake)
    sums = [BrandSummary(brand=Brand.SPAO, highlights=["h [#1]"], event_ids=[1]),
            BrandSummary(brand=Brand.UNIQLO, highlights=["h [#5]"], event_ids=[5]),
            BrandSummary(brand=Brand.EIGHT_SECONDS, highlights=[], event_ids=[])]
    res = insight.generate(sums, "지난주 요약")
    assert res.tldr == "이번 주는 할인"
    assert res.insights == ["동시 할인 [#1][#5]", "a [#1]", "b [#5]"]
    assert res.actions == ["x", "y", "z"]
    assert "8seconds" not in fake.calls[0] and "지난주 요약" in fake.calls[0]


# ---------- cost ----------
def test_cost_estimate():
    usage = [{"model": "claude-haiku-5-5", "calls": 3, "input_tokens": 1_000_000, "output_tokens": 200_000},
             {"model": "claude-opus-5-5", "calls": 5, "input_tokens": 100_000, "output_tokens": 10_000},
             {"model": "unknown-model", "calls": 1, "input_tokens": 5, "output_tokens": 5}]
    assert cost.estimate_usd(usage) == pytest.approx(0.10 + 0.10 + 0.40 + 0.20)
    assert cost.estimate_usd([]) is None
