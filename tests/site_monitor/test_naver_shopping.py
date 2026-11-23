"""[A] 네이버 쇼핑 API 수집기 테스트 — 가짜 응답만 사용, 실 API 호출 없음."""
import httpx
import pytest

from app.core.models import Brand, SourceType
from app.site_monitor.collectors import naver_shopping as ns
from app.site_monitor.collectors.naver_shopping import NaverShoppingCollector, clean


def _item(pid, title, price, brand="스파오", maker="", mall="SPAO", **kw):
    return {"title": title, "link": f"https://search.shopping.naver.com/catalog/{pid}",
            "image": f"https://shopping-phinf.pstatic.net/{pid}.jpg", "lprice": str(price), "hprice": "",
            "mallName": mall, "productId": pid, "productType": "2", "brand": brand, "maker": maker,
            "category1": "패션의류", "category2": "남성의류", "category3": "티셔츠", "category4": "", **kw}


def _collector(opts, pages):
    c = NaverShoppingCollector(Brand.SPAO, {"via": "naver_shopping", "naver_shopping": opts})
    c.request_interval_sec = 0
    calls = []

    def fake(params):
        calls.append(params)
        return pages[len(calls) - 1]

    c._request = fake
    return c, calls


def test_clean():
    assert clean("<b>스파오</b> 티셔츠 &amp; 팬츠") == "스파오 티셔츠 & 팬츠"
    assert clean(None) == ""


def test_maps_and_filters_brand():
    page = {"total": 4, "items": [
        _item("1", "<b>스파오</b> 크루넥 티셔츠", 19900),
        _item("2", "스파오 st 리셀 티셔츠", 9900, brand="기타브랜드", mall="리셀러샵"),  # 다른 브랜드
        _item("3", "SPAO 데님", 39900, brand="", maker=""),  # brand 비어있음 → 제목으로 판정
        _item("4", "스파오 가격없음", 0),  # 가격 0 → 버림
    ]}
    c, calls = _collector({"queries": ["스파오"], "brands": ["스파오", "SPAO"]}, [page])
    items = c.collect()
    assert [(i.external_id, i.title, i.price) for i in items] == [
        ("1", "스파오 크루넥 티셔츠", 19900), ("3", "SPAO 데님", 39900)]
    a = items[0]
    assert a.source == "naver_shopping" and a.source_type == SourceType.SITE_PRODUCT
    assert a.in_stock is None and a.sale_price is None
    assert a.image_url.endswith("/1.jpg") and a.body == "SPAO | 패션의류>남성의류>티셔츠"
    assert calls == [{"display": 100, "sort": "date", "query": "스파오", "start": 1}]


def test_mall_filter():
    page = {"total": 2, "items": [_item("1", "스파오 티", 19900, mall="SPAO"),
                                  _item("2", "스파오 티", 17900, mall="다른몰")]}
    c, _ = _collector({"queries": ["스파오"], "brands": ["스파오"], "malls": ["spao"]}, [page])
    assert [i.external_id for i in c.collect()] == ["1"]


def test_pagination_and_dedupe():
    p1 = {"total": 150, "items": [_item(str(n), "스파오 티", 10000 + n) for n in range(100)]}
    p2 = {"total": 150, "items": [_item(str(n), "스파오 티", 10000 + n) for n in range(90, 140)]}
    c, calls = _collector({"queries": ["스파오"], "brands": ["스파오"], "max_items": 1000}, [p1, p2])
    items = c.collect()
    assert [p["start"] for p in calls] == [1, 101]  # 2페이지째 50건 < 100 → 종료
    assert len(items) == 140  # 90~99 중복 제거


def test_max_items_limits_pages():
    full = {"total": 5000, "items": [_item(str(n), "스파오 티", 10000) for n in range(100)]}
    c, calls = _collector({"queries": ["스파오"], "max_items": 200}, [full, full, full])
    c.collect()
    assert len(calls) == 2


def test_requires_queries():
    c, _ = _collector({}, [])
    with pytest.raises(ValueError, match="queries"):
        c.collect()


def test_missing_keys(monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)
    c = NaverShoppingCollector(Brand.SPAO, {"naver_shopping": {"queries": ["스파오"]}})
    with pytest.raises(RuntimeError, match="NAVER_CLIENT_ID"):
        c.collect()


def test_request_retries_then_succeeds(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "test-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(ns.time, "sleep", lambda s: None)
    req = httpx.Request("GET", ns.API_URL)
    responses = [httpx.Response(429, request=req), httpx.Response(200, json={"items": []}, request=req)]
    seen_headers = []

    def fake_get(url, params, headers, timeout):
        seen_headers.append(headers)
        return responses.pop(0)

    monkeypatch.setattr(ns.httpx, "get", fake_get)
    c = NaverShoppingCollector(Brand.SPAO, {"naver_shopping": {}})
    c.request_interval_sec = 0
    assert c._request({"query": "x"}) == {"items": []}
    assert c.api_calls == 2
    assert seen_headers[0]["X-Naver-Client-Id"] == "test-id"


def test_request_auth_error(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "bad")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "bad")
    req = httpx.Request("GET", ns.API_URL)
    monkeypatch.setattr(ns.httpx, "get", lambda *a, **k: httpx.Response(401, request=req))
    c = NaverShoppingCollector(Brand.SPAO, {"naver_shopping": {}})
    c.request_interval_sec = 0
    with pytest.raises(RuntimeError, match="401"):
        c._request({"query": "x"})


def test_pipeline_picks_naver(monkeypatch):
    from app.site_monitor import pipeline

    cfg = {"spao": {"site": {"via": "naver_shopping", "naver_shopping": {"queries": ["스파오"]}}}}
    monkeypatch.setattr(pipeline, "brands_config", lambda: cfg)
    monkeypatch.setattr(NaverShoppingCollector, "collect", lambda self: [])
    used = {}
    monkeypatch.setattr(pipeline.repo, "latest_items", lambda b, source: used.setdefault("src", source) and [])
    monkeypatch.setattr(pipeline.repo, "save_items", lambda items: None)
    monkeypatch.setattr(pipeline.repo, "save_events", lambda ev: None)
    assert pipeline.run(Brand.SPAO) == []
    assert used["src"] == "naver_shopping"
