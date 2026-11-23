"""뉴스 수집기(네이버·구글) 공통 관련성 기준. 담당: B

어느 수집기로 들어왔든 같은 기준으로 살리고 버린다.
1) 브랜드명(키워드)이 제목이나 요약 어디에도 없으면 버린다
   - 네이버는 검색어 주변 문장을 요약으로 줘서, 제목에 없어도 요약에 있으면 살아남는다
   - 구글 RSS는 요약이 없어 제목만 본다. 실데이터(2026-11-23)에서 구글이 이 기준으로 버린 18건 중
     16건은 네이버에 같은 소식이 요약 근거와 함께 들어와 있었다
2) 제외어가 제목이나 요약에 있으면 버린다
"""
from __future__ import annotations


def _text(*parts: str | None) -> str:
    return " ".join(p for p in parts if p).lower()


def mentions_brand(keywords: list[str], title: str, summary: str | None = None) -> bool:
    """'스파오는 29일까지 블프…'(요약) → True / '8 Myths About Watches' → False"""
    text = _text(title, summary)
    return any(k.lower() in text for k in keywords)


def has_excluded(exclude: list[str], title: str, summary: str | None = None) -> bool:
    text = _text(title, summary)
    return any(word.lower() in text for word in exclude)
