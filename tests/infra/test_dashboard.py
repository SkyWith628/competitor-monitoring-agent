"""[D] 대시보드 렌더링 테스트.

render()는 순수 함수라 샘플 데이터를 직접 넣어 HTML 문자열을 확인한다. 생성 시각은 NOW로 고정한다
("최근 30일"·"N시간 전"·"지연" 판정이 전부 생성 시각 기준이라 고정하지 않으면 날짜에 따라 결과가 바뀐다).
build() 테스트는 repo 함수를 가짜로 바꿔 DB 없이 돈다.
"""
import re
from datetime import UTC, datetime, timedelta

import pytest

from app.core.models import Brand, Category, ChangeEvent, ChangeType, WeeklyReport
from app.infra import dashboard
from app.infra.storage import repo

NOW = datetime(2026, 11, 22, 9, 0, tzinfo=dashboard.KST)


def _event(brand=Brand.SPAO, change=ChangeType.PRICE_DOWN, days_ago=1, **kw) -> ChangeEvent:
    kw.setdefault("title", "히트텍 가격 인하")
    kw.setdefault("url", "https://example.com/p/1")
    return ChangeEvent(brand=brand, change_type=change, detected_at=NOW - timedelta(days=days_ago), **kw)


def _report(week="2026-W47") -> WeeklyReport:
    return WeeklyReport(week=week, tldr="유니클로가 겨울 세일을 앞당겼다", top_events=[], brand_summaries=[],
                        insights=["세일 시점이 빨라짐"], actions=["가격 대응 검토"])


def _section(html: str, heading_id: str) -> str:
    """제목 id로 섹션 하나의 HTML만 잘라낸다. 예: "h-health" → 운영 상태 섹션."""
    return html.split(f'id="{heading_id}"')[1].split("</section>")[0]


def _row(section: str, text: str) -> str:
    """섹션 안에서 text가 들어 있는 표의 한 줄(<tr>…</tr>)."""
    return next(tr for tr in re.findall(r"<tr>.*?</tr>", section, re.DOTALL) if text in tr)


def _health(**overrides) -> dict:
    """정상 2줄 + 지연 1줄 + 시각 없음 1줄, 발송·미발송·일부 발송 리포트가 섞인 운영 상태 샘플.

    NOW(2026-11-22 09:00 KST) 기준: 06:01 수집 = 2시간 전(정상), 11-20 20:59 = 36시간 1분 전(지연).
    """
    health = {
        "sources": [
            {"brand": "spao", "source": "spao.com", "source_type": "site_product", "items": 1234,
             "last_collected_at": "2026-11-22T06:01:12"},
            {"brand": "spao", "source": "naver_news", "source_type": "news", "items": 56,
             "last_collected_at": "2026-11-20T21:00:00"},  # 정확히 36시간 전 → 아직 정상
            {"brand": "8seconds", "source": "instagram", "source_type": "sns_post", "items": 78,
             "last_collected_at": "2026-11-20T20:59:00"},  # 36시간 1분 전 → 지연
            {"brand": "uniqlo", "source": "uniqlo.com", "source_type": "site_product", "items": 9,
             "last_collected_at": None},
        ],
        "unclassified": 1234,
        "pending_urgent": 2,
        "reports": [
            {"week": "2026-W47", "sent_at": None, "channels": []},
            {"week": "2026-W46", "sent_at": "2026-11-16T08:00:03+09:00", "channels": ["email", "slack"]},
            {"week": "2026-W45", "sent_at": None, "channels": ["slack"]},
        ],
    }
    health.update(overrides)
    return health


@pytest.fixture
def fake_repo(monkeypatch):
    """build()가 읽는 repo 함수를 전부 가짜로 바꾼다(DB에 닿지 않게). 테스트가 필요한 것만 다시 덮어쓴다.

    운영 상태용 함수는 다른 작업에서 추가 중이라 아직 없을 수 있어 raising=False로 넣는다.
    """
    monkeypatch.setattr(repo, "recent_events", lambda days=30: [], raising=False)
    monkeypatch.setattr(repo, "list_reports", lambda limit=12: [], raising=False)
    monkeypatch.setattr(repo, "llm_usage", lambda days=30: [], raising=False)
    monkeypatch.setattr(repo, "item_counts", dict, raising=False)
    monkeypatch.setattr(repo, "source_freshness", list, raising=False)
    monkeypatch.setattr(repo, "unclassified_count", lambda: 0, raising=False)
    monkeypatch.setattr(repo, "report_status", lambda limit=8: [], raising=False)
    monkeypatch.setattr(repo, "pending_urgent", lambda days=7: [], raising=False)
    return monkeypatch


