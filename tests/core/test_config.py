"""[공통] 환경 변수 읽기 — 붙여 넣기 실수(끝 줄바꿈, 두 줄)에 수집이 죽지 않게."""
import pytest

from app.core.config import env, env_key


def test_env_strips_surrounding_whitespace(monkeypatch):
    monkeypatch.setenv("SOME_KEY", "  abc123\n")  # GitHub Secrets 에 값 끝 줄바꿈이 딸려 들어온 경우
    assert env("SOME_KEY") == "abc123"


def test_empty_or_missing_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("SOME_KEY", " \n")
    monkeypatch.delenv("OTHER_KEY", raising=False)
    assert env("SOME_KEY") is None and env("SOME_KEY", "기본값") == "기본값"
    assert env("OTHER_KEY") is None and env("OTHER_KEY", "기본값") == "기본값"


def test_multiline_value_names_the_key_without_showing_it(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "first-secret\nsecond-secret\n")  # 두 값을 한 칸에 붙여 넣은 경우
    with pytest.raises(RuntimeError) as err:
        env_key("YOUTUBE_API_KEY")
    assert "YOUTUBE_API_KEY" in str(err.value) and "여러 줄" in str(err.value)
    assert "first-secret" not in str(err.value) and "second-secret" not in str(err.value)


def test_env_key_strips_trailing_newline(monkeypatch):
    monkeypatch.setenv("IG_BUSINESS_ACCOUNT_ID", "17841400000000000\n")
    assert env_key("IG_BUSINESS_ACCOUNT_ID") == "17841400000000000"
    monkeypatch.delenv("IG_BUSINESS_ACCOUNT_ID")
    assert env_key("IG_BUSINESS_ACCOUNT_ID") is None
