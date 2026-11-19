"""[D] 리포트 렌더링 테스트 — 예시."""
from app.core.models import Brand, BrandSummary, WeeklyReport
from app.infra.notify.render import to_html, to_slack_blocks


def _report() -> WeeklyReport:
    return WeeklyReport(week="2026-W47", tldr="테스트 요약", top_events=[],
                        brand_summaries=[BrandSummary(brand=Brand.UNIQLO, highlights=["예시 [#1]"],
                                                      event_ids=[1])],
                        insights=["인사이트"], actions=["액션"])


def test_html_contains_tldr_and_brand():
    html = to_html(_report())
    assert "테스트 요약" in html and "유니클로" in html


def test_slack_blocks_have_header():
    assert to_slack_blocks(_report())[0]["type"] == "header"
