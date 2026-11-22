"""[D] 대시보드 렌더링 테스트."""
import re
from datetime import datetime, timedelta

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


def _card(html: str, label: str) -> str:
    """브랜드 카드 한 장의 HTML만 잘라낸다."""
    return re.search(rf"<article[^>]*>\s*<h3>.*?{label}</h3>.*?</article>", html, re.DOTALL).group(0)


def test_render_empty_shows_empty_states():
    html = dashboard.render([], [], [], {}, generated_at=NOW)
    assert "2026-11-22 09:00" in html
    for label in ("스파오", "에잇세컨즈", "무신사 스탠다드", "유니클로"):
        assert label in html
    assert "감지된 이벤트가 없습니다" in html
    assert "아직 저장된 주간 리포트가 없습니다" in html
    assert "기록된 LLM 호출이 없습니다" in html
    assert "<svg" not in html  # 데이터가 없으면 빈 차트 대신 안내 문구


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


def test_render_escapes_scraped_text():
    e = _event(title='<script>alert(1)</script> "특가"', llm_note="<b>굵게</b>", importance=5,
               category=Category.PRICING)
    report = _report()
    report.tldr = "<img src=x onerror=alert(1)>"
    report.insights = ["<i>기울임</i>"]
    html = dashboard.render([e], [report], [{"model": "<m>", "calls": 1, "input_tokens": 2, "output_tokens": 3}],
                            {"spao": {"<x>": 1}}, generated_at=NOW)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<b>굵게</b>" not in html and "&lt;b&gt;굵게&lt;/b&gt;" in html
    assert "<img" not in html and "<i>기울임</i>" not in html
    assert "<m>" not in html and "<x>" not in html


def test_render_links_only_http_urls():
    events = [
        _event(title="정상 링크", url="https://example.com/a?x=1&y=2", importance=5),
        _event(title="스크립트 링크", url="javascript:alert(1)", importance=5),
        _event(title="데이터 링크", url="data:text/html,<script>1</script>", importance=4),
        _event(title="빈 링크", url="", importance=4),
    ]
    html = dashboard.render(events, [], [], {}, generated_at=NOW)
    assert '<a href="https://example.com/a?x=1&amp;y=2" target="_blank" rel="noopener noreferrer">정상 링크</a>' in html
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


def test_render_reports_and_usage():
    usage = [
        {"model": "claude-haiku", "calls": 1200, "input_tokens": 2_500_000, "output_tokens": 90_000},
        {"model": "claude-sonnet", "calls": 4, "input_tokens": 50_000, "output_tokens": None},
    ]
    html = dashboard.render([], [_report("2026-W47"), _report("2026-W46")], usage, {}, generated_at=NOW)
    assert html.index("2026-W47") < html.index("2026-W46")
    assert "유니클로가 겨울 세일을 앞당겼다" in html
    assert html.count("<details") == 2
    assert "인사이트 1건 · 액션 제안 1건" in html and "가격 대응 검토" in html
    assert "claude-haiku" in html and "2,500,000" in html
    assert '<td>합계</td><td class="num">1,204</td><td class="num">2,550,000</td><td class="num">90,000</td>' in html


def test_build_writes_file_from_repo(tmp_path, monkeypatch):
    calls = {}

    def recent_events(days=30):
        calls["days"] = days
        return [_event(title="빌드 테스트 이벤트", importance=5, days_ago=0)]

    monkeypatch.setattr(repo, "recent_events", recent_events, raising=False)
    monkeypatch.setattr(repo, "list_reports", lambda limit=12: [_report()], raising=False)
    monkeypatch.setattr(repo, "llm_usage", lambda days=30: [], raising=False)
    monkeypatch.setattr(repo, "item_counts", lambda: {"spao": {"news": 3}}, raising=False)

    out = dashboard.build(tmp_path / "nested" / "dash.html")
    assert out == tmp_path / "nested" / "dash.html" and calls["days"] == 30
    html = out.read_text(encoding="utf-8")
    assert "빌드 테스트 이벤트" in html and "2026-W47" in html


def test_build_default_path(tmp_path, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(repo, "recent_events", lambda days=30: [], raising=False)
    monkeypatch.setattr(repo, "list_reports", lambda limit=12: [], raising=False)
    monkeypatch.setattr(repo, "llm_usage", lambda days=30: [], raising=False)
    monkeypatch.setattr(repo, "item_counts", dict, raising=False)

    out = dashboard.build()
    assert out == tmp_path / "data" / "dashboard.html" and out.exists()
