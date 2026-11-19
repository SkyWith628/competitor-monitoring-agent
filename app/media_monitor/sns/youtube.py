"""YouTube Data API v3 공식 채널 수집기."""
from __future__ import annotations

from app.core.base_collector import BaseCollector
from app.core.models import RawItem


class YoutubeCollector(BaseCollector):
    source = "youtube"
    request_interval_sec = 1.0

    def collect(self) -> list[RawItem]:
        # TODO(B): 채널 uploads 재생목록 → 최근 영상 → RawItem(SNS_POST, metrics={views, likes, comments})
        raise NotImplementedError
