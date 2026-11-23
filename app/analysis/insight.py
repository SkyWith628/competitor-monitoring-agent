"""③ 크로스 브랜드 인사이트 + 액션 제안. 담당: C"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from app.analysis.citations import keep_grounded
from app.analysis.llm_client import REPORT_MODEL, ask_json
from app.core.models import BrandSummary

PROMPT = (Path(__file__).parent / "prompts" / "insight.md").read_text(encoding="utf-8")
MAX_INSIGHTS = 3
MAX_ACTIONS = 3


class Insight(BaseModel):
    tldr: str
    insights: list[str]
    actions: list[str]


def build_input(summaries: list[BrandSummary], last_week_tldr: str | None) -> str:
    parts = []
    for s in summaries:
        parts.append(f"[{s.brand.value}]\n" + "\n".join(f"- {h}" for h in s.highlights))
    text = "[이번 주 브랜드별 요약]\n" + "\n\n".join(parts)
    text += f"\n\n[지난주 리포트 한 줄 요약]\n{last_week_tldr or '(없음 — 첫 리포트)'}"
    return text


def generate(summaries: list[BrandSummary], last_week_tldr: str | None) -> Insight:
    """요약이 하나도 없으면 LLM을 부르지 않는다 (빈 tldr → 렌더러가 '변화 없음' 문구를 쓴다)."""
    filled = [s for s in summaries if s.highlights]
    if not filled:
        return Insight(tldr="", insights=[], actions=[])
    res = ask_json(REPORT_MODEL, PROMPT, build_input(filled, last_week_tldr), Insight, effort="high")
    valid = {i for s in filled for i in s.event_ids}
    return Insight(
        tldr=" ".join(res.tldr.split()),
        insights=keep_grounded(res.insights, valid)[:MAX_INSIGHTS],
        actions=[a.strip() for a in res.actions if a.strip()][:MAX_ACTIONS],
    )
