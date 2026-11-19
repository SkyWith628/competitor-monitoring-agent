"""Instagram Graph API business_discovery 수집기."""
from __future__ import annotations

from app.core.base_collector import BaseCollector
from app.core.models import RawItem


class InstagramCollector(BaseCollector):
    source = "instagram"
    request_interval_sec = 1.0

    def collect(self) -> list[RawItem]:
        # TODO(B): IG_BUSINESS_ACCOUNT_ID 로 business_discovery 호출 → 최근 게시물 → RawItem(SNS_POST, metrics={likes, comments})
        raise NotImplementedError
