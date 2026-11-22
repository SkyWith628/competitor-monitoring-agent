"""[D] 리포트 렌더링 테스트 — HTML / Markdown / 슬랙 Block Kit."""
import json
import re
from datetime import UTC, datetime

from app.core.models import Brand, BrandSummary, Category, ChangeEvent, ChangeType, WeeklyReport
from app.infra.notify import render
from app.infra.notify.render import (
    to_html,
    to_markdown,
    to_slack_blocks,
    to_slack_brand_blocks,
    to_urgent_blocks,
)

STATS = {"spao": {"new_product": 12, "price_down": 3}, "uniqlo": {"news": 5}}
PREV = {"spao": {"new_product": 9, "price_down": 3}, "uniqlo": {"news": 7, "sold_out": 2}}


def _event(title: str = "히트텍 가격 인하", **kw) -> ChangeEvent:
    base = {"id": 1, "brand": Brand.UNIQLO, "change_type": ChangeType.PRICE_DOWN, "title": title,
            "url": "https://example.com/p/1", "detected_at": datetime(2026, 11, 20, 9, 30, tzinfo=UTC),
            "detail": {"before": 39900, "after": 29900}, "category": Category.PRICING, "importance": 5,
            "llm_note": "경쟁 상품 대비 25% 저렴"}
    return ChangeEvent(**{**base, **kw})


def _report(**kw) -> WeeklyReport:
    base = {"week": "2026-W47", "tldr": "테스트 요약", "top_events": [],
            "brand_summaries": [BrandSummary(brand=Brand.UNIQLO, highlights=["예시 [#1]"], event_ids=[1])],
            "insights": ["인사이트"], "actions": ["액션"]}
    return WeeklyReport(**{**base, **kw})


def _text(html: str) -> str:
    """태그를 걷어내고 공백을 정리한 본문."""
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _mrkdwn(blocks: list[dict]) -> str:
    return "\n".join(b["text"]["text"] for b in blocks if b["type"] == "section")


def _assert_slack_limits(blocks: list[dict]) -> None:
    assert 0 < len(blocks) <= 50
    for b in blocks:
        if b["type"] == "section":
            assert 0 < len(b["text"]["text"]) <= 3000
        if b["type"] == "header":
            assert len(b["text"]["text"]) <= 150
    json.dumps(blocks)  # 웹훅 payload로 직렬화 가능해야 한다


# ---------------------------------------------------------------- HTML

def test_html_contains_tldr_and_brand():
    html = to_html(_report())
    assert "테스트 요약" in html and "유니클로" in html


def test_html_is_self_contained_for_mail_clients():
    html = to_html(_report(top_events=[_event()], stats=STATS))
    assert html.lstrip().startswith("<!doctype html>")
    assert "<script" not in html and "<link" not in html and "<style" not in html


def test_html_stats_table_without_prev():
    text = _text(to_html(_report(stats=STATS)))
    assert "부록: 수치 표" in text
    assert "변화 유형 스파오 유니클로 합계" in text  # 열 = 브랜드
    assert "품절" not in text  # 건수가 없는 유형은 행을 만들지 않는다
    assert "전주 대비" not in text and "(+" not in text and "±0" not in text
    assert "신상품 12 0 12" in text
    assert "가격 인하 3 0 3" in text
    assert "뉴스 0 5 5" in text
    assert "합계 15 5 20" in text


def test_html_stats_table_with_prev_shows_delta():
    text = _text(to_html(_report(stats=STATS), prev_stats=PREV))
    assert "전주 대비" in text
    assert "신상품 12 (+3) 0 12 (+3)" in text  # 이번 주·전주 모두 0인 칸은 증감 없이 0
    assert "가격 인하 3 (±0) 0 3 (±0)" in text
    assert "품절 0 0 (-2) 0 (-2)" in text  # 전주에만 있던 유형도 보여준다
    assert "뉴스 0 5 (-2) 5 (-2)" in text
    assert "합계 15 (+3) 5 (-4) 20 (-1)" in text


def test_empty_prev_stats_means_everything_is_new():
    assert "신상품 12 (+12)" in _text(to_html(_report(stats=STATS), prev_stats={}))


