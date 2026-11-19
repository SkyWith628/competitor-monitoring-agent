"""Claude API 래퍼. 모든 LLM 호출은 여기를 거친다. 담당: C"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from app.core.config import env

CLASSIFY_MODEL = env("CLASSIFY_MODEL", "claude-haiku-5-5")
REPORT_MODEL = env("REPORT_MODEL", "claude-opus-5-5")

T = TypeVar("T", bound=BaseModel)


class LLMRefusal(RuntimeError):
    pass


@lru_cache
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()  # ANTHROPIC_API_KEY 를 환경변수에서 읽음


def _call(model: str, system: str, user: str, max_tokens: int, effort: str):
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "output_config": {"effort": effort},
    }
    if model.startswith("claude-opus"):
        # 안전 분류기가 거절하면 서버가 대체 모델로 자동 재시도
        return _client().beta.messages.create(
            **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
        )
    return _client().messages.create(**kwargs)


def ask_json(model: str, system: str, user: str, schema: type[T], *,
             max_tokens: int = 16000, effort: str = "medium", retries: int = 1) -> T:
    """JSON으로 답하게 하고 pydantic으로 검증. 실패 시 retries 만큼 재시도."""
    last_err: Exception | None = None
    for _ in range(retries + 1):
        resp = _call(model, system, user, max_tokens, effort)
        if resp.stop_reason == "refusal":
            raise LLMRefusal(str(resp.stop_details))
        log_usage(model, resp.usage)
        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            return schema.model_validate(json.loads(_strip_fence(text)))
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = e
    raise ValueError(f"LLM JSON 검증 실패: {last_err}")


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    return t


def log_usage(model: str, usage) -> None:
    # TODO(C): infra.storage.repo.save_llm_call(model, usage.input_tokens, usage.output_tokens)
    pass
