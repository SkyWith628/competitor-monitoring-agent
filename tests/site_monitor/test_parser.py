"""[A] 파서/수집기 테스트 — 저장된 HTML만 사용, 실사이트 접속 없음."""
from pathlib import Path

import pytest

from app.core.models import Brand, SourceType
from app.site_monitor.collectors.spao import SpaoCollector
from app.site_monitor.parser import parse_page, parse_price, parse_products

HTML = (Path(__file__).parents[1] / "fixtures/html/sample_listing.html").read_text(encoding="utf-8")
SEL = {"product_card": "li.item", "name": ".nm", "price": ".pr", "sale_price": ".sale",
       "sold_out": ".so", "promo": ".banner", "id_attr": "data-pid"}
URL = "https://shop.example.com/list"


def test_parse_price():
    assert parse_price("39,900원") == 39900
    assert parse_price("품절") is None


def test_parse_products():
    items = parse_products(HTML, brand=Brand.SPAO, source="spao.com", page_url=URL, selectors=SEL)
    assert [i.external_id for i in items] == ["P100", "P200"]  # 광고 카드 제외
    a, b = items
    assert (a.price, a.sale_price, a.in_stock) == (39900, 29900, True)
    assert a.url == "https://shop.example.com/p/P100"
    assert a.image_url == "https://shop.example.com/img/100.jpg"
    assert (b.sale_price, b.in_stock) == (None, False)


def test_parse_page_snapshot():
    page = parse_page(HTML, brand=Brand.SPAO, source="spao.com", page_url=URL, selectors=SEL)
    assert page.source_type == SourceType.SITE_PAGE and "50%" in page.body


def test_empty_selectors_raise():
    with pytest.raises(ValueError, match="셀렉터"):
        parse_products(HTML, brand=Brand.SPAO, source="s", page_url=URL, selectors={})


def test_collector_with_fake_fetch(monkeypatch):
    monkeypatch.setattr("app.site_monitor.collectors.base_site.SELECTORS", {"spao": SEL})
    c = SpaoCollector(Brand.SPAO, {"urls": [URL]})
    monkeypatch.setattr(c, "_allowed", lambda url: True)
    monkeypatch.setattr(c, "fetch", lambda url: HTML)
    items = c.collect()
    assert {i.source_type for i in items} == {SourceType.SITE_PRODUCT, SourceType.SITE_PAGE}
    assert len(items) == 3


def test_collector_skips_disallowed(monkeypatch):
    c = SpaoCollector(Brand.SPAO, {"urls": [URL]})
    monkeypatch.setattr(c, "_allowed", lambda url: False)
    assert c.collect() == []


def test_uniqlo_real_selectors():
    from app.site_monitor.selectors import SELECTORS

    html = (Path(__file__).parents[1] / "fixtures/html/uniqlo_listing.html").read_text(encoding="utf-8")
    page = "https://www.uniqlo.com/kr/ko/men/tops"
    items = parse_products(html, brand=Brand.UNIQLO, source="uniqlo.com", page_url=page,
                           selectors=SELECTORS["uniqlo"])
    # 같은 상품이라도 색상 그룹(/00, /01)별로 가격이 달라 별개 상품으로 본다
    assert [(i.external_id, i.title, i.price) for i in items] == [
        ("E450179-000/00", "소프트브러시드크루넥T(긴팔)", 29900),
        ("E465185-000/00", "에어리즘코튼크루넥T(반팔)", 19900),
        ("E465185-000/01", "에어리즘코튼크루넥T(반팔)", 12900),
    ]
    a, _, c = items
    assert a.url == "https://www.uniqlo.com/kr/ko/products/E450179-000/00?colorDisplayCode=09"
    assert a.image_url.endswith("krgoods_09_450179_3x4.jpg?width=300")
    assert c.image_url.endswith("krgoods_09_465185_3x4.jpg?width=300")  # data-src, 색상칩 아님
    assert all(i.sale_price is None and i.in_stock for i in items)


def test_collector_requires_urls():
    with pytest.raises(ValueError, match="urls"):
        SpaoCollector(Brand.SPAO, {"urls": []}).collect()
