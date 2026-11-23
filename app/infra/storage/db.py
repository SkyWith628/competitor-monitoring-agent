"""DB 연결·스키마. 담당: D

DATABASE_URL 이 있으면 PostgreSQL(운영: Supabase), 없으면 로컬 SQLite 파일(DB_PATH)을 쓴다.
"""
from __future__ import annotations

from sqlalchemy import Column, Engine, Integer, MetaData, Table, Text, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

from app.core.config import ROOT, env

DB_PATH = ROOT / env("DB_PATH", "data/monitor.db")  # 상대 경로는 실행 위치가 아닌 저장소 루트 기준

metadata = MetaData()

raw_items = Table(  # 항목별 최신 상태
    "raw_items", metadata,
    Column("brand", Text, primary_key=True),
    Column("source", Text, primary_key=True),
    Column("external_id", Text, primary_key=True),
    Column("source_type", Text, nullable=False),
    Column("data", Text, nullable=False),  # RawItem JSON
    Column("collected_at", Text, nullable=False),
)
snapshots = Table(  # 일자별 원본, 8주 보관
    "snapshots", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("brand", Text, index=True),
    Column("source", Text),
    Column("external_id", Text),
    Column("data", Text),
    Column("collected_at", Text, index=True),
)
change_events = Table(
    "change_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("brand", Text, nullable=False),
    Column("data", Text, nullable=False),  # ChangeEvent JSON
    Column("detected_at", Text, nullable=False, index=True),
)
reports = Table(
    "reports", metadata,
    Column("week", Text, primary_key=True),
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
    Column("called_at", Text),
)

_engines: dict[str, Engine] = {}


def database_url() -> URL:
    raw = env("DATABASE_URL")
    if not raw:
        return URL.create("sqlite", database=str(DB_PATH))
    url = make_url(raw)
    if url.drivername in ("postgres", "postgresql"):  # Supabase 가 주는 주소 그대로 붙여 넣어도 되게
        url = url.set(drivername="postgresql+psycopg")
    return url


def engine() -> Engine:
    url = database_url()
    key = url.render_as_string(hide_password=False)
    if key not in _engines:
        if url.get_backend_name() == "sqlite":
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _engines[key] = create_engine(url)
        else:
            # 명령 한 번 돌고 끝나므로 풀을 두지 않는다. prepare_threshold=None: Supabase 의
            # 트랜잭션 풀러(6543 포트)는 prepared statement 를 지원하지 않는다.
            _engines[key] = create_engine(url, poolclass=NullPool, connect_args={"prepare_threshold": None})
    return _engines[key]


def connect():
    """`with connect() as conn:` — 블록이 끝나면 커밋(예외 시 롤백)하고 연결을 닫는다."""
    return engine().begin()


def describe() -> str:
    """접속 대상을 비밀번호 없이 보여 준다 (로그용)."""
    url = database_url()
    return f"sqlite: {url.database}" if url.get_backend_name() == "sqlite" else f"postgresql: {url.host}"


def init_db() -> None:
    eng = engine()
    metadata.create_all(eng)
    if eng.dialect.name == "postgresql":
        # Supabase 는 public 스키마 테이블을 REST API 로 노출한다. RLS 를 켜고 정책을 두지 않으면
        # API(anon 키)로는 읽고 쓸 수 없고, 이 앱(DB 직접 접속)만 접근한다.
        with connect() as conn:
            for table in metadata.sorted_tables:
                conn.execute(text(f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY'))
