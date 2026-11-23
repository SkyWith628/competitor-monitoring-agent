"""[C] llm_client — 실제 API 없이 가짜 응답으로 토큰 기록·캐싱 설정을 확인한다."""
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.analysis import llm_client
from app.infra.storage import db, repo


class _Out(BaseModel):
    ok: bool


def _resp(text='{"ok": true}', stop_reason="end_turn", model="claude-haiku-5-5", **usage):
    u = {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 0,
         "cache_creation_input_tokens": 0, **usage}
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason,
                           stop_details=None, model=model, usage=SimpleNamespace(**u))


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REQUIRE_DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "monitor.db")
    db.init_db()


@pytest.fixture
def fake_call(monkeypatch):
    sent, replies = [], []

    def _fake(model, system, user, max_tokens, effort):
        sent.append({"model": model, "system": system})
        return replies.pop(0)

    monkeypatch.setattr(llm_client, "_call", _fake)
    return sent, replies


def test_records_tokens_including_cache(fresh_db, fake_call):
    _, replies = fake_call
    replies.append(_resp(cache_read_input_tokens=900, cache_creation_input_tokens=50))
    assert llm_client.ask_json("claude-haiku-5-5", "sys", "u", _Out).ok
    assert repo.llm_usage() == [{"model": "claude-haiku-5-5", "calls": 1,
                                 "input_tokens": 1050, "output_tokens": 20}]


def test_retry_records_every_call(fresh_db, fake_call):
    _, replies = fake_call
    replies.extend([_resp(text="not json"), _resp()])
    llm_client.ask_json("claude-haiku-5-5", "sys", "u", _Out)
    assert repo.llm_usage()[0]["calls"] == 2


def test_refusal_is_recorded_then_raised(fresh_db, fake_call):
    _, replies = fake_call
    replies.append(_resp(stop_reason="refusal", model="claude-opus-5-5"))
    with pytest.raises(llm_client.LLMRefusal):
        llm_client.ask_json("claude-opus-5-5", "sys", "u", _Out)
    assert repo.llm_usage()[0]["model"] == "claude-opus-5-5"


def test_db_failure_does_not_break_analysis(fake_call, monkeypatch):
    _, replies = fake_call
    replies.append(_resp())

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(repo, "save_llm_call", boom)
    assert llm_client.ask_json("claude-haiku-5-5", "sys", "u", _Out).ok


def test_system_prompt_is_cached(monkeypatch):
    captured = {}

    class _Messages:
        def create(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm_client, "_client", lambda: SimpleNamespace(messages=_Messages()))
    llm_client._call("claude-haiku-5-5", "분류 규칙", "u", 100, "low")
    assert captured["system"] == [{"type": "text", "text": "분류 규칙", "cache_control": {"type": "ephemeral"}}]
