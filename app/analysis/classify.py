"""① 이벤트 분류·중요도 태깅 (매일 실행, 저비용 모델). 담당: C"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from app.analysis.llm_client import CLASSIFY_MODEL, ask_json
from app.core.models import Category, ChangeEvent

PROMPT = (Path(__file__).parent / "prompts" / "classify.md").read_text(encoding="utf-8")
BATCH_SIZE = 30


class _Label(BaseModel):
    id: int
    category: Category
    importance: int = Field(ge=1, le=5)
    note: str


class _Labels(BaseModel):
    labels: list[_Label]


def classify(events: list[ChangeEvent]) -> list[ChangeEvent]:
    by_id = {e.id: e for e in events}
    for i in range(0, len(events), BATCH_SIZE):
        batch = events[i : i + BATCH_SIZE]
        payload = "\n".join(
            e.model_dump_json(include={"id", "brand", "change_type", "title", "detail"})
            for e in batch
        )
        result = ask_json(CLASSIFY_MODEL, PROMPT, payload, _Labels, max_tokens=4096, effort="low")
        for lab in result.labels:
            if ev := by_id.get(lab.id):
                ev.category, ev.importance, ev.llm_note = lab.category, lab.importance, lab.note
    return events
