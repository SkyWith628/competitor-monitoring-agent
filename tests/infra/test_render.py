"""[D] 리포트 렌더링 테스트 — 예시."""
from app.core.models import Brand, BrandSummary, WeeklyReport
from app.infra.notify.render import to_html, to_markdown, to_slack_blocks


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


def test_markdown_has_sections_and_highlights():
    md = to_markdown(_report())
    assert md.startswith("# 📊 경쟁사 주간 리포트 2026-W47")
    assert "### 유니클로\n- 예시 [#1]" in md and "- 액션" in md
