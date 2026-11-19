"""중복 기사 병합 (통신사 기사 재배포 등). 담당: B"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from rapidfuzz import fuzz

from app.core.models import RawItem

TITLE_SIMILARITY = 90


def normalize_url(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", ""))


def dedup(items: list[RawItem]) -> list[RawItem]:
    kept: list[RawItem] = []
    seen_urls: set[str] = set()
    for item in items:
        url = normalize_url(item.url)
        if url in seen_urls:
            continue
        if any(fuzz.token_sort_ratio(item.title, k.title) >= TITLE_SIMILARITY for k in kept):
            continue
        seen_urls.add(url)
        kept.append(item)
    return kept
