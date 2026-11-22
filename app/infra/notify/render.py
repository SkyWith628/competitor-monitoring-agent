"""WeeklyReport → HTML(메일) / Markdown / 슬랙 Block Kit. 담당: D

설계서 §6.4 리포트 구성(TL;DR → Top 5 → 브랜드별 → 인사이트·액션 → 부록 수치 표)을 그대로 따른다.
"""
from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.core.models import ChangeEvent, ChangeType, WeeklyReport

BRAND_LABEL = {"spao": "스파오", "8seconds": "에잇세컨즈", "musinsa_standard": "무신사 스탠다드", "uniqlo": "유니클로"}

CHANGE_LABEL = {
    ChangeType.NEW_PRODUCT.value: "신상품",
    ChangeType.PRICE_DOWN.value: "가격 인하",
    ChangeType.PRICE_UP.value: "가격 인상",
    ChangeType.SOLD_OUT.value: "품절",
    ChangeType.RESTOCK.value: "재입고",
    ChangeType.PROMO_CHANGED.value: "프로모션 변경",
    ChangeType.NEWS.value: "뉴스",
    ChangeType.SNS_SPIKE.value: "SNS 반응 급증",
    ChangeType.SNS_NEW_POST.value: "SNS 새 게시물",
}

# Slack Block Kit 제한 (https://api.slack.com/reference/block-kit/blocks)
SLACK_SECTION_LIMIT = 3000
SLACK_HEADER_LIMIT = 150
SLACK_MAX_BLOCKS = 50
SLACK_URL_LIMIT = 3000
_TITLE_LIMIT = 150  # 한 항목이 섹션을 독차지하지 않도록 자체적으로 두는 상한
_NOTE_LIMIT = 300


def _safe_url(url: object) -> str:
    """http(s) 링크만 허용 — 수집된 URL에 javascript: 같은 스킴이 섞여도 메일에 실리지 않게 한다."""
    s = str(url or "").strip()
    return s if s.lower().startswith(("http://", "https://")) else "#"


def _md_text(text: object) -> str:
    """Markdown 표 셀·링크 텍스트를 깨뜨리는 문자를 이스케이프."""
    s = " ".join(str(text).split())
    for ch in ("\\", "|", "[", "]"):
        s = s.replace(ch, "\\" + ch)
    return s


# 템플릿 이름이 weekly.html.j2 라서 select_autoescape(["html"])로는 이스케이프가 켜지지 않는다 → 직접 판별
_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=lambda name: bool(name) and ".html" in name,
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["safe_url"] = _safe_url
_env.filters["md"] = _md_text


def _brand(value: str) -> str:
    return BRAND_LABEL.get(value, value)


# --------------------------------------------------------------------------- 부록: 수치 표

def _is_num(v: object) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _fmt_num(v: float) -> str:
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.1f}"
    return f"{int(v):,}"


def _fmt_delta(cur: float, prev: float) -> str:
    d = cur - prev
    if isinstance(d, float):
        d = round(d, 1)
    if d == 0:
        return "±0"
    return ("+" if d > 0 else "-") + _fmt_num(abs(d))


def _cell(cur: float, prev: float | None) -> dict:
    """표 한 칸. text는 "12 (+3)" 형태(전주 값이 없으면 "12")."""
    value = _fmt_num(cur)
    delta = _fmt_delta(cur, prev) if prev is not None else None
    return {"value": value, "delta": delta, "text": f"{value} ({delta})" if delta else value}


def _fmt_other(v: object) -> str:
    if v is None:
        return "-"
    if isinstance(v, str | bool):
        return str(v)
    if isinstance(v, list | tuple | set):
        return ", ".join(_fmt_other(x) for x in v) or "-"
    try:
        return json.dumps(v, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(v)


def _flatten(prefix: str, value: object, out: dict[str, object]) -> None:
    """중첩 dict를 "a · b" 경로로 펼친다 (C가 나중에 추가할 지표를 형태와 무관하게 표시하기 위함)."""
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix} · {k}" if prefix else str(k), v, out)
    else:
        out[prefix] = value


def _split_stats(stats: object) -> tuple[dict[str, dict[str, float]], dict[str, object]]:
    """stats → (브랜드별 변화유형 건수, 그 밖의 지표). 모르는 키는 버리지 않고 전부 '그 밖'으로 보낸다."""
    counts: dict[str, dict[str, float]] = {}
    extras: dict[str, object] = {}
    if not isinstance(stats, dict):
        return counts, extras
    for key, value in stats.items():
        key = str(key)
        if not isinstance(value, dict):
            extras[key] = value
            continue
        row = counts.setdefault(key, {})
        for k, v in value.items():
            if str(k) in CHANGE_LABEL and _is_num(v):
                row[str(k)] = v
            else:
                _flatten(f"{_brand(key)} · {k}", v, extras)
    return counts, extras


