"""네이버 쇼핑 검색 API 수집기 — 브랜드 상품명·가격을 공식 API로 받는다. 담당: A

브랜드 공식몰을 직접 수집하지 않는 이유 (2026-11-24 확인)
    - 스파오(spao.com)·SSF샵(에잇세컨즈): robots.txt 가 허용 목록 밖의 봇에 `Disallow: /`
    - 무신사·유니클로: 봇 접속 자체를 차단 (403, 연결 끊김)
    → 프로젝트 규칙(robots.txt 준수, 공식 API 우선)에 따라 네이버 쇼핑 검색 API로 대체했다.

이 방식으로 알 수 있는 것 / 없는 것
    - 알 수 있음: 새로 등록된 상품(신상품), 가격 변동
    - 알 수 없음: 품절·재입고(API에 재고 정보 없음), 메인 배너·기획전 변경(페이지를 보지 않음)
    - 가격은 네이버 쇼핑의 `lprice`(최저가)다. 여러 쇼핑몰이 묶인 '가격비교 상품'은 공식몰이 아닌
      판매처의 최저가일 수 있으므로, `brands.yaml` 의 `malls` 에 공식 판매처 이름을 넣어 좁히기를 권장한다.

API 명세: https://developers.naver.com/docs/serviceapi/search/shopping/shopping.md
"""
from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

import httpx

from app.core.base_collector import BaseCollector
from app.core.config import env, env_key
from app.core.models import RawItem, SourceType

# 문서로 확인된 공식 주소(네이버 개발자센터). NAVER API HUB 에서 쇼핑 검색을 쓰는 경우에는
# 콘솔에 나오는 주소를 .env 의 NAVER_SHOP_API_URL 에 넣는다 (인증 헤더는 주소를 보고 자동 선택).
DEFAULT_API_URL = "https://openapi.naver.com/v1/search/shop.json"
DISPLAY = 100  # 한 번에 받을 상품 수 (API 최대값)
MAX_START = 1000  # API 가 허용하는 검색 시작 위치의 최댓값
DEFAULT_MAX_ITEMS = 300  # 검색어당 최신 등록순으로 몇 개까지 볼지
NORMAL_PRODUCT_TYPES = {1, 2, 3}  # 일반상품만 (4~6 중고, 7~9 단종, 10~12 판매예정 제외)


class NaverShoppingCollector(BaseCollector):
    source = "naver_shopping"
    request_interval_sec = 1.0

    def collect(self) -> list[RawItem]:
        cfg = self.config.get("shopping") or {}
        queries = cfg.get("queries") or []
        if not queries:  # brands.yaml 에 검색어가 아직 없으면 건너뛰기
            return []
        brand_names = {_norm(b) for b in cfg.get("brand_names") or []}
        malls = {_norm(m) for m in cfg.get("malls") or []}
        max_items = int(cfg.get("max_items") or DEFAULT_MAX_ITEMS)

        items: dict[str, RawItem] = {}
        for query in queries:
            for raw in self._search(query, max_items):
                # ① 일반상품만 (중고·단종·판매예정 제외)
                if int(raw.get("productType") or 0) not in NORMAL_PRODUCT_TYPES:
                    continue
                # ② 브랜드 칸이 우리 브랜드인 상품만 ('스파오 스타일' 같은 다른 브랜드 상품 제외)
                if brand_names and _norm(raw.get("brand")) not in brand_names:
                    continue
                # ③ 공식 판매처를 지정했으면 그 판매처 상품만
                if malls and _norm(raw.get("mallName")) not in malls:
                    continue
                product_id = str(raw.get("productId") or "")
                price = _to_int(raw.get("lprice"))
                if not product_id or not price:  # 가격 정보가 없으면(0) 비교할 수 없으니 버림
                    continue
                # ④ 두 검색어에 같이 걸린 상품은 한 번만 (먼저 본 것 유지)
                items.setdefault(product_id, RawItem(
                    brand=self.brand,
                    source_type=SourceType.SITE_PRODUCT,
                    source=self.source,
                    external_id=product_id,
                    url=raw.get("link") or "",
                    title=_clean(raw.get("title") or ""),
                    body=_category(raw) or None,
                    price=price,
                    image_url=raw.get("image") or None,
                ))
        return list(items.values())

    def _search(self, query: str, max_items: int) -> list[dict]:
        """최신 등록순으로 max_items 개까지 페이지를 넘기며 받는다."""
        out: list[dict] = []
        start = 1
        while len(out) < max_items and start <= MAX_START:
            page = self._fetch(query, start)
            got = page.get("items") or []
            out += got
            start += DISPLAY
            if len(got) < DISPLAY or start > int(page.get("total") or 0):
                break  # 마지막 페이지
        return out[:max_items]

    def _fetch(self, query: str, start: int) -> dict:
        """인터넷 접속은 여기서만 한다 (테스트 때 이 부분만 가짜로 바꿈)."""
        url = env("NAVER_SHOP_API_URL") or DEFAULT_API_URL
        self.throttle()
        r = httpx.get(url, headers=auth_headers(url), timeout=10,
                      # 중고·렌탈·해외직구는 브랜드의 가격 정책이 아니므로 뺀다
                      params={"query": query, "display": DISPLAY, "start": start, "sort": "date",
                              "exclude": "used:rental:cbshop"})
        if r.status_code != 200:
            raise RuntimeError(f"네이버 쇼핑 검색 API 오류 {r.status_code}: {_error_message(r)}")
        return r.json()


def auth_headers(url: str) -> dict[str, str]:
    """주소에 맞는 인증 헤더. 키는 NAVER_SHOP_CLIENT_ID/SECRET, 없으면 뉴스와 같은 NAVER_CLIENT_ID/SECRET."""
    client_id = env_key("NAVER_SHOP_CLIENT_ID") or env_key("NAVER_CLIENT_ID")
    secret = env_key("NAVER_SHOP_CLIENT_SECRET") or env_key("NAVER_CLIENT_SECRET")
    if not client_id or not secret:
        raise RuntimeError(".env 에 NAVER_SHOP_CLIENT_ID / NAVER_SHOP_CLIENT_SECRET (없으면 NAVER_CLIENT_ID / "
                           "NAVER_CLIENT_SECRET) 를 채워야 합니다")
    if (urlsplit(url).hostname or "").endswith("openapi.naver.com"):  # 네이버 개발자센터
        return {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": secret}
    return {"X-NCP-APIGW-API-KEY-ID": client_id, "X-NCP-APIGW-API-KEY": secret}  # NAVER API HUB


def _error_message(r: httpx.Response) -> str:
    """오류 응답(JSON)의 메시지만 꺼낸다. 개발자센터는 errorMessage, API HUB 는 error.message."""
    try:
        data = r.json()
        return data.get("errorMessage") or data["error"]["message"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return r.text[:200]


def _clean(text: str) -> str:
    """'<b>스파오</b> 푸퍼 &amp; 패딩' → '스파오 푸퍼 & 패딩'"""
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _norm(text: str | None) -> str:
    """비교용: 공백 제거 + 소문자 ('무신사 스탠다드' = '무신사스탠다드')."""
    return re.sub(r"\s+", "", text or "").lower()


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _category(raw: dict) -> str:
    """'패션의류 > 남성의류 > 니트/스웨터' (C가 어떤 상품군인지 알 수 있게 body 에 넣는다)."""
    return " > ".join(c for c in (raw.get(f"category{n}") for n in range(1, 5)) if c)
