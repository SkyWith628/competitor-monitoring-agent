"""중복 기사 병합 (통신사 기사 재배포, 같은 소식을 언론사마다 다른 제목으로 쓴 기사 등). 담당: B

판단 기준
1) URL이 같으면 (쿼리스트링 무시) 중복
2) 제목에서 브랜드명·기호를 뺀 "핵심 내용"의 글자 2개 묶음(bigram)이 **짧은 쪽 기준 60%** 이상 겹치면 같은 소식
   예) "무신사 스탠다드, 제주 첫 매장" ≒ "무신사 스탠다드, 제주 첫 매장 연다…전국 45번째"
   단, 아래는 겹쳐도 다른 소식으로 본다 (리뷰 #6에서 나온 경계 사례)
   - 숫자가 서로 다름      "3분기 매출 12% 증가" ≠ "4분기 매출 12% 감소"
   - 지역이 서로 다름      "제주 첫 매장 오픈" ≠ "부산 첫 매장 오픈"
   - 핵심 내용이 너무 짧음  "유니클로 신상 출시"("신상출시")는 정확히 같은 제목만 중복
     (짧으면 그 말이 들어간 모든 기사와 100% 겹쳐서 다른 소식을 삼키고, 결과가 수집 순서에 따라 달라짐)

기준은 실데이터(2026-11-23 구글 뉴스 무신사 스탠다드 51건·유니클로 67건)로 확인했다.
'두 제목 전체(합집합) 기준'은 같은 소식을 다른 표현으로 쓴 기사를 너무 많이 놓쳐서(26→39건, 45→57건)
짧은 쪽 기준을 유지하고 위 예외만 더했다. 묶인 기사들은 모두 실제로 같은 소식이었다.
"""
from __future__ import annotations

import re
from typing import NamedTuple
from urllib.parse import urlsplit, urlunsplit

from app.core.models import RawItem

OVERLAP_THRESHOLD = 0.6  # 핵심 내용 bigram 겹침 비율, 짧은 쪽 기준 (실데이터로 튜닝: 0.5는 다른 소식까지 묶임)
MIN_BIGRAMS = 4  # 핵심 내용이 이보다 짧으면(예: "신상 출시" = 3개) 우연히 겹치기 쉬우므로 정확히 같을 때만 중복
# 두 제목에 서로 다른 지역이 나오면 다른 소식 (같은 틀의 "OO 첫 매장 오픈" 구분용).
# '경기'(경기 침체)처럼 다른 뜻으로 자주 쓰이는 말은 넣지 않는다
PLACES = ("서울", "부산", "대구", "인천", "광주", "대전", "울산", "충북", "충남", "전북", "전남", "경북", "경남",
          "강원", "제주", "강남", "홍대", "명동", "성수", "잠실", "여의도", "판교", "수원", "창원", "청주", "전주",
          "천안", "김해", "거제", "일본", "중국", "미국", "대만", "태국", "베트남")


def normalize_url(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", ""))


class Core(NamedTuple):
    """제목의 비교용 핵심 내용."""
    bigrams: set[str]
    numbers: set[str]
    places: set[str]


def _core(title: str, ignore: list[str]) -> Core:
    """'무신사 스탠다드, 제주 첫 매장' → bigrams {'제주', '주첫', '첫매', '매장'}, numbers {}, places {'제주'}"""
    t = _normalize_numbers(title.lower())
    for word in ignore:  # 브랜드명은 모든 기사에 들어 있어서 비교에서 뺀다 ('8seconds'의 8도 여기서 빠짐)
        t = t.replace(word.lower(), " ")
    numbers = set(re.findall(r"\d+", t))
    places = {p for p in PLACES if p in t}
    t = re.sub(r"[\W_]", "", t)  # 기호·공백 제거
    return Core({t[i:i + 2] for i in range(len(t) - 1)}, numbers, places)


def _normalize_numbers(t: str) -> str:
    """'3만5천' 과 '3만5000' 을 같은 숫자로 보이게 ('5천' → '5000', '3백' → '300')."""
    t = re.sub(r"(\d+)천", lambda m: str(int(m.group(1)) * 1000), t)
    return re.sub(r"(\d+)백", lambda m: str(int(m.group(1)) * 100), t)


def _conflict(a: set[str], b: set[str]) -> bool:
    """양쪽에 다 있는데 서로 다르면 충돌. 한쪽에만 있으면 덧붙인 정보일 수 있어서 충돌 아님."""
    return bool(a and b and not (a <= b or b <= a))


def same_story(a: Core, b: Core) -> bool:
    if _conflict(a.numbers, b.numbers) or _conflict(a.places, b.places):
        return False
    if min(len(a.bigrams), len(b.bigrams)) < MIN_BIGRAMS:
        return a.bigrams == b.bigrams
    # 짧은 제목이 긴 제목에 거의 다 들어 있어도 같은 소식으로 보기 위해 작은 쪽 기준으로 나눈다
    return len(a.bigrams & b.bigrams) / min(len(a.bigrams), len(b.bigrams)) >= OVERLAP_THRESHOLD


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
