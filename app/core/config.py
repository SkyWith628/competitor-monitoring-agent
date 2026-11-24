"""설정 로드: .env(비밀값) + config/brands.yaml(브랜드별 대상)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@lru_cache
def brands_config() -> dict:
    with open(ROOT / "config" / "brands.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["brands"]


def env(key: str, default: str | None = None) -> str | None:
    """환경 변수 값. 앞뒤 공백·줄바꿈은 잘라내고, 비어 있으면 default 를 돌려준다.

    GitHub Secrets 나 .env 에 값을 붙여 넣을 때 끝에 줄바꿈이 딸려 들어오는 일이 잦다. 그대로 쓰면
    요청 주소·헤더가 깨져 수집 전체가 알아보기 어려운 오류로 실패한다.
    """
    return (os.getenv(key) or "").strip() or default


def env_key(key: str) -> str | None:
    """API 키·토큰·ID 처럼 '한 줄짜리 값'을 읽는다. 없으면 None.

    값 중간에 줄바꿈이 있으면(두 값을 한 칸에 붙여 넣은 경우) 고쳐 쓸 수 없으므로, 어느 값인지
    이름만 밝혀 알린다 (값 자체는 오류 문구에 넣지 않는다).
    """
    value = env(key)
    if value and ("\n" in value or "\r" in value):
        raise RuntimeError(f"{key} 값이 여러 줄입니다 — 값 하나만 한 줄로 다시 넣으세요 (.env 또는 GitHub Secrets)")
    return value
