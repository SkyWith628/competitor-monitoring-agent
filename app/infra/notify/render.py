"""WeeklyReport → HTML(메일) / Markdown / 슬랙 Block Kit. 담당: D

설계서 §6.4 리포트 구성(TL;DR → Top 5 → 브랜드별 → 인사이트·액션 → 부록 수치 표)을 그대로 따른다.

이 모듈은 "어떻게 보일지"만 맡는다. 네트워크·DB·환경 변수는 건드리지 않고, 같은 입력이면 항상 같은
결과를 돌려준다(그래서 테스트와 `--dry-run` 미리보기가 쉽다).

누가 부르나
    - `app/__main__.py` 의 report 명령: `to_html`·`to_markdown` 으로 파일을 만들고 메일 본문으로 쓴다.
    - `notify/slack.py`: `to_slack_blocks`·`to_slack_brand_blocks`(주간 리포트), `to_urgent_blocks`(긴급 알림).
    - 대시보드 등 다른 화면: `BRAND_LABEL`·`CHANGE_LABEL` 로 내부 값을 한글 이름으로 바꾼다.

데이터 흐름
    C(analysis)가 만든 WeeklyReport ─┬─ `_context()` ─ Jinja 템플릿(templates/weekly.*.j2) ─ HTML / Markdown
                                     └─ `to_slack_*()` ─ Block Kit dict 목록 ─ slack.py 가 웹훅으로 POST
    report.stats(브랜드별 변화 유형 건수)는 `stats_view()` 가 표 구조로 바꿔 템플릿에 넘긴다.

입력은 수집된 웹 페이지 제목·LLM 문장처럼 믿을 수 없는 문자열이다. 출력 형식마다 깨지거나 악용될 수 있는
문자가 달라서 이스케이프도 따로 둔다: HTML 은 Jinja 자동 이스케이프, Markdown 은 `_md_text`,
슬랙 mrkdwn 은 `_esc`·`_link`, 링크 주소는 `_safe_url`.
"""
from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.core.models import ChangeEvent, ChangeType, WeeklyReport

BRAND_LABEL = {"spao": "스파오", "8seconds": "에잇세컨즈", "musinsa_standard": "무신사 스탠다드", "uniqlo": "유니클로",
               "whoau": "후아유"}
# 메일 수치 표의 열 머리글용 짧은 이름 — 휴대폰 폭(360px)에서 브랜드 4열 + 합계가 한 화면에 들어가야 한다
BRAND_SHORT_LABEL = {"8seconds": "8세컨즈", "musinsa_standard": "무신사"}

# 내용이 없을 때의 문구. 메일·Markdown·슬랙이 같은 말을 쓴다
EMPTY_TLDR = "(요약 없음)"
EMPTY_EVENTS = "이번 주 감지된 주요 변화가 없습니다."

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
    ChangeType.TREND_SPIKE.value: "쇼핑 클릭 급증",
}

# Slack Block Kit 제한 (https://api.slack.com/reference/block-kit/blocks)
# 하나라도 넘으면 슬랙이 메시지 전체를 400 invalid_blocks 로 거절한다 → 넘기 전에 여기서 자르거나 나눈다.
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
    """브랜드 내부 값("8seconds") → 표시 이름("에잇세컨즈"). 모르는 값은 그대로 보여준다."""
    return BRAND_LABEL.get(value, value)


# --------------------------------------------------------------------------- 부록: 수치 표

def _is_num(v: object) -> bool:
    """집계할 수 있는 숫자인지. bool 은 int 의 하위 타입이라 True 가 1 로 더해지지 않게 따로 뺀다."""
    return isinstance(v, int | float) and not isinstance(v, bool)


def _fmt_num(v: float) -> str:
    """천 단위 쉼표. 소수는 한 자리까지만: 1234 → "1,234", 3.14 → "3.1", 2.0 → "2"."""
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.1f}"
    return f"{int(v):,}"


