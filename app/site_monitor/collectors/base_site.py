"""브랜드 공식몰 공통 수집기: robots.txt 확인 → 요청(throttle) → 파싱. 담당: A

브랜드 수집기는 source 만 지정하면 된다. 셀렉터는 selectors.py, URL은 config/brands.yaml.
brands.yaml 의 site.render: js 이면 playwright 로 렌더링한다.
"""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx

from app.core.base_collector import USER_AGENT, BaseCollector
from app.core.models import RawItem
from app.site_monitor.parser import parse_page, parse_products
from app.site_monitor.robots import Robots
from app.site_monitor.selectors import SELECTORS

log = logging.getLogger(__name__)


class SiteCollector(BaseCollector):
    def __init__(self, brand, config):
        super().__init__(brand, config)
        self._robots: dict[str, Robots] = {}

    # --- 네트워크 (테스트에서 override) ---
    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self.throttle()
            try:
                resp = httpx.get(f"{origin}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=15)
                # 4xx = robots.txt 없음 → 허용, 5xx = 확인 불가 → 차단 (RFC 9309)
                text = resp.text if resp.status_code == 200 else (
                    "" if 400 <= resp.status_code < 500 else "User-agent: *\nDisallow: /")
            except httpx.HTTPError:
                text = "User-agent: *\nDisallow: /"  # 확인 불가 시 보수적으로 차단
            self._robots[origin] = Robots(text, USER_AGENT)
        return self._robots[origin].can_fetch(url)

    def fetch(self, url: str) -> str:
        self.throttle()
        if self.config.get("render") == "js":
            from playwright.sync_api import sync_playwright  # 필요할 때만 로드

            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    page = browser.new_page(user_agent=USER_AGENT)
                    page.goto(url, wait_until="networkidle", timeout=45000)
                    return page.content()
                finally:
                    browser.close()
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return resp.text

    # --- 수집 ---
    def collect(self) -> list[RawItem]:
        urls = self.config.get("urls") or []
        if not urls:
            raise ValueError(f"{self.brand.value}: config/brands.yaml 의 site.urls 가 비어 있음")
        selectors = SELECTORS[self.brand.value]
        items: dict[str, RawItem] = {}
        for url in urls:
            if not self._allowed(url):
                log.warning("robots.txt 금지 경로, 건너뜀: %s", url)
                continue
            html = self.fetch(url)
            found = parse_products(html, brand=self.brand, source=self.source, page_url=url,
                                   selectors=selectors)
            page = parse_page(html, brand=self.brand, source=self.source, page_url=url,
                              selectors=selectors)
            for it in [*found, *([page] if page else [])]:
                items[it.external_id] = it  # 여러 목록 페이지에 중복된 상품은 1건으로
            log.info("%s %s: 상품 %d개", self.brand.value, url, len(found))
        return list(items.values())
