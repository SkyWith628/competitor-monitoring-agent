"""③ 크로스 브랜드 인사이트 + 액션 제안. 담당: C"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from app.analysis.llm_client import REPORT_MODEL, ask_json
from app.core.models import BrandSummary

PROMPT = (Path(__file__).parent / "prompts" / "insight.md").read_text(encoding="utf-8")


class Insight(BaseModel):
    tldr: str
    insights: list[str]
    actions: list[str]


def generate(summaries: list[BrandSummary], last_week_tldr: str | None) -> Insight:
    user = "\n".join(s.model_dump_json() for s in summaries)
    if last_week_tldr:
        user += f"\n\n[지난주 요약]\n{last_week_tldr}"
    return ask_json(REPORT_MODEL, PROMPT, user, Insight, effort="high")