def _card(html: str, label: str) -> str:
    """브랜드 카드 한 장의 HTML만 잘라낸다."""
    return re.search(rf"<article[^>]*>\s*<h3>.*?{label}</h3>.*?</article>", html, re.DOTALL).group(0)


def test_render_empty_shows_empty_states():
    html = dashboard.render([], [], [], {}, generated_at=NOW)
    assert "2026-11-22 09:00 KST" in html
    assert '<html lang="ko">' in html
    assert "<title>경쟁사 모니터링 대시보드 · 2026-11-22</title>" in html
    assert "운영 상태 정보 없음" in html  # health를 주지 않으면 중립 문구
    assert "중요도 4 이상으로 분류된 이벤트가 없습니다" in html
    for label in ("스파오", "에잇세컨즈", "무신사 스탠다드", "유니클로"):
        assert label in html
    assert "감지된 이벤트가 없습니다" in html
    assert "아직 저장된 주간 리포트가 없습니다" in html
    assert "기록된 LLM 호출이 없습니다" in html
    assert 'id="trend-chart"' not in html and 'class="hit"' not in html  # 빈 차트 대신 안내 문구


def test_render_is_self_contained():
    html = dashboard.render([_event()], [_report()], [], {}, generated_at=NOW)
    assert not re.search(r"<(script|link|img)[^>]+(src|href)=", html)
    assert "@import" not in html and "url(" not in html


def test_render_counts_per_brand():
    events = [
        _event(Brand.SPAO, ChangeType.PRICE_DOWN),
        _event(Brand.SPAO, ChangeType.PRICE_DOWN, days_ago=2),
        _event(Brand.SPAO, ChangeType.NEW_PRODUCT, days_ago=3),
        _event(Brand.UNIQLO, ChangeType.NEWS),
        _event(Brand.UNIQLO, ChangeType.SOLD_OUT, days_ago=45),  # 30일 밖 → 집계 제외
    ]
    counts = {"spao": {"site_product": 1234, "news": 5}, "uniqlo": {"sns_post": 7, "blog": 2}}
    html = dashboard.render(events, [], [], counts, generated_at=NOW)

    spao = _card(html, "스파오")
    assert '<div class="stat-value">1,239</div>' in spao  # 수집 항목 합계
    assert '<div class="stat-value">3</div>' in spao  # 30일 이벤트
    assert '상품 <b class="num">1,234</b>' in spao
    assert '가격 인하 <b class="num">2</b>' in spao and '신상품 <b class="num">1</b>' in spao

    uniqlo = _card(html, "유니클로")
    assert '<div class="stat-value">9</div>' in uniqlo and '<div class="stat-value">1</div>' in uniqlo
    assert 'blog <b class="num">2</b>' in uniqlo  # 모르는 소스 타입도 표시
    assert "품절" not in uniqlo
    assert "감지된 변화 없음" in _card(html, "에잇세컨즈")

    # 차트: 어제 칸에 스파오 1 · 유니클로 1
    assert 'data-day="2026-11-21" data-counts="1,0,0,1" data-total="2"' in html
    assert html.count('class="hit"') == 30
    # 주요 이벤트·차트와 섞이지 않게 창 밖 날짜는 어디에도 없다
    assert "2026-10-08" not in html


def test_render_escapes_scraped_text():
    e = _event(title='<script>alert(1)</script> "특가"', llm_note="<b>굵게</b>", importance=5,
               category=Category.PRICING)
    report = _report()
    report.tldr = "<img src=x onerror=alert(1)>"
    report.insights = ["<i>기울임</i>"]
    usage = [{"model": "<m>", "calls": 1, "input_tokens": 2, "output_tokens": 3}]
    html = dashboard.render([e], [report], usage,
                            {"spao": {"<x>": 1}}, generated_at=NOW)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<b>굵게</b>" not in html and "&lt;b&gt;굵게&lt;/b&gt;" in html
    assert "<img" not in html and "<i>기울임</i>" not in html
    assert "<m>" not in html and "<x>" not in html


