"""DB 연결·스키마. 시작은 SQLite, 운영 전환 시 PostgreSQL(SQLAlchemy)로 교체. 담당: D"""
from __future__ import annotations

import sqlite3

from app.core.config import ROOT, env

DB_PATH = ROOT / env("DB_PATH", "data/monitor.db")  # 상대 경로는 실행 위치가 아닌 저장소 루트 기준

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_items (
    brand TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    data TEXT NOT NULL,              -- RawItem JSON (최신 상태)
    collected_at TEXT NOT NULL,
    PRIMARY KEY (brand, source, external_id)
);
CREATE TABLE IF NOT EXISTS snapshots (       -- 일자별 원본, 8주 보관
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand TEXT, source TEXT, external_id TEXT, data TEXT, collected_at TEXT
);
CREATE TABLE IF NOT EXISTS change_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand TEXT NOT NULL,
    data TEXT NOT NULL,              -- ChangeEvent JSON
    detected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    week TEXT PRIMARY KEY,
    tldr TEXT,
    data TEXT NOT NULL,              -- WeeklyReport JSON
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT, input_tokens INTEGER, output_tokens INTEGER, called_at TEXT
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