def test_stats_unknown_keys_do_not_crash_and_render_generically():
    stats = {
        "spao": {"new_product": 2, "avg_discount_rate": 23.5, "by_category": {"가격전략": 1}, "tags": ["a", "b"]},
        "zara": {"new_product": 1},
        "sns_engagement_total": 15300,
        "note": None,
        7: "숫자 키",
    }
    prev = {"spao": {"avg_discount_rate": 20.0}, "sns_engagement_total": 12000, "note": "x"}
    for out in (_text(to_html(_report(stats=stats), prev_stats=prev)), to_markdown(_report(stats=stats), prev)):
        assert "zara" in out
        assert "스파오 · avg_discount_rate" in out and "23.5 (+3.5)" in out
        assert "스파오 · by_category · 가격전략" in out
        assert "sns_engagement_total" in out and "15,300 (+3,300)" in out
        assert "스파오 · tags" in out and "a, b" in out
    to_html(_report(stats=stats))
    to_markdown(_report(stats={"x": {"y": {"z": object()}}}))


def test_stats_empty_or_malformed():
    assert "집계된 수치가 없습니다" in to_html(_report())
    assert "집계된 수치가 없습니다" in to_markdown(_report(stats={"spao": {}}))
    assert "이상한 값" in to_html(_report(stats={"spao": "이상한 값"}), prev_stats={"spao": 3, "x": [1]})


def test_html_escapes_content_and_blocks_non_http_links():
    html = to_html(_report(tldr="<script>alert(1)</script>",
                           top_events=[_event(title="A <b>&</b> B", url="javascript:alert(1)")]))
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "A &lt;b&gt;&amp;&lt;/b&gt; B" in html
    assert "javascript:" not in html


def test_html_handles_empty_report():
    html = to_html(WeeklyReport(week="2026-W47", tldr="", top_events=[], brand_summaries=[], insights=[],
                                actions=[]))
    assert "이번 주 감지된 주요 변화가 없습니다" in html


# ---------------------------------------------------------------- Markdown

def test_markdown_has_all_sections():
    md = to_markdown(_report(top_events=[_event()], stats=STATS), prev_stats=PREV)
    assert md.startswith("# 📊 경쟁사 주간 리포트 2026-W47\n")
    for heading in ("## 🔥 Top 5 변화", "## 브랜드별 요약", "### 유니클로", "## 💡 인사이트", "## ✅ 액션 제안",
                    "## 부록: 수치 표"):
        assert f"\n{heading}\n" in md
    assert "1. [유니클로] [히트텍 가격 인하](https://example.com/p/1) — 경쟁 상품 대비 25% 저렴" in md
    assert "- 예시 [#1]" in md
    assert "\n\n| 변화 유형 | 스파오 | 유니클로 | 합계 |\n|---|---:|---:|---:|\n" in md
    assert "| 신상품 | 12 (+3) | 0 | 12 (+3) |" in md
    assert "| 품절 | 0 | 0 (-2) | 0 (-2) |" in md
    assert "| **합계** | 15 (+3) | 5 (-4) | 20 (-1) |" in md


def test_markdown_without_prev_and_escaping():
    md = to_markdown(_report(top_events=[_event(title="1+1 | [특가]")], stats=STATS))
    assert "| 신상품 | 12 | 0 | 12 |" in md
    assert "(+" not in md
    assert "[1+1 \\| \\[특가\\]](https://example.com/p/1)" in md
    assert "&lt;" not in to_markdown(_report(tldr="a < b"))  # Markdown은 HTML 이스케이프를 하지 않는다


# ---------------------------------------------------------------- 슬랙

def test_slack_blocks_have_header():
    assert to_slack_blocks(_report())[0]["type"] == "header"


def test_slack_main_message_structure_and_button():
    r = _report(top_events=[_event()])
    blocks = to_slack_blocks(r, full_report_url="https://example.com/report")
    _assert_slack_limits(blocks)
    text = _mrkdwn(blocks)
    assert "*TL;DR* 테스트 요약" in text
    assert "• [유니클로] <https://example.com/p/1|히트텍 가격 인하> — 경쟁 상품 대비 25% 저렴" in text
    assert "• 액션" in text
    assert blocks[-1]["type"] == "actions"
    button = blocks[-1]["elements"][0]
    assert button["text"]["text"] == "전체 리포트 보기" and button["url"] == "https://example.com/report"
    assert all(b["type"] != "actions" for b in to_slack_blocks(r))
    assert all(b["type"] != "actions" for b in to_slack_blocks(r, full_report_url="not a url"))


