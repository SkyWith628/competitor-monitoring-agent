"""골든셋 자체의 형식 검증 (LLM 호출 없음)."""
from collections import Counter

from app.analysis.eval.run_eval import load_golden
from app.core.models import Category, ChangeEvent


def test_golden_rows_are_valid():
    rows = load_golden()
    ids = [r["event"]["id"] for r in rows]
    assert len(ids) == len(set(ids)), "event id 중복"
    for r in rows:
        ChangeEvent(**r["event"])
        Category(r["category"])
        assert 1 <= r["importance"] <= 5
        if r["category"] == Category.IRRELEVANT.value:
            assert r["importance"] == 1


def test_golden_covers_every_category():
    counts = Counter(r["category"] for r in load_golden())
    assert set(counts) == {c.value for c in Category}
    assert min(counts.values()) >= 3
