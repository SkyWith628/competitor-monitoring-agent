"""네이버 검색 API(뉴스) 수집기."""
from __future__ import annotations

from app.core.base_collector import BaseCollector
from app.core.models import RawItem


class NaverNewsCollector(BaseCollector):
    source = "naver_news"
    request_interval_sec = 1.0

    def collect(self) -> list[RawItem]:
        # TODO(B): keywords 로 검색 → exclude 키워드 필터 → RawItem(source_type=NEWS, external_id=url 해시)
        raise NotImplementedError
