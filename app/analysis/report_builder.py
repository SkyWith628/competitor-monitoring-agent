"""①~③ 결과를 WeeklyReport로 조립. 담당: C"""
from __future__ import annotations

import re

from app.analysis import insight, summarize
from app.core.models import Brand, Category, ChangeEvent, WeeklyReport
from app.infra.storage import repo

CITATION = re.compile(r"\[#\d+\]")


def compute_stats(events: list[ChangeEvent]) -> dict:
    """LLM 대신 코드로 계산하는 수치. TODO(C): 평균 할인율, 전주 대비 증감 등 추가"""
    stats: dict[str, dict[str, int]] = {}
    for e in events:
        brand_stats = stats.setdefault(e.brand.value, {})
        brand_stats[e.change_type.value] = brand_stats.get(e.change_type.value, 0) + 1
    return stats


def build(week: str) -> WeeklyReport:
    events = [e for e in repo.events_of_week(week) if e.category and e.category != Category.IRRELEVANT]
    stats = compute_stats(events)
    summaries = []
    for brand in Brand:
        s = summarize.summarize(brand, [e for e in events if e.brand == brand],
                                stats.get(brand.value, {}))
        s.highlights = [h for h in s.highlights if CITATION.search(h)]  # 근거 없는 문장 제거
        summaries.append(s)
    ins = insight.generate(summaries, repo.last_report_tldr(week))
    top = sorted(events, key=lambda e: e.importance or 0, reverse=True)[:5]
    return WeeklyReport(week=week, tldr=ins.tldr, top_events=top, brand_summaries=summaries,
                        insights=ins.insights, actions=ins.actions, stats=stats)
