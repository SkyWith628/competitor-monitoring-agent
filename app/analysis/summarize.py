"""② 브랜드별 주간 요약. 담당: C"""
from __future__ import annotations

from pathlib import Path

from app.analysis.llm_client import REPORT_MODEL, ask_json
from app.core.models import Brand, BrandSummary, ChangeEvent

PROMPT = (Path(__file__).parent / "prompts" / "summarize.md").read_text(encoding="utf-8")
TOP_N = 40  # 중요도순 상위 N건만 넘겨 비용 관리


def summarize(brand: Brand, events: list[ChangeEvent], stats: dict) -> BrandSummary:
    top = sorted(events, key=lambda e: e.importance or 0, reverse=True)[:TOP_N]
    # TODO(C): 입력 포맷 다듬기 (stats는 코드로 계산한 정확한 수치)
    user = f"brand={brand.value}\nstats={stats}\n" + "\n".join(e.model_dump_json() for e in top)
    return ask_json(REPORT_MODEL, PROMPT, user, BrandSummary)
