"""에잇세컨즈 공식몰 수집기. 로직은 SiteCollector, 셀렉터는 selectors.py, URL은 brands.yaml."""
from __future__ import annotations

from app.site_monitor.collectors.base_site import SiteCollector


class EightSecondsCollector(SiteCollector):
    source = "ssfshop.com"
