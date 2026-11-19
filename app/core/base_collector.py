"""모든 수집기(A: 사이트, B: 뉴스·SNS)가 구현하는 인터페이스."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

from app.core.models import Brand, RawItem

USER_AGENT = "CompetitorMonitorBot/0.1 (internal research)"


class BaseCollector(ABC):
    source: str  # 하위 클래스에서 지정 (예: "spao.com")
    request_interval_sec: float = 3.0  # 요청 간격 — robots/약관 준수

    def __init__(self, brand: Brand, config: dict):
        self.brand = brand
        self.config = config
        self._last_request = 0.0

    def throttle(self) -> None:
        wait = self.request_interval_sec - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    @abstractmethod
    def collect(self) -> list[RawItem]:
        """수집 결과를 RawItem 리스트로 반환. 실패 시 예외를 던지면 D가 운영 알림 처리."""