def _fmt_delta(cur: float, prev: float) -> str:
    """전주 대비 증감 문구: "+3", "-1,042", 변화가 없으면 "±0"."""
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
    """숫자가 아닌 지표 값을 표에 넣을 글자로. 어떤 형태가 와도 리포트 생성이 죽지 않게 한다."""
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

    반환: {"has_prev", "columns": [브랜드 라벨], "short_columns": [좁은 표용 짧은 라벨],
    "rows": [{"label": 변화 유형, "cells", "total"}], "total_row", "extras"}
    cells/total/extras의 값은 `_cell()` 형태.

    Args:
        stats: 이번 주 `WeeklyReport.stats`. 보통 {"spao": {"new_product": 12, "price_down": 3}, ...}.
            None·빈 dict·형태가 다른 값이 와도 예외 없이 빈 표를 돌려준다.
        prev_stats: 전주 stats. None 이면 "전주 자료 없음"으로 보고 증감을 표시하지 않는다.
            빈 dict({})는 "전주에는 0건"이라는 뜻이라 증감을 표시한다 — 둘은 다르다.

    Returns:
        위 구조의 dict. 변화 유형이 하나도 없으면 rows 가 비고, 행이 하나뿐이면 합계 행(total_row)은
        같은 숫자의 반복이라 None 이다.

    예:
        >>> v = stats_view({"spao": {"new_product": 12}}, {"spao": {"new_product": 9}})
        >>> v["columns"], v["rows"][0]["label"], v["rows"][0]["cells"][0]["text"]
        (['스파오'], '신상품', '12 (+3)')
    """
    counts, extras = _split_stats(stats or {})
    has_prev = prev_stats is not None
    prev_counts, prev_extras = _split_stats(prev_stats or {})

    brands = [b for b in BRAND_LABEL if b in counts or b in prev_counts]
    # 모르는 키는 변화 유형 건수가 실제로 있을 때만 열로 세운다 ("llm": {"input_tokens": ..} 같은 지표 묶음은
    # 건수가 없으므로 0으로 채운 빈 열 대신 아래 '그 밖의 지표' 표에만 나온다). 양쪽 주에 다 있어도 한 번만.
    for b in [*counts, *prev_counts]:
        if b not in brands and (counts.get(b) or prev_counts.get(b)):
            brands.append(b)
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
        "short_columns": [BRAND_SHORT_LABEL.get(b) or _brand(b) for b in brands],
        "rows": rows,
        "total_row": total_row if len(rows) > 1 else None,
        "extras": extra_rows,
    }


# --------------------------------------------------------------------------- 메일 / Markdown

def _context(report: WeeklyReport, prev_stats: dict | None) -> dict:
    """HTML·Markdown 템플릿이 함께 쓰는 변수. 두 형식의 내용이 어긋나지 않도록 한 곳에서 만든다."""
    return {
        "r": report,
        # 줄바꿈을 편 TL;DR — 프리헤더·Markdown 인용문은 한 줄이어야 하고, 공백뿐이면 "없음"으로 다룬다
        "tldr": " ".join((report.tldr or "").split()),
        "empty_tldr": EMPTY_TLDR,
        "empty_events": EMPTY_EVENTS,
        "brand_label": BRAND_LABEL,
        "change_label": CHANGE_LABEL,
        "summaries": [s for s in report.brand_summaries if s.highlights],  # 할 말이 없는 브랜드는 제목도 뺀다
        "stats": stats_view(report.stats, prev_stats),
    }


def to_html(report: WeeklyReport, prev_stats: dict | None = None) -> str:
    """메일 본문 HTML(완결된 문서 한 장)을 만든다.

    Args:
        report: C가 만든 주간 리포트.
        prev_stats: 전주 리포트의 stats. 주면 수치 표에 전주 대비 증감을 함께 표시한다.

    Returns:
        `<!doctype html>` 로 시작하는 문자열. 외부 CSS·JS·이미지 없이 인라인 스타일과 table 레이아웃만
        쓰므로 메일 본문으로도, 파일로 저장해 브라우저로 열어도 같은 모습이다.

    리포트 안의 글자는 모두 HTML 이스케이프되고 http(s) 가 아닌 링크는 "#" 으로 바뀐다. 예외를 던지지 않는다
    (빈 리포트는 "없음" 문구로 채운다).

    예:
        email.send_html(f"[경쟁사 주간 리포트] {r.week}", to_html(r, prev), text=to_markdown(r, prev))
    """
    return _env.get_template("weekly.html.j2").render(**_context(report, prev_stats))


def to_markdown(report: WeeklyReport, prev_stats: dict | None = None) -> str:
    """HTML과 같은 내용을 Markdown으로 만든다 (메일 text 파트·파일 보관용).

    인자는 `to_html` 과 같다. 반환값은 줄바꿈 하나로 끝나는 Markdown 문자열. 표·링크 문법을 깨는 문자
    (`|`, `[`, `]`, `\\`)는 이스케이프한다.
    """
    return _env.get_template("weekly.md.j2").render(**_context(report, prev_stats)).strip() + "\n"


# --------------------------------------------------------------------------- 슬랙

def _esc(text: object) -> str:
    """mrkdwn 제어 문자 이스케이프. 줄바꿈은 목록 구조를 깨므로 공백으로 편다.

    슬랙은 `&`, `<`, `>` 세 글자만 엔티티로 바꾸라고 요구한다. 안 바꾸면 `<!channel>` 같은 멘션이나
    `<주소|글자>` 링크가 수집된 제목을 통해 그대로 만들어진다.
    """
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
    # 링크로 쓸 수 없거나 너무 긴 주소는 글자만 남긴다. 주소 안의 `<`, `>`, `|` 는 링크 문법을 끝내 버리므로
    # 퍼센트 인코딩한다(주소의 뜻은 그대로다).
    if safe == "#" or len(safe) > SLACK_URL_LIMIT - 500:
        return label
    safe = safe.replace("&", "&amp;").replace("<", "%3C").replace(">", "%3E").replace("|", "%7C")
    return f"<{safe}|{label}>"


def _sections(heading: str, lines: list[str], empty: str = "-") -> list[dict]:
    """heading + 줄 목록을 3000자 제한에 맞춰 여러 section으로 나눈다. 줄 중간에서는 끊지 않는다."""
    chunks: list[str] = []
    cur = heading
    for line in lines or [empty]:
        # 한 줄이 혼자서도 제한을 넘으면 뺀다 — 호출하는 쪽이 줄마다 미리 잘라서(_esc_clip) 실제로는 생기지 않는다
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
    """header 블록. plain_text 라 이스케이프는 필요 없고 150자 제한만 지킨다."""
    text = " ".join(text.split())
    if len(text) > SLACK_HEADER_LIMIT:
        text = text[: SLACK_HEADER_LIMIT - 1] + "…"
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}


def _event_line(e: ChangeEvent) -> str:
    """Top 5 한 줄: "• [브랜드] <링크|제목> — 메모"."""
    line = f"• [{_esc(_brand(e.brand.value))}] {_link(e.url, e.title)}"
    if e.llm_note:
        line += f" — {_esc_clip(e.llm_note, _NOTE_LIMIT)}"
    return line


def to_slack_blocks(report: WeeklyReport, full_report_url: str | None = None) -> list[dict]:
    """슬랙 본 메시지 블록: 헤더 · TL;DR · Top 5 · 액션 제안 · (선택) 전체 리포트 버튼.

    Args:
        report: 주간 리포트. top_events 는 앞의 5건만 쓴다.
        full_report_url: "전체 리포트 보기" 버튼이 열 주소(환경 변수 REPORT_URL). http(s) 주소가 아니거나
            비어 있으면 버튼을 만들지 않는다.

    Returns:
        Block Kit 블록(dict) 목록. 글자 수·블록 수 제한 안에 들어오도록 자르고 나눈 상태라 그대로
        `slack.send_blocks` 에 넘기면 된다. 예외를 던지지 않는다.

    브랜드별 요약과 인사이트는 여기 없다. 본 메시지는 한눈에 읽히게 짧게 두고, 브랜드별 내용은
    `to_slack_brand_blocks` 로 후속 메시지에 싣는다.
    """
    tldr_head = "*TL;DR* "
    blocks: list[dict] = [
        _header(f"📊 경쟁사 주간 리포트 {report.week}"),
        {"type": "section", "text": {"type": "mrkdwn", "text": tldr_head + (
            _esc_clip(report.tldr, SLACK_SECTION_LIMIT - len(tldr_head)) or EMPTY_TLDR)}},
        *_sections("*🔥 Top 5*", [_event_line(e) for e in report.top_events[:5]], empty=EMPTY_EVENTS),
    ]
    if report.actions:
        blocks += _sections("*💡 액션 제안*", [f"• {_esc_clip(a, 500)}" for a in report.actions])
    button = None
    url = (full_report_url or "").strip()
    if url.lower().startswith(("http://", "https://")) and len(url) <= SLACK_URL_LIMIT:
        button = {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "전체 리포트 보기"}, "url": url}
        ]}
    # 블록이 50개를 넘으면 뒤를 버리되, 버튼은 맨 끝에 반드시 남긴다
    blocks = blocks[: SLACK_MAX_BLOCKS - (1 if button else 0)]
    if button:
        blocks.append(button)
    return blocks


def to_slack_brand_blocks(report: WeeklyReport) -> list[list[dict]]:
    """슬랙 후속 메시지들: 하이라이트가 있는 브랜드마다 블록 목록 하나 (설계서 "너무 길면 브랜드별 분할").

    Returns:
        메시지별 블록 목록의 목록. 바깥 목록의 원소 하나가 슬랙 메시지 한 건이다. 하이라이트가 있는
        브랜드가 없으면 빈 목록(후속 메시지 없음).
    """
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
    """중요도 높은 단일 이벤트용 "긴급 알림" 메시지 블록.

    구성: 헤더 → [브랜드] 변화 유형 + 링크 + (있으면) 전/후 값 + LLM 메모 → 분류·중요도·감지 시각.
    분류·중요도가 아직 없는(None) 이벤트도 그대로 그린다. 예외를 던지지 않는다.

    예:
        slack.send_blocks(to_urgent_blocks(event), fallback_text=f"[긴급] {event.title}")
    """
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