def test_render_escapes_health_text():
    health = _health(
        sources=[{"brand": "<b>brand</b>", "source": "<script>alert(2)</script>", "source_type": "<i>t</i>",
                  "items": "<u>", "last_collected_at": "<img src=x>"}],
        reports=[{"week": "<s>week</s>", "sent_at": "\"><script>alert(3)</script>",
                  "channels": ["<em>ch</em>"]}],
    )
    html = dashboard.render([], [], [], {}, generated_at=NOW, health=health)
    section = _section(html, "h-health")
    for raw in ("<b>brand</b>", "<script>alert(2)</script>", "<i>t</i>", "<u>", "<img", "<s>week</s>",
                "<script>alert(3)</script>", "<em>ch</em>"):
        assert raw not in section
    assert "&lt;b&gt;brand&lt;/b&gt;" in section and "&lt;script&gt;alert(2)&lt;/script&gt;" in section
    assert "&lt;s&gt;week&lt;/s&gt;" in section and "&lt;em&gt;ch&lt;/em&gt;" in section
    # 읽을 수 없는 시각은 버리고 "기록 없음"·"미발송"으로 본다
    assert "기록 없음" in _row(section, "&lt;b&gt;brand")
    assert "일부 발송" in _row(section, "&lt;s&gt;week")


def test_render_links_only_http_urls():
    events = [
        _event(title="정상 링크", url="https://example.com/a?x=1&y=2", importance=5),
        _event(title="스크립트 링크", url="javascript:alert(1)", importance=5),
        _event(title="데이터 링크", url="data:text/html,<script>1</script>", importance=4),
        _event(title="빈 링크", url="", importance=4),
    ]
    html = dashboard.render(events, [], [], {}, generated_at=NOW)
    link = '<a href="https://example.com/a?x=1&amp;y=2" target="_blank" rel="noopener noreferrer">'
    assert link + "정상 링크</a>" in html
    assert html.count("<a ") == 1
    assert "javascript:" not in html and "data:text" not in html
    assert "스크립트 링크" in html and "데이터 링크" in html and "빈 링크" in html


def test_render_high_importance_list():
    events = [
        _event(title="중요한 변화", importance=5, category=Category.PRICING, llm_note="경쟁사 최저가"),
        _event(title="사소한 변화", importance=2),
        _event(title="미분류 변화"),
    ]
    html = dashboard.render(events, [], [], {}, generated_at=NOW)
    listing = html.split('id="h-events"')[1].split("</section>")[0]
    assert "중요한 변화" in listing and "중요도 5" in listing
    assert "가격전략" in listing and "경쟁사 최저가" in listing
    assert "사소한 변화" not in listing and "미분류 변화" not in listing
    assert '<time datetime="2026-11-21T09:00:00+09:00">2026-11-21 09:00 KST</time>' in listing


def test_render_high_importance_only_inside_window():
    events = [
        _event(title="창 안 첫날", importance=5, days_ago=29),
        _event(title="창 밖 하루 전", importance=5, days_ago=30),
        _event(title="미래 날짜", importance=5, days_ago=-1),
    ]
    html = dashboard.render(events, [], [], {}, generated_at=NOW)
    listing = _section(html, "h-events")
    assert "창 안 첫날" in listing
    assert "창 밖 하루 전" not in listing and "미래 날짜" not in listing
    assert 'data-day="2026-10-24" data-counts="1,0,0,0" data-total="1"' in html  # 차트의 첫 칸과 같은 창