def stats_view(stats: dict | None, prev_stats: dict | None = None) -> dict:
    """템플릿이 그대로 그릴 수 있는 수치 표 구조를 만든다.

    반환: {"has_prev", "columns": [브랜드 라벨], "rows": [{"label": 변화 유형, "cells", "total"}],
    "total_row", "extras"}
    cells/total/extras의 값은 `_cell()` 형태.
    """
    counts, extras = _split_stats(stats or {})
    has_prev = prev_stats is not None
    prev_counts, prev_extras = _split_stats(prev_stats or {})

    brands = [b for b in BRAND_LABEL if b in counts or b in prev_counts]
    brands += [b for b in list(counts) + list(prev_counts) if b not in BRAND_LABEL and b not in brands]
    # 이번 주·전주 어느 쪽에도 건수가 없는 변화 유형은 열에서 뺀다
    types = [t for t in CHANGE_LABEL
             if any(counts.get(b, {}).get(t) or prev_counts.get(b, {}).get(t) for b in brands)]

    def cell(cur: float, prev: float) -> dict:
        # 이번 주·전주 모두 0인 칸은 증감 표시 없이 0만 보여준다 (표가 ±0으로 뒤덮이지 않게)
        return _cell(cur, prev if has_prev and (cur or prev) else None)

    # 행 = 변화 유형, 열 = 브랜드 (브랜드 수는 고정이라 모바일 메일에서도 폭이 넘치지 않는다)
    rows = []
    for t in types:
        cur = [counts.get(b, {}).get(t, 0) for b in brands]
        prev = [prev_counts.get(b, {}).get(t, 0) for b in brands]
        rows.append({
            "label": CHANGE_LABEL[t],
            "cells": [cell(c, p) for c, p in zip(cur, prev, strict=True)],
            "total": cell(sum(cur), sum(prev)),
        })
    col_cur = [sum(counts.get(b, {}).get(t, 0) for t in types) for b in brands]
    col_prev = [sum(prev_counts.get(b, {}).get(t, 0) for t in types) for b in brands]
    total_row = {
        "label": "합계",
        "cells": [cell(c, p) for c, p in zip(col_cur, col_prev, strict=True)],
        "total": cell(sum(col_cur), sum(col_prev)),
    }

    extra_rows = []
    for label, value in extras.items():
        if _is_num(value):
            prev = prev_extras.get(label)
            c = _cell(value, prev if has_prev and _is_num(prev) else None)
        else:
            text = _fmt_other(value)
            c = {"value": text, "delta": None, "text": text}
        extra_rows.append({"label": label, **c})

    return {
        "has_prev": has_prev,
        "columns": [_brand(b) for b in brands],
        "rows": rows,
        "total_row": total_row if len(rows) > 1 else None,
        "extras": extra_rows,
    }


# --------------------------------------------------------------------------- 메일 / Markdown

def _context(report: WeeklyReport, prev_stats: dict | None) -> dict:
    return {
        "r": report,
        "brand_label": BRAND_LABEL,
        "change_label": CHANGE_LABEL,
        "summaries": [s for s in report.brand_summaries if s.highlights],
        "stats": stats_view(report.stats, prev_stats),
    }


def to_html(report: WeeklyReport, prev_stats: dict | None = None) -> str:
    """메일 본문 HTML. prev_stats(전주 stats)를 주면 수치 표에 전주 대비 증감을 함께 표시한다."""
    return _env.get_template("weekly.html.j2").render(**_context(report, prev_stats))


def to_markdown(report: WeeklyReport, prev_stats: dict | None = None) -> str:
    """HTML과 같은 내용을 Markdown으로 (메일 text 파트·파일 보관용)."""
    return _env.get_template("weekly.md.j2").render(**_context(report, prev_stats)).strip() + "\n"


# --------------------------------------------------------------------------- 슬랙

def _esc(text: object) -> str:
    """mrkdwn 제어 문자 이스케이프. 줄바꿈은 목록 구조를 깨므로 공백으로 편다."""
    s = " ".join(str(text).split())
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_clip(text: object, limit: int) -> str:
    """이스케이프한 결과가 limit 이하가 되도록 원문을 잘라낸다 (엔티티 중간에서 끊기지 않게 원문 기준)."""
    raw = " ".join(str(text).split())
    out = _esc(raw)
    while len(out) > limit and raw:
        raw = raw[: max(0, len(raw) - (len(out) - limit) - 1)]
        out = _esc(raw) + "…"
    return out if len(out) <= limit else ""


