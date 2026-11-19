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
    return os.getenv(key, default)