def test_render_buckets_days_in_korean_time():
    """UTC·tz 없는 시각이 섞여도 KST 날짜로 묶는다. 월·연 경계에서도 칸은 30개."""
    now = datetime(2027, 1, 2, 0, 30, tzinfo=dashboard.KST)
    events = [
        # 2026-12-31 15:10 UTC = 2027-01-01 00:10 KST → 1월 1일 칸
        ChangeEvent(brand=Brand.SPAO, change_type=ChangeType.NEWS, title="새해 첫 뉴스", url="",
                    detected_at=datetime(2026, 12, 31, 15, 10, tzinfo=UTC), importance=4),
        # tz 없는 값은 한국 로컬 시각 → 12월 31일 칸
        ChangeEvent(brand=Brand.UNIQLO, change_type=ChangeType.NEWS, title="연말 뉴스", url="",
                    detected_at=datetime(2026, 12, 31, 23, 59), importance=4),
        # 2027-01-01 15:20 UTC = 1월 2일 00:20 KST → 오늘 칸
        ChangeEvent(brand=Brand.UNIQLO, change_type=ChangeType.NEWS, title="오늘 뉴스", url="",
                    detected_at=datetime(2027, 1, 1, 15, 20, tzinfo=UTC), importance=4),
    ]
    html = dashboard.render(events, [], [], {}, generated_at=now)
    assert 'data-day="2027-01-01" data-counts="1,0,0,0" data-total="1"' in html
    assert 'data-day="2026-12-31" data-counts="0,0,0,1" data-total="1"' in html
    assert 'data-day="2027-01-02" data-counts="0,0,0,1" data-total="1"' in html
    days = re.findall(r'data-day="([\d-]+)"', html)
    assert len(days) == 30 and days[0] == "2026-12-04" and days[-1] == "2027-01-02"
    assert "2026-12-04 ~ 2027-01-02 (KST)" in html
    listing = _section(html, "h-events")
    assert "2027-01-01 00:10 KST" in listing and "2026-12-31 23:59 KST" in listing
    # 날짜 눈금: 오늘에서 5일 간격, 월이 바뀌어도 "월/일"
    labels = re.findall(r"<text [^>]*>([^<]+)</text>", html)
    assert labels == ["12/8", "12/13", "12/18", "12/23", "12/28", "1/2"]


def test_render_naive_generated_at_is_read_as_local_time():
    html = dashboard.render([], [], [], {}, generated_at=datetime(2026, 11, 22, 9, 0))
    assert '<time datetime="2026-11-22T09:00:00+09:00">2026-11-22 09:00 KST</time>' in html


def test_trend_points_are_keyboard_focusable():
    events = [
        _event(Brand.SPAO, days_ago=3),
        _event(Brand.UNIQLO, days_ago=3),
        _event(Brand.UNIQLO, days_ago=3),
        _event(Brand.EIGHT_SECONDS, days_ago=10),
    ]
    html = dashboard.render(events, [], [], {}, generated_at=NOW)
    hits = re.findall(r'<rect class="hit"[^>]*>', html)
    assert len(hits) == 30
    # Tab 정지점은 하나(이벤트가 있는 가장 최근 날짜), 나머지는 화살표 키로 이동
    entry = [h for h in hits if 'tabindex="0"' in h]
    assert len(entry) == 1 and 'data-day="2026-11-19"' in entry[0]
    assert sum('tabindex="-1"' in h for h in hits) == 29
    # 포커스된 칸이 툴팁과 같은 내용을 읽어 준다
    assert 'role="img" aria-label="2026-11-19 합계 3건: 스파오 1건, 유니클로 2건"' in entry[0]
    assert any('aria-label="2026-11-22 합계 0건"' in h for h in hits)
    assert ".hit:focus-visible" in html and "ArrowLeft" in html  # 포커스 표시와 키보드 이동
    assert "<summary>표로 보기</summary>" in html  # 표 보기는 그대로


def test_render_tables_have_header_scopes():
    usage = [{"model": "m1", "calls": 1, "input_tokens": 2, "output_tokens": 3}]
    html = dashboard.render([_event()], [_report()], usage, {}, generated_at=NOW, health=_health())
    body = html.split("</style>")[1].split("<script>")[0]
    assert body.count("<table") == 4  # 수집 상태, 발송, 추이 표, 사용량
    assert not re.search(r"<th(?![^>]*\bscope=)[ >]", body)  # 모든 th에 scope
    for row in re.findall(r"<tbody>.*?</tbody>", body, re.DOTALL):
        assert all('<th scope="row"' in tr for tr in re.findall(r"<tr>.*?</tr>", row, re.DOTALL))
    # 제목 단계는 건너뛰지 않는다: h1 → h2 → h3
    levels = [int(n) for n in re.findall(r"<h(\d)", body)]
    assert levels[0] == 1 and levels.count(1) == 1 and max(levels) == 3
    assert all(b - a <= 1 for a, b in zip(levels, levels[1:], strict=False))


