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

3) 제목이 많이 달라도 요약까지 보면 같은 소식이면 중복 (같은 보도자료를 언론사마다 다르게 쓴 기사)
   예) "농심, 무신사와 이색 마케팅 전개" ≒ "[식음료 소식] KT&Gㆍ풀무원ㆍ농심" (요약이 둘 다 '새우깡 55주년 커스텀 티셔츠')
   - 제목+요약에서 조사를 뗀 단어를 모아, 겹치는 단어가 4개 이상이고 짧은 쪽 기준 30% 이상이면 같은 소식
   - 단, 제목의 지역이 서로 다르면 다른 소식 (2)의 예외와 같음)
   - 실데이터(2026-11-23 네이버+구글 7일): 무신사 스탠다드 77→36건, 유니클로 97→54건.
     묶인 기사는 모두 같은 소식이었다 (새우깡 협업 39건, 히트텍 기부·김장 봉사 54건이 제목 비교만으로는 남았음)

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


# ── 3) 요약까지 보는 비교 ──
TOPIC_THRESHOLD = 0.3  # 겹치는 단어 비율, 짧은 쪽 기준
MIN_SHARED_WORDS = 4  # 단어 몇 개가 우연히 겹치는 것('겨울', '할인')으로 묶이지 않게
JOSA = re.compile(r"(으로|에서|에게|까지|부터|과|와|은|는|이|가|을|를|의|에|도|로|만)$")
# 어느 기사에나 나오는 말은 같은 소식의 근거가 아니므로 뺀다
STOPWORDS = frozenset({
    "패션", "브랜드", "업계", "기자", "관계자", "올해", "지난", "이번", "통해", "위해", "따르면", "밝혔다", "대표",
    "국내", "함께", "진행", "선보인다", "운영", "제공", "출시", "매장", "고객", "소비자", "최근", "대한", "있는",
    "있다", "했다", "한다", "사진", "the", "of", "and", "in", "to", "for",
})


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


def _words(text: str, ignore: list[str]) -> set[str]:
    """'농심, 무신사 스탠다드와 새우깡 55주년…' → {'농심', '새우깡', '55주년', ...} (브랜드명·조사·흔한 말 제외)"""
    t = text.lower()
    for word in ignore:
        t = t.replace(word.lower(), " ")
    words = (JOSA.sub("", w) for w in re.findall(r"[0-9a-z가-힣]+", t))
    return {w for w in words if len(w) >= 2 and w not in STOPWORDS}


def same_topic(a: set[str], b: set[str]) -> bool:
    shared = len(a & b)
    return shared >= MIN_SHARED_WORDS and shared / min(len(a), len(b)) >= TOPIC_THRESHOLD


def dedup(items: list[RawItem], ignore: list[str] | None = None) -> list[RawItem]:
    """ignore: 비교에서 뺄 단어 (보통 그 브랜드의 뉴스 키워드). 같은 소식이면 앞에 온 기사가 대표로 남는다."""
    ignore = ignore or []
    kept: list[RawItem] = []
    kept_cores: list[Core] = []
    kept_words: list[set[str]] = []
    seen_urls: set[str] = set()
    for item in items:
        url = normalize_url(item.url)
        if url in seen_urls:
            continue
        core = _core(item.title, ignore)
        if any(same_story(core, k) for k in kept_cores):
            continue
        words = _words(f"{item.title} {item.body or ''}", ignore)
        # 제목의 지역이 서로 다르면("제주 첫 매장" / "부산 첫 매장") 요약이 비슷해도 다른 소식
        if any(same_topic(words, kw) and not _conflict(core.places, kc.places)
               for kw, kc in zip(kept_words, kept_cores, strict=True)):
            continue
        seen_urls.add(url)
        kept.append(item)
        kept_cores.append(core)
        kept_words.append(words)
    return kept
