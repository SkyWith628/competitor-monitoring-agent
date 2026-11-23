"""② 브랜드별 주간 요약. 담당: C"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from app.analysis.citations import cited_ids, keep_grounded
from app.analysis.features import event_line
from app.analysis.llm_client import REPORT_MODEL, ask_json
from app.core.models import Brand, BrandSummary, ChangeEvent

PROMPT = (Path(__file__).parent / "prompts" / "summarize.md").read_text(encoding="utf-8")
TOP_N = 40  # 중요도순 상위 N건만 넘겨 비용 관리
MAX_HIGHLIGHTS = 5


class _Highlights(BaseModel):
    highlights: list[str]


def pick_top(events: list[ChangeEvent], n: int = TOP_N) -> list[ChangeEvent]:
    """중요도 높은 순, 같으면 최근 감지 순."""
    return sorted(events, key=lambda e: (e.importance or 0, e.detected_at), reverse=True)[:n]


def build_input(brand: Brand, events: list[ChangeEvent], stats: dict) -> str:
    return (
        f"[브랜드] {brand.value}\n"
        f"[통계 — 코드로 계산한 정확한 수치]\n{json.dumps(stats, ensure_ascii=False)}\n"
        f"[이벤트 — 중요도 높은 순, {len(events)}건]\n" + "\n".join(event_line(e, with_labels=True) for e in events)
    )


def summarize(brand: Brand, events: list[ChangeEvent], stats: dict) -> BrandSummary:
    """브랜드의 이번 주 핵심 변화 3~5문장. 근거 인용이 없거나 틀린 문장은 뺀다.

    이벤트가 없으면 LLM을 부르지 않고 빈 요약을 돌려준다 (렌더러가 그 브랜드를 생략한다).
    """
    top = pick_top(events)
    if not top:
        return BrandSummary(brand=brand, highlights=[], event_ids=[])
    result = ask_json(REPORT_MODEL, PROMPT, build_input(brand, top, stats), _Highlights)
    highlights = keep_grounded(result.highlights, {e.id for e in top if e.id is not None})[:MAX_HIGHLIGHTS]
    ids = sorted(set().union(*(cited_ids(h) for h in highlights))) if highlights else []
    return BrandSummary(brand=brand, highlights=highlights, event_ids=ids)
