"""DB 연결·스키마 (SQLAlchemy Core). 담당: D

- `DATABASE_URL`이 있으면 PostgreSQL(운영, GitHub Actions처럼 디스크가 사라지는 환경)
- 없으면 `DB_PATH`의 SQLite 파일(로컬·테스트)

두 DB 모두 같은 스키마를 쓴다. `data` 컬럼은 pydantic JSON을 TEXT 그대로 저장한다.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Connection, Engine, Integer, MetaData, Table, Text, create_engine, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import CursorResult
from sqlalchemy.pool import NullPool

from app.core.config import ROOT, env

DB_PATH = Path(env("DB_PATH", str(ROOT / "data" / "monitor.db")))

metadata = MetaData()

# ISO 문자열(시각·주차)은 문자열 비교로 범위를 거른다. PostgreSQL은 DB 로케일에 따라 문자열 정렬이
# 달라질 수 있어 바이트 순서("C")로 고정한다. SQLite는 기본이 바이트 순서(BINARY)다.
_Iso = Text().with_variant(Text(collation="C"), "postgresql")

raw_items = Table(
    "raw_items", metadata,
    Column("brand", Text, primary_key=True),
    Column("source", Text, primary_key=True),
    Column("external_id", Text, primary_key=True),
    Column("source_type", Text, nullable=False),
    Column("data", Text, nullable=False),  # RawItem JSON (최신 상태)
    Column("collected_at", _Iso, nullable=False),
)
snapshots = Table(  # 일자별 원본, 8주 보관
    "snapshots", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("brand", Text),
    Column("source", Text),
    Column("external_id", Text),
    Column("data", Text),
    Column("collected_at", _Iso),
)
change_events = Table(
    "change_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("brand", Text, nullable=False),
    Column("data", Text, nullable=False),  # ChangeEvent JSON
    Column("detected_at", _Iso, nullable=False),
)
reports = Table(
    "reports", metadata,
    Column("week", _Iso, primary_key=True),
    Column("tldr", Text),
    Column("data", Text, nullable=False),  # WeeklyReport JSON
    Column("sent_at", Text),
)
llm_calls = Table(
    "llm_calls", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("model", Text),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("called_at", _Iso),
)

_engines: dict[str, Engine] = {}
_initialized: set[str] = set()


def database_url() -> str:
    """지금 설정에서 쓸 SQLAlchemy URL. 호출할 때마다 환경변수·DB_PATH를 다시 읽는다."""
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        return f"sqlite:///{Path(DB_PATH).resolve()}"
    # Heroku·Supabase·Neon 등이 주는 postgres:// / postgresql:// 를 psycopg3 드라이버로 맞춘다
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def is_postgres() -> bool:
    return database_url().startswith("postgresql")


def engine() -> Engine:
    """URL별 엔진을 재사용한다. 프로세스에서 처음 여는 DB면 테이블도 만든다(init-db 누락 방지)."""
    url = database_url()
    eng = _engines.get(url)
    if eng is None:
        if url.startswith("sqlite"):
            Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
            eng = create_engine(url, poolclass=NullPool)  # 파일을 붙잡고 있지 않도록 매번 닫는다
        else:
            eng = create_engine(url, pool_pre_ping=True)
        _engines[url] = eng
    if url not in _initialized:
        metadata.create_all(eng)  # 이미 있는 테이블은 건드리지 않는다 (기존 SQLite 파일 그대로 사용)
        _initialized.add(url)
    return eng


class _Conn:
    """SQLAlchemy 연결을 감싼다. 문자열 SQL도 그대로 실행할 수 있다(파라미터는 :name + dict)."""

    def __init__(self, conn: Connection):
        self._conn = conn
        self.dialect = conn.dialect.name  # "sqlite" | "postgresql"

    def execute(self, stmt: Any, params: Any = None) -> CursorResult:
        if isinstance(stmt, str):
            stmt = text(stmt)
        return self._conn.execute(stmt, params) if params is not None else self._conn.execute(stmt)


@contextmanager
def connect() -> Iterator[_Conn]:
    """`with connect() as conn:` — 블록이 끝나면 커밋(예외면 롤백)하고 연결을 닫는다."""
    with engine().begin() as conn:
        yield _Conn(conn)


def upsert(table: Table):
    """지금 DB 방언의 INSERT (on_conflict_do_update 지원). SQLite·PostgreSQL 문법이 같다."""
    return postgresql.insert(table) if is_postgres() else sqlite.insert(table)


def init_db() -> None:
    _initialized.discard(database_url())
    engine()