def test_slack_handles_empty_lists():
    blocks = to_slack_blocks(WeeklyReport(week="2026-W47", tldr="", top_events=[], brand_summaries=[],
                                          insights=[], actions=[]))
    _assert_slack_limits(blocks)
    assert to_slack_brand_blocks(_report(brand_summaries=[])) == []


def test_slack_escapes_mrkdwn_special_chars():
    e = _event(title="A|B <세일> & more", url="https://example.com/p?a=1&b=<2>|3", llm_note="x < y & z")
    text = _mrkdwn(to_slack_blocks(_report(tldr="<!channel> R&D", top_events=[e], actions=["<@U1> 확인"])))
    assert "<https://example.com/p?a=1&amp;b=%3C2%3E%7C3|A｜B &lt;세일&gt; &amp; more>" in text
    assert "x &lt; y &amp; z" in text
    assert "&lt;!channel&gt; R&amp;D" in text and "<!channel>" not in text
    assert "&lt;@U1&gt; 확인" in text
    # 남아 있는 `<`는 우리가 만든 링크 하나뿐이어야 한다
    assert text.count("<") == 1 and text.count("|") == 1


def test_slack_truncates_to_limits():
    events = [_event(title="제목&" * 2000, llm_note="<메모>" * 2000, id=i) for i in range(8)]
    r = _report(tldr="요약&<>" * 3000, top_events=events, actions=["액션&" * 3000] * 40)
    blocks = to_slack_blocks(r, full_report_url="https://example.com/report")
    _assert_slack_limits(blocks)
    assert blocks[-1]["type"] == "actions"
    text = _mrkdwn(blocks)
    assert text.count("<https://") == 5  # Top 5만
    assert not re.search(r"&(?!amp;|lt;|gt;)", text)  # 엔티티 중간에서 잘리지 않는다
    assert "…" in text


def test_slack_many_long_actions_stay_within_block_limit():
    r = _report(actions=["가" * 499] * 400)
    blocks = to_slack_blocks(r, full_report_url="https://example.com/report")
    _assert_slack_limits(blocks)
    assert len(blocks) == 50 and blocks[-1]["type"] == "actions"


def test_slack_brand_blocks_one_list_per_brand_with_highlights():
    r = _report(brand_summaries=[
        BrandSummary(brand=Brand.SPAO, highlights=["신상품 12건 <확대> [#3]", "가격 인하 [#4]"], event_ids=[3, 4]),
        BrandSummary(brand=Brand.EIGHT_SECONDS, highlights=[], event_ids=[]),
        BrandSummary(brand=Brand.UNIQLO, highlights=["긴 문장 " * 300 + "[#9]"] * 30, event_ids=[9]),
    ])
    out = to_slack_brand_blocks(r)
    assert len(out) == 2
    spao = _mrkdwn(out[0])
    assert spao.startswith("*[스파오] 브랜드 요약 · 2026-W47*")
    assert "• 신상품 12건 &lt;확대&gt; [#3]\n• 가격 인하 [#4]" in spao
    assert "에잇세컨즈" not in spao
    assert len(out[1]) > 1  # 길면 여러 section으로 나뉜다
    for blocks in out:
        _assert_slack_limits(blocks)


def test_urgent_blocks():
    blocks = to_urgent_blocks(_event(title="히트텍 <50%> 세일 | 한정"))
    _assert_slack_limits(blocks)
    assert blocks[0]["type"] == "header" and "긴급 알림" in blocks[0]["text"]["text"]
    text = _mrkdwn(blocks)
    assert text.startswith("*[유니클로] 가격 인하*")
    assert "<https://example.com/p/1|히트텍 &lt;50%&gt; 세일 ｜ 한정>" in text
    assert "39,900 → 29,900" in text
    assert "> 경쟁 상품 대비 25% 저렴" in text
    assert "중요도 5/5" in blocks[-1]["elements"][0]["text"]


def test_urgent_blocks_minimal_event():
    e = ChangeEvent(brand=Brand.SPAO, change_type=ChangeType.NEWS, title="", url="")
    blocks = to_urgent_blocks(e)
    _assert_slack_limits(blocks)
    assert "*[스파오] 뉴스*" in _mrkdwn(blocks)


def test_brand_label_kept():
    assert render.BRAND_LABEL["8seconds"] == "에잇세컨즈"
    assert set(render.BRAND_LABEL) == {b.value for b in Brand}
    assert set(render.CHANGE_LABEL) == {c.value for c in ChangeType}


