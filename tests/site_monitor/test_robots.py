"""[A] robots.txt 판정기 테스트 — 4개 브랜드 실제 규칙 발췌(2026-11-23 확인)."""
from app.site_monitor.robots import Robots

UA = "CompetitorMonitorBot/0.1 (internal research)"

SPAO = """User-agent: *
Disallow: /

User-agent: Googlebot
Allow: /i/item
Allow: /c/ctg
"""

MUSINSA = """User-agent: Googlebot
User-agent: ClaudeBot
Allow: /
Disallow: /auth/

User-agent: *
Disallow: /
"""

UNIQLO = """User-Agent: *
Disallow: /*/*/cms/
Allow: /customize/tairyo/cart
Disallow: /*/*/cart
Disallow: /*/*/search?q=
Disallow: /*?categoryIds=
Disallow: /*&categoryIds=
"""


def test_wildcard_fallback_blocks_unknown_bot():
    assert not Robots(SPAO, UA).can_fetch("https://www.spao.com/i/item?no=1")
    assert not Robots(MUSINSA, UA).can_fetch("https://www.musinsa.com/products/123")


def test_named_group_applies_to_that_bot_only():
    r = Robots(MUSINSA, "ClaudeBot/1.0")
    assert r.can_fetch("https://www.musinsa.com/products/123")
    assert not r.can_fetch("https://www.musinsa.com/auth/login")


def test_path_wildcards():
    r = Robots(UNIQLO, UA)
    assert r.can_fetch("https://www.uniqlo.com/kr/ko/products/E123456-000")
    assert not r.can_fetch("https://www.uniqlo.com/kr/ko/cart")
    assert not r.can_fetch("https://www.uniqlo.com/kr/ko/search?q=fleece")
    assert not r.can_fetch("https://www.uniqlo.com/kr/ko/men/tops?categoryIds=1")
    assert not r.can_fetch("https://www.uniqlo.com/kr/ko/men/tops?a=1&categoryIds=1")
    assert r.can_fetch("https://www.uniqlo.com/customize/tairyo/cart")  # 더 긴 Allow 우선


def test_dollar_anchor_and_empty_file():
    r = Robots("User-agent: *\nDisallow: /*.pdf$", UA)
    assert not r.can_fetch("https://x.com/a.pdf")
    assert r.can_fetch("https://x.com/a.pdf?v=1")
    assert Robots("", UA).can_fetch("https://x.com/anything")