def _link(url: str, title: str) -> str:
    """<url|제목>. 제목의 `|`는 링크 문법과 충돌하므로 전각 문자로 바꾼다."""
    label = _esc_clip(title, _TITLE_LIMIT).replace("|", "｜") or "(제목 없음)"
    safe = _safe_url(url)
    if safe == "#" or len(safe) > SLACK_URL_LIMIT - 500:
        return label
    safe = safe.replace("&", "&amp;").replace("<", "%3C").replace(">", "%3E").replace("|", "%7C")
    return f"<{safe}|{label}>"


def _sections(heading: str, lines: list[str], empty: str = "-") -> list[dict]:
    """heading + 줄 목록을 3000자 제한에 맞춰 여러 section으로 나눈다. 줄 중간에서는 끊지 않는다."""
    chunks: list[str] = []
    cur = heading
    for line in lines or [empty]:
        line = line if len(line) <= SLACK_SECTION_LIMIT - len(heading) - 1 else ""
        if not line:
            continue
        if len(cur) + 1 + len(line) > SLACK_SECTION_LIMIT:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    chunks.append(cur)
    return [{"type": "section", "text": {"type": "mrkdwn", "text": c}} for c in chunks if c]


def _header(text: str) -> dict:
    text = " ".join(text.split())
    if len(text) > SLACK_HEADER_LIMIT:
        text = text[: SLACK_HEADER_LIMIT - 1] + "…"
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}


def _event_line(e: ChangeEvent) -> str:
    line = f"• [{_esc(_brand(e.brand.value))}] {_link(e.url, e.title)}"
    if e.llm_note:
        line += f" — {_esc_clip(e.llm_note, _NOTE_LIMIT)}"
    return line


def to_slack_blocks(report: WeeklyReport, full_report_url: str | None = None) -> list[dict]:
    """본 메시지: 헤더 · TL;DR · Top 5 · 액션 제안 · (선택) 전체 리포트 버튼."""
    tldr_head = "*TL;DR* "
    blocks: list[dict] = [
        _header(f"📊 경쟁사 주간 리포트 {report.week}"),
        {"type": "section", "text": {"type": "mrkdwn", "text": tldr_head + (
            _esc_clip(report.tldr, SLACK_SECTION_LIMIT - len(tldr_head)) or "-")}},
        *_sections("*🔥 Top 5*", [_event_line(e) for e in report.top_events[:5]]),
    ]
    if report.actions:
        blocks += _sections("*💡 액션 제안*", [f"• {_esc_clip(a, 500)}" for a in report.actions])
    button = None
    url = (full_report_url or "").strip()
    if url.lower().startswith(("http://", "https://")) and len(url) <= SLACK_URL_LIMIT:
        button = {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "전체 리포트 보기"}, "url": url}
        ]}
    blocks = blocks[: SLACK_MAX_BLOCKS - (1 if button else 0)]
    if button:
        blocks.append(button)
    return blocks


def to_slack_brand_blocks(report: WeeklyReport) -> list[list[dict]]:
    """후속 메시지: 하이라이트가 있는 브랜드마다 블록 목록 하나 (설계서 "너무 길면 브랜드별 분할")."""
    out: list[list[dict]] = []
    for s in report.brand_summaries:
        if not s.highlights:
            continue
        heading = f"*[{_esc(_brand(s.brand.value))}] 브랜드 요약 · {_esc(report.week)}*"
        blocks = _sections(heading, [f"• {_esc_clip(h, 1000)}" for h in s.highlights])
        out.append(blocks[:SLACK_MAX_BLOCKS])
    return out


def _detail_text(detail: dict) -> str | None:
    """{"before": 39900, "after": 29900} → "39,900 → 29,900"."""
    before, after = detail.get("before"), detail.get("after")
    if before is None or after is None:
        return None

    def fmt(v: object) -> str:
        return _fmt_num(v) if _is_num(v) else _esc_clip(v, 100)

    return f"{fmt(before)} → {fmt(after)}"


def to_urgent_blocks(event: ChangeEvent) -> list[dict]:
    """중요도 높은 단일 이벤트용 "긴급 알림" 메시지."""
    change = CHANGE_LABEL.get(event.change_type.value, event.change_type.value)
    lines = [_link(event.url, event.title)]
    detail = _detail_text(event.detail or {})
    if detail:
        lines.append(detail)
    if event.llm_note:
        lines.append(f"> {_esc_clip(event.llm_note, 1000)}")
    meta = [f"감지 {event.detected_at:%Y-%m-%d %H:%M}"]
    if event.importance is not None:
        meta.insert(0, f"중요도 {event.importance}/5")
    if event.category is not None:
        meta.insert(0, _esc(event.category.value))
    return [
        _header("🚨 긴급 알림"),
        *_sections(f"*[{_esc(_brand(event.brand.value))}] {_esc(change)}*", lines),
        {"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(meta)}]},
    ]
