"""Claude API 래퍼. 모든 LLM 호출은 여기를 거친다. 담당: C"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from app.core.config import env, env_key

CLASSIFY_MODEL = env("CLASSIFY_MODEL", "claude-haiku-5-5")
REPORT_MODEL = env("REPORT_MODEL", "claude-opus-5-5")

T = TypeVar("T", bound=BaseModel)
log = logging.getLogger(__name__)


class LLMTruncated(ValueError):
    """응답이 max_tokens 에 걸려 중간에서 끊겼다. 같은 요청을 다시 보내도 또 끊기므로 재시도하지 않는다.

    부르는 쪽이 입력을 줄여(예: 묶음을 반으로 나눠) 다시 물어야 한다.
    """


class LLMRefusal(RuntimeError):
    pass


@lru_cache
def _client() -> anthropic.Anthropic:
    # env_key() 로 읽어 값 끝의 줄바꿈을 잘라낸다. 값이 없으면(None) SDK 가 다른 인증 수단을 찾는다
    return anthropic.Anthropic(api_key=env_key("ANTHROPIC_API_KEY"))


def _call(model: str, system: str, user: str, max_tokens: int, effort: str):
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        # 시스템 프롬프트(=prompts/*.md)는 배치마다 같으므로 캐시해 재사용한다.
        # 모델별 최소 길이보다 짧으면 캐시되지 않을 뿐 오류는 없다.
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
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
        log_usage(getattr(resp, "model", None) or model, resp.usage)  # 대체 모델로 응답했으면 그 모델로
        if resp.stop_reason == "refusal":  # 거절돼도 토큰은 썼으니 기록은 먼저
            raise LLMRefusal(str(resp.stop_details))
        if resp.stop_reason == "max_tokens":
            # 생각(thinking) 토큰도 max_tokens 에 포함된다. JSON 이 중간에서 끊겨 파싱할 수 없다
            raise LLMTruncated(f"LLM 응답이 max_tokens({max_tokens})에 걸려 끊겼습니다 "
                               f"(출력 {getattr(resp.usage, 'output_tokens', '?')} 토큰)")
        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            return schema.model_validate(json.loads(_strip_fence(text)))
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = e
    raise ValueError(f"LLM JSON 검증 실패 (stop_reason={resp.stop_reason}): {last_err}")


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    return t


def log_usage(model: str, usage) -> None:
    """토큰 사용량을 llm_calls 에 기록. 기록 실패가 분석을 멈추게 하지는 않는다.

    입력 토큰은 캐시에서 읽은/캐시에 쓴 토큰까지 합친 실제 처리량으로 남긴다
    (usage.input_tokens 는 캐시되지 않은 부분만 센다).
    """
    input_tokens = (
        (usage.input_tokens or 0)
        + (getattr(usage, "cache_read_input_tokens", None) or 0)
        + (getattr(usage, "cache_creation_input_tokens", None) or 0)
    )
    try:
        from app.infra.storage import repo

        repo.save_llm_call(model, input_tokens, usage.output_tokens or 0)
    except Exception:  # DB 문제로 리포트가 실패하면 안 된다
        log.warning("LLM 사용량 기록 실패 (model=%s)", model, exc_info=True)
