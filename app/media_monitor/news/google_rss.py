"""Google News RSS 수집기."""
from __future__ import annotations

from app.core.base_collector import BaseCollector
from app.core.models import RawItem


class GoogleNewsCollector(BaseCollector):
    source = "google_news"
    request_interval_sec = 1.0

    def collect(self) -> list[RawItem]:
        # TODO(B): feedparser 로 RSS 파싱 → RawItem(source_type=NEWS)
        raise NotImplementedError