def test_render_css_details():
    html = dashboard.render([_event()], [_report()], [], {}, generated_at=NOW)
    css = html.split("<style>")[1].split("</style>")[0]
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "@media print" in css and "details::details-content" in css
    assert "beforeprint" in html  # ::details-content 미지원 브라우저용
    assert "overflow-wrap: anywhere" in css  # 긴 제목 줄바꿈
    # 브랜드 색은 밝은·어두운 배경 모두에서 3:1 이상인 조합(값을 바꾸면 대비를 다시 계산할 것)
    assert "--series-1: #2a78d6; --series-2: #d17236; --series-3: #2f6b12; --series-4: #a31583;" in css
    assert "--series-1: #3987e5; --series-2: #e29936; --series-3: #35802a; --series-4: #ca358b;" in css


def _luminance(hex_color: str) -> float:
    """WCAG 상대 휘도."""
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_brand_colours_have_3_to_1_contrast_on_both_themes():
    html = dashboard.render([], [], [], {}, generated_at=NOW)
    css = html.split("<style>")[1].split("</style>")[0]
    light, dark = css.split("prefers-color-scheme: dark")[0], css.split("prefers-color-scheme: dark")[1]
    for theme in (light, dark):
        backgrounds = [re.search(rf"--{name}: (#\w{{6}})", theme).group(1) for name in ("page", "surface")]
        colours = re.findall(r"--series-\d: (#\w{6})", theme)[:4]
        assert len(set(colours)) == 4
        for colour in colours:
            for bg in backgrounds:
                hi, lo = sorted((_luminance(colour), _luminance(bg)), reverse=True)
                assert (hi + 0.05) / (lo + 0.05) >= 3, (colour, bg)


def test_brand_marker_shapes_differ():
    """색만으로 구분하지 않도록 범례·카드의 표식은 브랜드마다 모양이 다르다."""
    html = dashboard.render([_event()], [], [], {}, generated_at=NOW)
    legend = html.split('id="trend-legend"')[1].split("</ul>")[0]
    shapes = re.findall(r'<svg class="swatch s(\d)"[^>]*>(<[a-z]+ [^>]*>)</svg>', legend)
    assert [n for n, _ in shapes] == ["1", "2", "3", "4"]
    assert len({shape for _, shape in shapes}) == 4


# ---------- 운영 상태 ----------
def test_health_sources_fresh_stale_missing():
    html = dashboard.render([], [], [], {}, generated_at=NOW, health=_health())
    section = _section(html, "h-health")
    assert "운영 상태 정보 없음" not in section

    fresh = _row(section, "spao.com")
    assert "스파오" in fresh and "상품" in fresh and '<td class="num">1,234</td>' in fresh
    assert "2시간 전" in fresh and "정상" in fresh
    assert '<time datetime="2026-11-22T06:01:12+09:00">2026-11-22 06:01 KST</time>' in fresh

    edge = _row(section, "naver_news")  # 정확히 36시간은 아직 정상
    assert "36시간 전" in edge and "정상" in edge and "지연" not in edge

    stale = _row(section, "instagram")
    assert "에잇세컨즈" in stale and "36시간 전" in stale and "2026-11-20 20:59 KST" in stale
    assert 'class="state state-stale"' in stale and "지연" in stale  # 색 말고 글자·모양으로도 표시
    assert "<svg" in stale and "정상" not in stale

    no_time = _row(section, "uniqlo.com")
    assert "기록 없음" in no_time and "<time" not in no_time
    never = _row(section, "무신사 스탠다드")  # 조회 결과에 아예 없는 브랜드도 드러낸다
    assert "기록 없음" in never and '<td class="num">0</td>' in never

    # 요약 타일
    assert "확인 필요" in section
    summary = re.sub(r"<[^>]+>|\s+", " ", section)
    assert re.search(r"소스 +5 +개 중 지연 +1 +개 · 기록 없음 +2 +개", summary)
    assert '<div class="stat-value num">1,234</div>' in section  # 미분류 이벤트, 천 단위 구분
    assert "2건" in section and "아직 알림이 나가지 않음" in section


