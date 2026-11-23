"""중복 기사 병합 (통신사 기사 재배포, 같은 소식을 언론사마다 다른 제목으로 쓴 기사 등). 담당: B

판단 기준
1) URL이 같으면 (쿼리스트링 무시) 중복
2) 제목에서 브랜드명·기호를 뺀 "핵심 내용"을 글자 2개 묶음(bigram)으로 비교
   - 숫자가 서로 다르면 다른 소식     예) "3분기 매출 12% 증가" ≠ "4분기 매출 12% 감소"
   - 짧은 제목이 긴 제목에 (거의) 그대로 들어 있으면 같은 소식 (긴 쪽은 같은 소식을 자세히 쓴 것)
     예) "무신사 스탠다드, 제주 첫 매장" ≒ "무신사 스탠다드, 제주 첫 매장 연다…전국 45번째"
   - 그 밖에는 두 제목 전체(합집합) 기준으로 60% 이상 겹쳐야 같은 소식
     예) "제주 첫 매장 오픈" ≠ "부산 첫 매장 오픈" (양쪽에 서로 없는 내용이 있음)
"""
from __future__ import annotations

import re
from typing import NamedTuple
from urllib.parse import urlsplit, urlunsplit

from app.core.models import RawItem

OVERLAP_THRESHOLD = 0.6  # 핵심 내용 bigram 겹침 비율 (합집합 기준)
MIN_BIGRAMS = 3  # 핵심 내용이 너무 짧으면(예: "매장") 우연히 겹치기 쉬우므로 정확히 같을 때만 중복
# '짧은 제목이 긴 제목에 들어 있음'으로 합치려면 짧은 쪽이 이만큼은 길어야 한다.
# "신상 출시"(bigram 3개)처럼 짧으면 그 말이 들어간 모든 기사를 삼키므로 이 규칙을 쓰지 않는다
MIN_CONTAINED_BIGRAMS = 4
MAX_EXTRA_BIGRAMS = 1  # 짧은 쪽에만 있는 bigram 이 이 이하면 '들어 있음' (조사 한 글자 차이 등 허용)


def normalize_url(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", ""))


class Core(NamedTuple):
    """제목의 비교용 핵심 내용."""
    bigrams: set[str]
    numbers: set[str]


def _core(title: str, ignore: list[str]) -> Core:
    """'무신사 스탠다드, 제주 첫 매장' → bigrams {'제주', '주첫', '첫매', '매장'}, numbers {}"""
    t = title.lower()
    for word in ignore:  # 브랜드명은 모든 기사에 들어 있어서 비교에서 뺀다 ('8seconds'의 8도 여기서 빠짐)
        t = t.replace(word.lower(), " ")
    numbers = set(re.findall(r"\d+", t))
    t = re.sub(r"[\W_]", "", t)  # 기호·공백 제거
    return Core({t[i:i + 2] for i in range(len(t) - 1)}, numbers)


def same_story(a: Core, b: Core) -> bool:
    # 숫자가 서로 다르면 다른 소식 (한쪽에만 숫자가 있으면 덧붙인 정보일 수 있어서 통과)
    if a.numbers and b.numbers and not (a.numbers <= b.numbers or b.numbers <= a.numbers):
        return False
    short, long_ = sorted((a.bigrams, b.bigrams), key=len)
    if len(short) < MIN_BIGRAMS:
        return short == long_
    # 짧은 제목이 긴 제목에 (거의) 그대로 들어 있으면 같은 소식을 길게 쓴 기사
    if len(short) >= MIN_CONTAINED_BIGRAMS and len(short - long_) <= MAX_EXTRA_BIGRAMS:
        return True
    # 양쪽에 서로 없는 내용이 있으면 두 제목 전체 기준으로 충분히 겹쳐야 같은 소식
    return len(short & long_) / len(short | long_) >= OVERLAP_THRESHOLD


def dedup(items: list[RawItem], ignore: list[str] | None = None) -> list[RawItem]:
    """ignore: 제목 비교에서 뺄 단어 (보통 그 브랜드의 뉴스 키워드)."""
    ignore = ignore or []
    kept: list[RawItem] = []
    kept_cores: list[Core] = []
    seen_urls: set[str] = set()
    for item in items:
        url = normalize_url(item.url)
        if url in seen_urls:
            continue
        core = _core(item.title, ignore)
        if any(same_story(core, k) for k in kept_cores):
            continue
        seen_urls.add(url)
        kept.append(item)
        kept_cores.append(core)
    return kept
