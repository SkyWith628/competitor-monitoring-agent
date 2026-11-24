"""[A] 네이버 쇼핑 검색 수집기 테스트 — 인터넷 없이 문서 형식대로 만든 가짜 응답으로."""
import json
from pathlib import Path

import pytest

from app.core.models import Brand, SourceType
from app.site_monitor import shopping
from app.site_monitor.shopping import DISPLAY, NaverShoppingCollector, auth_headers

SAMPLE = json.loads((Path(__file__).parents[1] / "fixtures" / "naver_shopping_spao.json").read_text(encoding="utf-8"))


def _collector(**cfg):
    conf = {"queries": ["스파오"], "brand_names": ["스파오", "SPAO"], **cfg}
    c = NaverShoppingCollector(Brand.SPAO, {"shopping": conf})
    c._fetch = lambda query, start: SAMPLE  # 진짜 API 대신 가짜 응답
    return c


def test_parses_products_and_filters_noise():
    items = _collector().collect()
    # 브랜드 칸이 빈 보세 상품(1003), 중고(1004), 가격 0(1005)은 빠진다
    assert [i.external_id for i in items] == ["1001", "1002", "1006"]
    first = items[0]
    assert first.title == "스파오 푸퍼 숏패딩 & 후드"  # <b> 태그·HTML 엔티티 정리
    assert first.price == 59900 and first.sale_price is None
    assert first.source_type == SourceType.SITE_PRODUCT and first.source == "naver_shopping"
    assert first.body == "패션의류 > 남성의류 > 점퍼 > 패딩"
    assert first.in_stock is None  # API에 재고 정보가 없다 → '모름'


def test_brand_name_match_ignores_case_and_spaces():
    c = NaverShoppingCollector(Brand.MUSINSA_STANDARD, {"shopping": {"queries": ["q"], "brand_names": ["무신사 스탠다드"]}})
    item = {**SAMPLE["items"][0], "brand": "무신사스탠다드"}
    c._fetch = lambda query, start: {"total": 1, "items": [item]}
    assert len(c.collect()) == 1


def test_malls_keep_only_official_sellers():
    items = _collector(malls=["스파오 공식스토어"]).collect()
    assert [i.external_id for i in items] == ["1001"]  # 재판매몰·가격비교 묶음은 빠진다


def test_same_product_from_two_queries_is_kept_once():
    assert len(_collector(queries=["스파오", "SPAO"]).collect()) == 3


def test_no_queries_means_skip():
    assert NaverShoppingCollector(Brand.SPAO, {}).collect() == []
    assert NaverShoppingCollector(Brand.SPAO, {"shopping": {"queries": []}}).collect() == []


def test_pages_until_max_items():
    calls = []

    def fetch(query, start):
        calls.append(start)
        items = [{**SAMPLE["items"][0], "productId": str(start + n)} for n in range(DISPLAY)]
        return {"total": 5000, "items": items}

    c = _collector(max_items=250)
    c._fetch = fetch
    assert len(c.collect()) == 250
    assert calls == [1, 101, 201]


def test_stops_at_last_page():
    calls = []

    def fetch(query, start):
        calls.append(start)
        return SAMPLE  # 6건 = 한 페이지(100건)가 안 참 → 마지막 페이지

    c = _collector()
    c._fetch = fetch
    c.collect()
    assert calls == [1]


def test_auth_headers_follow_the_api_address(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "news-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "news-secret")
    monkeypatch.delenv("NAVER_SHOP_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_SHOP_CLIENT_SECRET", raising=False)
    # 개발자센터 주소 → X-Naver-*, 쇼핑 전용 키가 없으면 뉴스 키를 쓴다
    assert auth_headers(shopping.DEFAULT_API_URL) == {"X-Naver-Client-Id": "news-id",
                                                      "X-Naver-Client-Secret": "news-secret"}
    # 그 밖의 주소(API HUB) → X-NCP-APIGW-*, 쇼핑 전용 키가 있으면 그것을 쓴다
    monkeypatch.setenv("NAVER_SHOP_CLIENT_ID", "shop-id")
    monkeypatch.setenv("NAVER_SHOP_CLIENT_SECRET", "shop-secret")
    assert auth_headers("https://naverapihub.apigw.ntruss.com/x") == {"X-NCP-APIGW-API-KEY-ID": "shop-id",
                                                                     "X-NCP-APIGW-API-KEY": "shop-secret"}


def test_missing_keys_raise_clear_error(monkeypatch):
    for key in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "NAVER_SHOP_CLIENT_ID", "NAVER_SHOP_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="NAVER_SHOP_CLIENT_ID"):
        auth_headers(shopping.DEFAULT_API_URL)


def test_api_error_shows_message_not_request(monkeypatch):
    import httpx

    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret-value")
    monkeypatch.setattr(shopping.httpx, "get", lambda *a, **k: httpx.Response(
        401, json={"errorMessage": "NID AUTH Result Invalid (1000) : Authentication failed.", "errorCode": "024"}))
    c = NaverShoppingCollector(Brand.SPAO, {"shopping": {"queries": ["스파오"]}})
    c.request_interval_sec = 0
    with pytest.raises(RuntimeError) as err:
        c.collect()
    assert "401" in str(err.value) and "Authentication failed" in str(err.value)
    assert "secret-value" not in str(err.value)