def test_health_report_send_status():
    html = dashboard.render([], [_report("2026-W47"), _report("2026-W46")], [], {}, generated_at=NOW,
                            health=_health())
    section = _section(html, "h-health")
    unsent = _row(section, "2026-W47")
    assert "미발송" in unsent and "<time" not in unsent
    sent = _row(section, "2026-W46")
    assert "미발송" not in sent and "발송" in sent and "메일, 슬랙" in sent
    assert "6일 전" in sent and "2026-11-16 08:00 KST" in sent
    partial = _row(section, "2026-W45")
    assert "일부 발송" in partial and "슬랙" in partial and "메일" not in partial
    # 최근 리포트 타일과 주간 리포트 목록에도 같은 상태가 붙는다
    assert section.index("최근 주간 리포트") < section.index("미발송")
    reports = _section(html, "h-reports")
    assert reports.count("미발송") == 1 and "2026-11-16 08:00 KST" in reports


def test_health_all_fresh_and_zero_counts():
    sources = [
        {"brand": b.value, "source": "site", "source_type": "site_product", "items": 1,
         "last_collected_at": "2026-11-22T08:59:30"}
        for b in Brand
    ]
    health = _health(sources=sources, unclassified=0, pending_urgent=0, reports=[])
    section = _section(dashboard.render([], [], [], {}, generated_at=NOW, health=health), "h-health")
    assert "확인 필요" not in section and "지연" not in section.split("</div>", 1)[1]
    assert "모두 36시간 안에 수집" in section and section.count("방금 전") == 4
    assert "모두 분류됨" in section and "밀린 긴급 알림 없음" in section
    assert "저장된 리포트 없음" in section and "아직 저장된 주간 리포트가 없습니다" in section


def test_health_time_formats_and_clock_skew():
    sources = [
        {"brand": "spao", "source": "a", "items": 1, "last_collected_at": "2026-11-22T08:15:00"},
        {"brand": "spao", "source": "b", "items": 1, "last_collected_at": "2026-11-22T09:05:00"},  # 미래
        {"brand": "spao", "source": "c", "items": 1, "last_collected_at": "2026-11-21T00:00:00+00:00"},
        {"brand": "spao", "source": "d", "items": 1, "last_collected_at": datetime(2026, 10, 1, 9, 0)},
    ]
    section = _section(
        dashboard.render([], [], [], {}, generated_at=NOW, health={"sources": sources}), "h-health"
    )
    assert "45분 전" in section and "방금 전" in section
    assert "2026-11-21 09:00 KST" in section and "24시간 전" in section  # UTC 값도 KST로 바꿔 표시
    assert "52일 전" in section


def test_health_partial_and_empty_data():
    # 키가 일부만 있어도 나머지는 "정보 없음"
    section = _section(
        dashboard.render([], [], [], {}, generated_at=NOW, health={"unclassified": 3}), "h-health"
    )
    assert '<div class="stat-value num">3</div>' in section
    assert section.count("정보 없음") >= 3 and "수집 상태 정보 없음" in section and "발송 기록 정보 없음" in section

    # 새 DB: 값은 있는데 전부 비어 있음
    empty = {"sources": [], "unclassified": 0, "reports": [], "pending_urgent": 0}
    section = _section(dashboard.render([], [], [], {}, generated_at=NOW, health=empty), "h-health")
    assert "아직 수집된 항목이 없습니다" in section and "<table" not in section
    assert "확인 필요" not in section

    # 이상한 값이 와도 죽지 않는다
    garbage = {"sources": "x", "reports": 1, "unclassified": "many", "pending_urgent": None}
    for weird in ({}, [], "x", garbage):
        html = dashboard.render([], [], [], {}, generated_at=NOW, health=weird)
        assert "운영 상태" in html
    assert "운영 상태 정보 없음" in dashboard.render([], [], [], {}, generated_at=NOW, health={})