# ---------------------------------------------------------------- 빈 상태·프리헤더·좁은 화면

def _empty() -> WeeklyReport:
    return WeeklyReport(week="2026-W47", tldr=" \n ", top_events=[], brand_summaries=[], insights=[], actions=[])


def test_empty_states_use_the_same_wording_everywhere():
    html, md, slack_text = to_html(_empty()), to_markdown(_empty()), _mrkdwn(to_slack_blocks(_empty()))
    for out in (_text(html), md, slack_text):
        assert render.EMPTY_EVENTS in out and render.EMPTY_TLDR in out
    assert "이번 주 감지된 주요 변화가 없습니다" in render.EMPTY_EVENTS
    for out in (_text(html), md):
        assert "브랜드별 요약이 없습니다" in out and "(없음)" in out and "집계된 수치가 없습니다" in out
    assert "> **TL;DR** (요약 없음)" in md


def test_html_has_hidden_preheader_with_tldr():
    html = to_html(_report(tldr="첫 줄\n둘째 <b>줄</b>"))
    body = html.split("<body", 1)[1]
    pre = re.search(r'<div style="([^"]*)">([^<]*)</div>', body)
    assert pre and body.index(pre.group(0)) < body.index("<table")  # 본문보다 먼저 와야 미리보기에 잡힌다
    assert "display:none" in pre.group(1) and "mso-hide:all" in pre.group(1)
    assert pre.group(2) == "첫 줄 둘째 &lt;b&gt;줄&lt;/b&gt;"  # 한 줄로 펴고 이스케이프
    assert "display:none" not in to_html(_empty())  # 요약이 없으면 프리헤더도 없다


def test_html_declares_language_and_has_no_images():
    html = to_html(_report(top_events=[_event()], stats=STATS))
    assert '<html lang="ko">' in html and "<img" not in html
    assert html.count('scope="col"') >= 4  # 수치 표 머리글


def test_html_stats_table_fits_phone_width():
    """휴대폰 폭 대책: 짧은 열 이름, 통째로 줄바꿈되는 증감, 넘칠 때는 표만 스크롤."""
    stats = {"musinsa_standard": {"new_product": 1203}, "8seconds": {"new_product": 86}}
    html = to_html(_report(stats=stats), prev_stats={"musinsa_standard": {"new_product": 87}})
    table = html.split("부록: 수치 표", 1)[1]
    assert "변화 유형 8세컨즈 무신사 합계" in _text(table)
    assert "무신사 스탠다드" not in table and "에잇세컨즈" not in table
    assert '<div style="overflow-x:auto;">' in table
    assert re.search(r'<span style="white-space:nowrap;">1,203</span> '
                     r'<span style="display:inline-block;white-space:nowrap;[^"]*">\(\+1,116\)</span>', table)
    assert html.count("table-layout:fixed") == 2  # 바깥 틀이 내용에 밀려 넓어지지 않는다
    md = to_markdown(_report(stats=stats))
    assert "| 변화 유형 | 에잇세컨즈 | 무신사 스탠다드 | 합계 |" in md  # 폭 제약이 없는 Markdown 은 전체 이름


def test_stats_view_short_columns_follow_columns():
    view = render.stats_view({"spao": {"news": 1}, "musinsa_standard": {"news": 2}, "zara": {"news": 3}})
    assert view["columns"] == ["스파오", "무신사 스탠다드", "zara"]
    assert view["short_columns"] == ["스파오", "무신사", "zara"]


def test_stats_unknown_key_in_both_weeks_is_one_column():
    view = render.stats_view({"zara": {"news": 3}}, {"zara": {"news": 1}})
    assert view["columns"] == ["zara"] and view["rows"][0]["cells"][0]["text"] == "3 (+2)"


def test_stats_metric_groups_do_not_become_empty_brand_columns():
    stats = {"spao": {"news": 2}, "llm": {"input_tokens": 1200}}
    view = render.stats_view(stats, {"spao": {"news": 1}, "llm": {"input_tokens": 1000}})
    assert view["columns"] == ["스파오"]
    assert [(x["label"], x["text"]) for x in view["extras"]] == [("llm · input_tokens", "1,200 (+200)")]


def test_markdown_tldr_stays_on_one_quote_line():
    md = to_markdown(_report(tldr="첫 줄\n\n# 둘째 줄"))
    assert "> **TL;DR** 첫 줄 # 둘째 줄\n" in md
