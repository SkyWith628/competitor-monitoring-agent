"""[C] 골든셋 채점 로직 (LLM 호출 없음)."""
from app.analysis.eval.metrics import MISSING, evaluate, format_report
from app.analysis.eval.run_eval import load_golden
from app.core.models import Brand, Category, ChangeEvent, ChangeType


def _row(i, cat, imp):
    return {"event": {"id": i, "brand": "spao", "change_type": "news", "title": f"t{i}", "url": "u"},
            "category": cat, "importance": imp}


def _pred(i, cat, imp):
    return ChangeEvent(id=i, brand=Brand.SPAO, change_type=ChangeType.NEWS, title=f"t{i}", url="u",
                       category=Category(cat), importance=imp)


ROWS = [_row(1, "가격전략", 4), _row(2, "무관", 1), _row(3, "무관", 1), _row(4, "협업", 3)]


def test_perfect_prediction():
    res = evaluate(ROWS, {r["event"]["id"]: _pred(r["event"]["id"], r["category"], r["importance"]) for r in ROWS})
    assert (res.category_correct, res.importance_exact, res.missing, res.misses) == (4, 4, 0, [])
    assert res.irrelevant.recall == 1.0 and res.irrelevant.precision == 1.0


def test_irrelevant_filter_scores():
    # 무관 2건 중 1건을 놓치고(→마케팅), 협업 1건을 무관으로 잘못 버림
    pred = {1: _pred(1, "가격전략", 4), 2: _pred(2, "무관", 1), 3: _pred(3, "마케팅", 2), 4: _pred(4, "무관", 1)}
    res = evaluate(ROWS, pred)
    assert res.category_correct == 2
    assert res.irrelevant.recall == 0.5
    assert res.irrelevant.precision == 0.5
    assert res.per_category["협업"].recall == 0.0
    assert res.per_category["신제품"].precision is None  # 예측 0건
    assert [m.id for m in res.misses] == [3, 4]


def test_importance_near_and_missing_label():
    pred = {1: _pred(1, "가격전략", 3), 2: _pred(2, "무관", 1), 4: _pred(4, "협업", 5)}  # 3번 누락
    res = evaluate(ROWS, pred)
    assert res.missing == 1
    assert res.importance_exact == 1  # 2번만
    assert res.importance_near == 2  # 1번(4→3), 2번
    miss3 = next(m for m in res.misses if m.id == 3)
    assert miss3.got_category == MISSING and miss3.got_importance is None


def test_report_text_lists_categories_and_misses():
    text = format_report(evaluate(ROWS, {}))
    assert "category accuracy : 0/4" in text
    assert "무관" in text and "#1 t1" in text


def test_golden_has_target_size():
    assert len(load_golden()) >= 50  # DESIGN 6.3: 분류 정답 50건
