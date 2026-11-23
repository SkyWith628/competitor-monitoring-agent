"""①~③ 결과를 WeeklyReport로 조립. 담당: C"""
from __future__ import annotations

from app.analysis import cost, insight, summarize
from app.analysis.features import discount_pct
from app.core.models import Brand, Category, ChangeEvent, WeeklyReport
from app.infra.storage import repo

TOP_EVENTS = 5
AVG_DISCOUNT = "평균 할인율(%)"
MAX_DISCOUNT = "최대 할인율(%)"


def compute_stats(events: list[ChangeEvent]) -> dict:
    """LLM 대신 코드로 계산하는 수치.

    형태: {"spao": {"price_down": 3, "new_product": 12, "평균 할인율(%)": 24.3, "최대 할인율(%)": 40.0}, ...}
    변화 유형 건수는 D의 렌더러가 표로, 그 밖의 키는 '그 밖의 지표'로 그린다 (전주 대비 증감은 렌더러가 계산).
    """
    stats: dict[str, dict] = {}
    discounts: dict[str, list[float]] = {}
    for e in events:
        brand_stats = stats.setdefault(e.brand.value, {})
        brand_stats[e.change_type.value] = brand_stats.get(e.change_type.value, 0) + 1
        if (d := discount_pct(e)) is not None:
            discounts.setdefault(e.brand.value, []).append(d)
    for brand, ds in discounts.items():
        stats[brand][AVG_DISCOUNT] = round(sum(ds) / len(ds), 1)
        stats[brand][MAX_DISCOUNT] = max(ds)
    return stats


def reportable(events: list[ChangeEvent]) -> list[ChangeEvent]:
    """분류가 끝났고 '무관'이 아닌 이벤트만."""
    return [e for e in events if e.category and e.category != Category.IRRELEVANT]


def build(week: str) -> WeeklyReport:
    events = reportable(repo.events_of_week(week))
    stats = compute_stats(events)
    summaries = [
        summarize.summarize(brand, [e for e in events if e.brand == brand], stats.get(brand.value, {}))
        for brand in Brand
    ]
    ins = insight.generate(summaries, repo.last_report_tldr(week))
    if (usd := cost.weekly_cost()) is not None:  # 이번 리포트 생성 호출까지 포함
        stats[cost.COST_KEY] = usd
    return WeeklyReport(week=week, tldr=ins.tldr, top_events=summarize.pick_top(events, TOP_EVENTS),
                        brand_summaries=summaries, insights=ins.insights, actions=ins.actions, stats=stats)
