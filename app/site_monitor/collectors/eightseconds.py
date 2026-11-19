"""에잇세컨즈 공식몰 수집기."""
from __future__ import annotations

from app.core.base_collector import BaseCollector
from app.core.models import RawItem


class EightSecondsCollector(BaseCollector):
    source = "ssfshop.com"

    def collect(self) -> list[RawItem]:
        # TODO(A): brands.yaml 의 site.urls 순회
        #   1) 내부 JSON API가 있으면 httpx 로 호출, 없으면 playwright 로 렌더링
        #   2) selectors.py 의 셀렉터로 상품(SITE_PRODUCT) / 페이지(SITE_PAGE) 파싱
        #   3) 요청마다 self.throttle()
        raise NotImplementedError
