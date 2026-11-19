"""분류 정확도 평가: golden_classify.jsonl (사람이 라벨링) vs classify() 결과. 담당: C

실행: python -m app.analysis.eval.run_eval
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from app.core.models import ChangeEvent

GOLDEN = Path(__file__).parent / "golden_classify.jsonl"


def load_golden() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    from app.analysis.classify import classify  # API 키가 필요하므로 실행 시점에 import

    rows = load_golden()
    events = [ChangeEvent(**r["event"]) for r in rows]
    predicted = {e.id: e for e in classify(events)}

    cat_ok = imp_ok = imp_near = 0
    misses: Counter[tuple[str, str]] = Counter()
    for r in rows:
        p = predicted[r["event"]["id"]]
        got = p.category.value if p.category else "(없음)"
        if got == r["category"]:
            cat_ok += 1
        else:
            misses[(r["category"], got)] += 1
        if p.importance is not None:
            imp_ok += p.importance == r["importance"]
            imp_near += abs(p.importance - r["importance"]) <= 1

    n = len(rows)
    print(f"category accuracy : {cat_ok}/{n} ({cat_ok / n:.0%})")
    print(f"importance exact  : {imp_ok}/{n} ({imp_ok / n:.0%})")
    print(f"importance ±1     : {imp_near}/{n} ({imp_near / n:.0%})")
    for (want, got), c in misses.most_common():
        print(f"  오분류 {want} → {got}: {c}건")


if __name__ == "__main__":
    main()
