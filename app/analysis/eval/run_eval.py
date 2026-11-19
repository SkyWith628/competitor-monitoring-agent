"""분류 정확도 평가: golden_classify.jsonl (사람이 라벨링한 50건) vs classify() 결과. 담당: C

실행: python -m app.analysis.eval.run_eval
"""
from __future__ import annotations

import json
from pathlib import Path

from app.analysis.classify import classify
from app.core.models import ChangeEvent

GOLDEN = Path(__file__).parent / "golden_classify.jsonl"


def main() -> None:
    rows = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line]
    events = [ChangeEvent(**r["event"]) for r in rows]
    predicted = classify(events)
    correct = sum(bool(p.category and p.category.value == r["category"]) for p, r in zip(predicted, rows))
    print(f"category accuracy: {correct}/{len(rows)}")


if __name__ == "__main__":
    main()
