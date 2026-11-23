"""DB 연결·스키마 (SQLAlchemy Core). 담당: D

어떤 DB를 쓰는가
    - 환경변수 `DATABASE_URL` 이 있으면 PostgreSQL — 운영용. GitHub Actions 러너는 실행이 끝나면
      디스크가 사라지므로 파일 DB를 쓸 수 없다.
    - 없으면 `DB_PATH`(기본 data/monitor.db)의 SQLite 파일 — 로컬 개발·테스트용.
    - `REQUIRE_DATABASE_URL=1` 인데 DATABASE_URL 이 비어 있으면 오류를 낸다 (운영 워크플로가 조용히
      임시 SQLite 로 내려가는 일을 막는다).

이 파일을 직접 쓸 일
    A·B·C는 이 파일 대신 repo.py 의 함수를 쓴다. 여기 있는 것은 repo.py 와 CLI(init-db, doctor)가 쓴다.

스키마를 바꿀 때
    - 테이블·인덱스 **추가**는 아래에 정의만 하면 다음 연결 때 자동으로 만들어진다.
    - 기존 테이블의 컬럼 변경은 자동으로 되지 않는다(마이그레이션 도구 없음). 모델에 필드를 추가하는
      정도는 `data`(JSON 문자열) 안에서 해결되므로 테이블을 바꿀 필요가 없다.
    - 두 DB 모두 같은 스키마를 쓴다.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Connection, Engine, Index, Integer, MetaData, Table, Text, create_engine, text
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
report_sends = Table(  # 주간 리포트의 채널별 발송 기록 — 재실행 때 이미 나간 채널을 다시 보내지 않기 위해
    "report_sends", metadata,
    Column("week", Text, primary_key=True),
    Column("channel", Text, primary_key=True),  # "slack" | "email"
    Column("sent_at", Text, nullable=False),
)
urgent_alerts = Table(  # 긴급 알림을 보낸 이벤트 — 실패한 알림을 다음 실행에서 다시 시도하기 위해
    "urgent_alerts", metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=False),
    Column("sent_at", Text, nullable=False),
)

# 자주 쓰는 범위 조회용 인덱스. 데이터가 쌓여도 주간 리포트·정리·0건 검사가 전체를 훑지 않게 한다.
INDEXES = (
    Index("ix_raw_items_brand_type_collected", raw_items.c.brand, raw_items.c.source_type,
          raw_items.c.collected_at),
    Index("ix_snapshots_collected_at", snapshots.c.collected_at),
    Index("ix_change_events_detected_at", change_events.c.detected_at),
    Index("ix_llm_calls_called_at", llm_calls.c.called_at),
)

_engines: dict[str, Engine] = {}
_initialized: set[str] = set()


def database_url() -> str:
    """지금 설정에서 쓸 SQLAlchemy URL. 호출할 때마다 환경변수·DB_PATH를 다시 읽는다."""
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        if (os.getenv("REQUIRE_DATABASE_URL") or "").strip() not in ("", "0", "false"):
            # GitHub Actions 러너의 디스크는 실행이 끝나면 사라진다. 조용히 SQLite로 내려가면 매일 빈 DB에서
            # 시작해 전 상품이 "신상품"으로 잡히고 주간 리포트가 비므로, 운영 워크플로에서는 멈춘다.
            raise RuntimeError("DATABASE_URL 이 비어 있습니다 (REQUIRE_DATABASE_URL 설정됨). "
                               "GitHub Secrets 에 운영 DB 주소를 등록하세요")
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
            # prepare_threshold=None: 서버 쪽 prepared statement 를 쓰지 않는다. Supabase·Neon 의
            # 트랜잭션 풀러(pgbouncer)는 연결을 요청마다 바꿔 줘서 prepared statement 가 깨진다.
            eng = create_engine(url, pool_pre_ping=True, connect_args={"prepare_threshold": None})
        _engines[url] = eng
    if url not in _initialized:
        metadata.create_all(eng)  # 이미 있는 테이블은 건드리지 않는다 (기존 SQLite 파일 그대로 사용)
        for index in INDEXES:  # create_all 은 이미 있던 테이블에 인덱스를 추가해 주지 않는다
            index.create(eng, checkfirst=True)
        if not url.startswith("sqlite"):
            _enable_row_level_security(eng)
        _initialized.add(url)
    return eng


def _enable_row_level_security(eng: Engine) -> None:
    """PostgreSQL: 우리 테이블에 RLS(행 수준 보안)를 켠다. 이미 켜진 테이블은 건드리지 않는다.

    Supabase 는 public 스키마의 테이블을 REST API 로 내보낸다. RLS 를 켜고 정책을 하나도 두지 않으면
    공개 키(anon)로는 읽지도 쓰지도 못하고, DB 주소로 직접 접속하는 이 앱(테이블 소유자)만 접근한다.
    """
    names = [t.name for t in metadata.sorted_tables]
    with eng.begin() as conn:
        rows = conn.execute(
            text("SELECT relname FROM pg_class WHERE relnamespace = current_schema()::regnamespace "
                 "AND relkind = 'r' AND NOT relrowsecurity AND relname = ANY(:names)"), {"names": names})
        for (name,) in rows.fetchall():
            conn.execute(text(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY'))


class _Conn:
    """SQLAlchemy 연결을 감싼다. 문자열 SQL도 그대로 실행할 수 있다(파라미터는 :name + dict).

    예: conn.execute("SELECT data FROM reports WHERE week = :w", {"w": "2026-W47"}).fetchone()
    사용자 입력이나 수집한 값을 SQL 문자열에 직접 붙이지 말고 반드시 파라미터로 넘긴다.
    """

    def __init__(self, conn: Connection):
        self._conn = conn
        self.dialect = conn.dialect.name  # "sqlite" | "postgresql"

    def execute(self, stmt: Any, params: Any = None) -> CursorResult:
        if isinstance(stmt, str):
            stmt = text(stmt)
        return self._conn.execute(stmt, params) if params is not None else self._conn.execute(stmt)


@contextmanager
def connect() -> Iterator[_Conn]:
    """`with connect() as conn:` — 블록이 끝나면 커밋(예외면 롤백)하고 연결을 닫는다.

    블록 하나가 트랜잭션 하나다. 예를 들어 save_items 는 최신 상태 갱신과 스냅샷 추가를 한 블록에서
    하므로, 중간에 실패하면 둘 다 반영되지 않는다.
    """
    with engine().begin() as conn:
        yield _Conn(conn)


def upsert(table: Table):
    """"있으면 갱신, 없으면 추가"용 INSERT. 지금 쓰는 DB에 맞는 것을 골라 준다.

    표준 SQL에는 upsert 가 없어 DB마다 전용 구문이 필요한데, SQLAlchemy 의 SQLite·PostgreSQL insert 는
    둘 다 .on_conflict_do_update(...) / .on_conflict_do_nothing(...) 를 같은 모양으로 지원한다.
    """
    return postgresql.insert(table) if is_postgres() else sqlite.insert(table)


def backend() -> str:
    """지금 쓰는 DB 종류. 주소(비밀번호 포함 가능)는 돌려주지 않는다."""
    return "postgresql" if is_postgres() else "sqlite"


def ping() -> None:
    """DB에 실제로 연결해 본다. 안 되면 예외."""
    with connect() as conn:
        conn.execute("SELECT 1")


def init_db() -> None:
    """테이블·인덱스를 (없으면) 만든다. 이미 있는 것은 건드리지 않으므로 여러 번 불러도 안전하다."""
    _initialized.discard(database_url())
    engine()