def test_render_reports_and_usage():
    usage = [
        {"model": "claude-haiku", "calls": 1200, "input_tokens": 2_500_000, "output_tokens": 90_000},
        {"model": "claude-sonnet", "calls": 4, "input_tokens": 50_000, "output_tokens": None},
    ]
    html = dashboard.render([], [_report("2026-W47"), _report("2026-W46")], usage, {}, generated_at=NOW)
    assert html.index("2026-W47") < html.index("2026-W46")
    assert "유니클로가 겨울 세일을 앞당겼다" in html
    assert _section(html, "h-reports").count("<details") == 2
    assert "인사이트 1건 · 액션 제안 1건" in html and "가격 대응 검토" in html
    assert "claude-haiku" in html and "2,500,000" in html
    total = '<th scope="row">합계</th><td class="num">1,204</td><td class="num">2,550,000</td>'
    assert total + '<td class="num">90,000</td>' in html


# ---------- build ----------
def test_build_writes_file_from_repo(tmp_path, fake_repo):
    calls = {}

    def recent_events(days=30):
        calls["days"] = days
        return [_event(title="빌드 테스트 이벤트", importance=5, days_ago=0)]

    fake_repo.setattr(repo, "recent_events", recent_events)
    fake_repo.setattr(repo, "list_reports", lambda limit=12: [_report()])
    fake_repo.setattr(repo, "item_counts", lambda: {"spao": {"news": 3}})

    out = dashboard.build(tmp_path / "nested" / "dash.html")
    assert out == tmp_path / "nested" / "dash.html" and calls["days"] == 30
    html = out.read_text(encoding="utf-8")
    assert "빌드 테스트 이벤트" in html and "2026-W47" in html


def test_build_default_path(tmp_path, fake_repo):
    from app.core import config

    fake_repo.setattr(config, "ROOT", tmp_path)
    out = dashboard.build()
    assert out == tmp_path / "data" / "dashboard.html" and out.exists()


def test_build_gathers_health_from_repo(tmp_path, fake_repo):
    now = datetime.now(dashboard.KST).replace(tzinfo=None)
    fake_repo.setattr(repo, "source_freshness", lambda: [
        {"brand": "spao", "source": "spao.com", "source_type": "site_product", "items": 5,
         "last_collected_at": (now - timedelta(hours=1)).isoformat()},
        {"brand": "uniqlo", "source": "uniqlo.com", "source_type": "site_product", "items": 7,
         "last_collected_at": (now - timedelta(days=4)).isoformat()},
    ])
    fake_repo.setattr(repo, "unclassified_count", lambda: 41)
    fake_repo.setattr(repo, "report_status", lambda limit=8: [
        {"week": "2026-W47", "sent_at": None, "channels": []},
    ])
    fake_repo.setattr(repo, "pending_urgent", lambda days=7: [_event(importance=5), _event(importance=5)])

    section = _section(dashboard.build(tmp_path / "d.html").read_text(encoding="utf-8"), "h-health")
    assert "정상" in _row(section, "spao.com") and "1시간 전" in _row(section, "spao.com")
    assert "지연" in _row(section, "uniqlo.com") and "4일 전" in _row(section, "uniqlo.com")
    assert '<div class="stat-value num">41</div>' in section
    assert "2건" in section and "미발송" in _row(section, "2026-W47")


def test_build_without_new_repo_functions(tmp_path, fake_repo):
    """repo에 운영 상태 함수가 아직 없어도(구버전) 대시보드는 만들어진다."""
    for name in ("source_freshness", "unclassified_count", "report_status", "pending_urgent"):
        fake_repo.delattr(repo, name, raising=False)
    html = dashboard.build(tmp_path / "d.html").read_text(encoding="utf-8")
    assert "운영 상태 정보 없음" in html and "브랜드별 현황" in html


def test_build_skips_health_functions_that_fail(tmp_path, fake_repo, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("postgresql://user:secret@db.example/prod 연결 실패")

    fake_repo.setattr(repo, "source_freshness", boom)
    fake_repo.setattr(repo, "report_status", boom)
    fake_repo.delattr(repo, "unclassified_count", raising=False)
    fake_repo.setattr(repo, "pending_urgent", lambda days=7: [_event(importance=5)])

    html = dashboard.build(tmp_path / "d.html").read_text(encoding="utf-8")
    section = _section(html, "h-health")
    assert "1건" in section  # 얻은 값은 보여 주고
    assert "수집 상태 정보 없음" in section and "발송 기록 정보 없음" in section  # 실패한 값만 비운다
    assert "source_freshness" in caplog.text and "secret" not in caplog.text and "secret" not in html
