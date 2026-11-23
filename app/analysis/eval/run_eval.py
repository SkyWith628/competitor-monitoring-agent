"""분류 정확도 평가: golden_classify.jsonl (사람이 라벨링) vs classify() 결과. 담당: C

실행: python -m app.analysis.eval.run_eval [--json data/eval/결과.json]

출력 맨 위의 prompt 해시로 "어느 프롬프트 버전의 점수인지" 구분한다.
--json 을 주면 결과를 파일로 남겨 프롬프트 수정 전후를 비교할 수 있다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from app.analysis.eval.metrics import evaluate, format_report
from app.core.models import ChangeEvent

GOLDEN = Path(__file__).parent / "golden_classify.jsonl"
PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "classify.md"


def load_golden() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]


def prompt_version() -> str:
    return hashlib.sha256(PROMPT_FILE.read_bytes()).hexdigest()[:8]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="분류 골든셋 평가")
    ap.add_argument("--json", type=Path, help="결과를 저장할 JSON 경로")
    args = ap.parse_args(argv)

    # API 키가 필요하므로 실행 시점에 import
    from app.analysis.classify import classify
    from app.analysis.llm_client import CLASSIFY_MODEL

    rows = load_golden()
    events = [ChangeEvent(**r["event"]) for r in rows]
    predicted = {e.id: e for e in classify(events) if e.category}
    res = evaluate(rows, predicted)

    print(f"model={CLASSIFY_MODEL}  prompt={prompt_version()}  golden={len(rows)}건\n")
    print(format_report(res))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        out = {"run_at": datetime.now().isoformat(timespec="seconds"), "model": CLASSIFY_MODEL,
               "prompt_version": prompt_version(), **asdict(res)}
        args.json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장: {args.json}")


if __name__ == "__main__":
    main()
