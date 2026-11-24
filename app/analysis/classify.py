"""① 이벤트 분류·중요도 태깅 (매일 실행, 저비용 모델). 담당: C"""
from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from app.analysis.features import event_line
from app.analysis.llm_client import CLASSIFY_MODEL, LLMTruncated, ask_json
from app.core.models import Category, ChangeEvent

PROMPT = (Path(__file__).parent / "prompts" / "classify.md").read_text(encoding="utf-8")
BATCH_SIZE = 30
# 생각(thinking) 토큰도 여기에 포함된다. 4096 은 30건 묶음에서 응답이 중간에 끊겼다 (2026-11-24 첫 실제 실행)
MAX_TOKENS = 16000
log = logging.getLogger(__name__)


class _Label(BaseModel):
    id: int
    category: Category
    importance: int = Field(ge=1, le=5)
    note: str


class _Labels(BaseModel):
    labels: list[_Label]


def _apply(batch: list[ChangeEvent]) -> list[ChangeEvent]:
    """한 배치를 분류해 결과를 채우고, 라벨을 못 받은 이벤트를 돌려준다."""
    by_id = {e.id: e for e in batch}
    payload = "\n".join(event_line(e) for e in batch)
    try:
        result = ask_json(CLASSIFY_MODEL, PROMPT, payload, _Labels, max_tokens=MAX_TOKENS, effort="low")
    except LLMTruncated:
        if len(batch) == 1:
            raise  # 한 건도 못 담으면 묶음 크기 문제가 아니다
        # 응답이 끊겼으면 묶음을 반으로 나눠 다시 묻는다
        half = len(batch) // 2
        log.warning("분류 응답이 끊겨 %d건 묶음을 %d건·%d건으로 나눠 다시 묻습니다", len(batch), half, len(batch) - half)
        return _apply(batch[:half]) + _apply(batch[half:])
    for lab in result.labels:
        if (ev := by_id.pop(lab.id, None)) is None:
            continue  # 입력에 없던 id 이거나 같은 id 중복 — 무시
        importance = 1 if lab.category == Category.IRRELEVANT else lab.importance  # 무관은 항상 1
        ev.category, ev.importance, ev.llm_note = lab.category, importance, lab.note
    return list(by_id.values())


def classify(events: list[ChangeEvent]) -> list[ChangeEvent]:
    """이벤트에 category·importance·llm_note 를 채워 돌려준다.

    모델이 일부 id를 빠뜨리면 빠진 것만 한 번 더 묻는다. 그래도 남으면 category 가 비어 있는
    채로 두며, repo 의 미분류 조회에 다시 잡혀 다음 실행에서 재시도된다.
    """
    for i in range(0, len(events), BATCH_SIZE):
        missing = _apply(events[i : i + BATCH_SIZE])
        if missing:
            missing = _apply(missing)
        if missing:
            log.warning("분류 라벨 누락 %d건 (다음 실행에서 재시도): %s", len(missing), [e.id for e in missing])
    return events
