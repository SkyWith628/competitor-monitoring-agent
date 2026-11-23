"""WHO.A.U 공식몰 수집기 (카페24). 로직은 SiteCollector, 셀렉터는 selectors.py, URL은 brands.yaml.

수집 규칙: robots.txt 허용 경로만(정렬·필터·검색 URL 금지), 요청 간격 ≥ 5초, 하루 1회.
"""
from __future__ import annotations

from app.site_monitor.collectors.base_site import SiteCollector


class WhoauCollector(SiteCollector):
    source = "whoau.com"
    request_interval_sec = 5.0  # 학습용 직접 수집: 서버 부담을 줄이려 기본(3초)보다 느리게
