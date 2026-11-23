"""네이버 쇼핑 검색 API 수집기 (공식몰 직접 수집이 막힌 브랜드용 대체 소스). 담당: A

공식 API: GET https://openapi.naver.com/v1/search/shop.json
  헤더 X-Naver-Client-Id / X-Naver-Client-Secret (.env 의 NAVER_SHOP_CLIENT_ID/SECRET, 없으면 NAVER_CLIENT_ID/SECRET)
  display ≤ 100, start ≤ 1000, sort = sim|date|asc|dsc

config/brands.yaml 예시 (site 아래):
    site:
      via: naver_shopping
      naver_shopping:
        queries: ["스파오"]          # 검색어 여러 개 가능
        brands: ["스파오", "SPAO"]   # 응답 brand/maker 가 이 중 하나여야 함 (리셀러 제거)
        malls: []                   # 비우면 판매처 무관, 지정하면 mallName 일치만
        max_items: 300              # 검색어당 최대 (≤ 1000)
        sort: date

한계: 재고/품절 정보 없음(in_stock=None) → 품절·재입고 이벤트는 생기지 않는다.
      lprice 는 '최저가'라 판매처가 여러 곳이면 공식몰 가격과 다를 수 있다 → malls 로 좁힐 것.
⚠ 응답 필드·한도는 공식 문서로 아직 검증하지 못했다(ROBOTS.md 참고). 키 발급 후 실호출로 확인.
"""
from __future__ import annotations

import html
import logging
import re
import time

import httpx

from app.core.base_collector import BaseCollector
from app.core.config import env
from app.core.models import RawItem, SourceType

log = logging.getLogger(__name__)

API_URL = "https://openapi.naver.com/v1/search/shop.json"
PAGE_SIZE = 100
MAX_START = 1000
RETRY_STATUSES = {429, 500, 502, 503, 504}
_TAG = re.compile(r"<[^>]+>")


def clean(text: str | None) -> str:
    """'<b>스파오</b> 티셔츠 &amp; 팬츠' → '스파오 티셔츠 & 팬츠'"""
    return html.unescape(_TAG.sub("", text or "")).strip()


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", "", clean(s)).lower()


class NaverShoppingCollector(BaseCollector):
    source = "naver_shopping"
    request_interval_sec = 0.5  # API 초당 호출 제한 대비
    max_retries = 3

    def __init__(self, brand, config):
        super().__init__(brand, config)
        self.opts = config.get("naver_shopping") or {}
        self.api_calls = 0  # 일일 쿼터 확인용 (로그로 남김)

    # --- 네트워크 (테스트에서 override) ---
    def _request(self, params: dict) -> dict:
        # 쇼핑 전용 키(NAVER_SHOP_*) 우선, 없으면 공용 키. B 구역의 NCP 키와 섞이지 않게 분리.
        cid = env("NAVER_SHOP_CLIENT_ID") or env("NAVER_CLIENT_ID")
        secret = env("NAVER_SHOP_CLIENT_SECRET") or env("NAVER_CLIENT_SECRET")
        if not cid or not secret:
            raise RuntimeError(
                ".env 에 NAVER_SHOP_CLIENT_ID / NAVER_SHOP_CLIENT_SECRET 가 없음 "
                "(developers.naver.com 에서 발급한 검색 API 키)"
            )
        headers = {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret}
        for attempt in range(1, self.max_retries + 1):
            self.throttle()
            self.api_calls += 1
            try:
                resp = httpx.get(API_URL, params=params, headers=headers, timeout=15)
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
            else:
                if resp.status_code not in RETRY_STATUSES:
                    if resp.status_code in (401, 403):
                        raise RuntimeError(f"네이버 API 인증/권한 오류 {resp.status_code}: "
                                           "키 값과 '검색' API 사용 설정을 확인")
                    resp.raise_for_status()
                    return resp.json()
                if attempt == self.max_retries:
                    resp.raise_for_status()
            time.sleep(2 ** attempt)  # 1회차 실패 2초, 2회차 4초 후 재시도
        raise AssertionError("unreachable")

    # --- 필터/변환 ---
    def _is_brand_item(self, item: dict) -> bool:
        names = {_norm(b) for b in self.opts.get("brands") or []}
        malls = {_norm(m) for m in self.opts.get("malls") or []}
        if malls and _norm(item.get("mallName")) not in malls:
            return False
        if not names:
            return True
        fields = [item.get("brand"), item.get("maker")]
        if any(fields):
            return any(_norm(f) in names for f in fields if f)
        # brand/maker 필드가 비어 있으면 제목에 브랜드명이 들어있는지로 판단
        title = _norm(item.get("title"))
        return any(n in title for n in names)

    def _to_raw(self, item: dict) -> RawItem | None:
        try:
            price = int(item.get("lprice") or 0)
        except ValueError:
            price = 0
        pid, link = str(item.get("productId") or ""), item.get("link") or ""
        if not pid or not link or price <= 0:
            return None
        categories = [item.get(f"category{i}") for i in range(1, 5)]
        body = " | ".join(filter(None, [item.get("mallName"), ">".join(filter(None, categories))]))
        return RawItem(
            brand=self.brand, source_type=SourceType.SITE_PRODUCT, source=self.source,
            external_id=pid, url=link, title=clean(item.get("title")), body=body or None,
            price=price, in_stock=None, image_url=item.get("image") or None,
        )

    # --- 수집 ---
    def collect(self) -> list[RawItem]:
        queries = self.opts.get("queries") or []
        if not queries:
            raise ValueError(f"{self.brand.value}: brands.yaml site.naver_shopping.queries 가 비어 있음")
        limit = min(int(self.opts.get("max_items", 300)), MAX_START + PAGE_SIZE - 1)
        params_base = {"display": PAGE_SIZE, "sort": self.opts.get("sort", "date")}
        if self.opts.get("exclude"):
            params_base["exclude"] = self.opts["exclude"]  # 예: "used:rental:cbshop"

        found: dict[str, RawItem] = {}
        for query in queries:
            seen = kept = 0
            start = 1
            while start <= MAX_START and seen < limit:
                data = self._request({**params_base, "query": query, "start": start})
                page = data.get("items") or []
                seen += len(page)
                for raw in page:
                    if self._is_brand_item(raw) and (it := self._to_raw(raw)):
                        found[it.external_id] = it
                        kept += 1
                if len(page) < PAGE_SIZE or start + PAGE_SIZE > int(data.get("total") or 0):
                    break
                start += PAGE_SIZE
            log.info("%s 네이버쇼핑 '%s': 조회 %d건 중 브랜드 상품 %d건", self.brand.value, query, seen, kept)
        log.info("%s 네이버쇼핑 API 호출 %d회 (일일 한도 확인용)", self.brand.value, self.api_calls)
        return list(found.values())
