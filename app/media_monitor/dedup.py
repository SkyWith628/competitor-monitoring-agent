"""중복 기사 병합 (통신사 기사 재배포, 같은 소식을 언론사마다 다른 제목으로 쓴 기사 등). 담당: B

판단 기준
1) URL이 같으면 (쿼리스트링 무시) 중복
2) 제목에서 브랜드명·기호를 뺀 "핵심 내용"의 글자 2개 묶음(bigram)이 60% 이상 겹치면 같은 소식
   예) "무신사 스탠다드, 제주 첫 매장" ≒ "무신사 스탠다드, 제주 첫 매장 연다…전국 45번째"
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from app.core.models import RawItem

OVERLAP_THRESHOLD = 0.6  # 핵심 내용 bigram 겹침 비율 (실데이터로 튜닝: 0.5는 다른 소식까지 묶임)
MIN_BIGRAMS = 3  # 핵심 내용이 너무 짧으면(예: "매장") 우연히 겹치기 쉬우므로 정확히 같을 때만 중복


def normalize_url(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", ""))


def _core_bigrams(title: str, ignore: list[str]) -> set[str]:
    """'무신사 스탠다드, 제주 첫 매장' → {'제주', '주첫', '첫매', '매장'}"""
    t = title.lower()
    for word in ignore:  # 브랜드명은 모든 기사에 들어 있어서 비교에서 뺀다
        t = t.replace(word.lower(), " ")
    t = re.sub(r"[\W_]", "", t)  # 기호·공백 제거
    return {t[i:i + 2] for i in range(len(t) - 1)}


def same_story(a: set[str], b: set[str]) -> bool:
    if min(len(a), len(b)) < MIN_BIGRAMS:
        return a == b
    # 짧은 제목이 긴 제목에 거의 다 들어 있어도 같은 소식으로 보기 위해 작은 쪽 기준으로 나눈다
    return len(a & b) / min(len(a), len(b)) >= OVERLAP_THRESHOLD


def dedup(items: list[RawItem], ignore: list[str] | None = None) -> list[RawItem]:
    """ignore: 제목 비교에서 뺄 단어 (보통 그 브랜드의 뉴스 키워드)."""
    ignore = ignore or []
    kept: list[RawItem] = []
    kept_cores: list[set[str]] = []
    seen_urls: set[str] = set()
    for item in items:
        url = normalize_url(item.url)
        if url in seen_urls:
            continue
        core = _core_bigrams(item.title, ignore)
        if any(same_story(core, k) for k in kept_cores):
            continue
        seen_urls.add(url)
        kept.append(item)
        kept_cores.append(core)
    return kept
