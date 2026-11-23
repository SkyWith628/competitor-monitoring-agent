"""WeeklyReport → HTML(메일) / Markdown / 슬랙 Block Kit. 담당: D"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.models import WeeklyReport

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
)

BRAND_LABEL = {"spao": "스파오", "8seconds": "에잇세컨즈", "musinsa_standard": "무신사 스탠다드", "uniqlo": "유니클로"}


def to_html(report: WeeklyReport) -> str:
    return _env.get_template("weekly.html.j2").render(r=report, brand_label=BRAND_LABEL)


def to_markdown(report: WeeklyReport) -> str:
    return _env.get_template("weekly.md.j2").render(r=report, brand_label=BRAND_LABEL)


def to_slack_blocks(report: WeeklyReport, full_report_url: str | None = None) -> list[dict]:
    top = "\n".join(
        f"• [{BRAND_LABEL[e.brand.value]}] <{e.url}|{e.title}>" + (f" — {e.llm_note}" if e.llm_note else "")
        for e in report.top_events
    )
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📊 경쟁사 주간 리포트 {report.week}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*TL;DR* {report.tldr}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*🔥 Top 5*\n{top or '-'}"}},
        {"type": "section", "text": {"type": "mrkdwn",
                                     "text": "*💡 액션 제안*\n" + "\n".join(f"• {a}" for a in report.actions)}},
    ]
    if full_report_url:
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "전체 리포트 보기"}, "url": full_report_url}
        ]})
    return blocks
