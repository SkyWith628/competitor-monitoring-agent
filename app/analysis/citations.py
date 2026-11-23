"""근거 인용 [#event_id] 검사 — 환각 방지. 담당: C

LLM이 쓴 문장은 반드시 입력으로 준 이벤트 id를 인용해야 한다. 인용이 없거나, 입력에 없는 id를
인용한 문장(=지어낸 근거)은 리포트에서 뺀다.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

CITATION = re.compile(r"\[#(\d+)\]")


def cited_ids(text: str) -> set[int]:
    return {int(m) for m in CITATION.findall(text)}


def keep_grounded(sentences: Iterable[str], valid_ids: set[int]) -> list[str]:
    """인용이 하나 이상 있고, 인용한 id가 전부 valid_ids 안에 있는 문장만 남긴다 (순서 유지, 중복 제거)."""
    out: list[str] = []
    for s in sentences:
        s = " ".join(s.split())
        ids = cited_ids(s)
        if s and ids and ids <= valid_ids and s not in out:
            out.append(s)
    return out
