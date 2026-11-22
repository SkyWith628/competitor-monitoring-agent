import os

import pytest

from app.infra.storage import db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """테스트마다 빈 DB를 쓴다. 기본은 임시 SQLite 파일.

    `TEST_DATABASE_URL`을 주면 그 PostgreSQL에서 돈다(방언 확인용, 테이블을 비우므로 테스트 전용 DB만!).
    """
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        monkeypatch.delenv("DATABASE_URL", raising=False)  # 로컬 .env의 운영 DB를 건드리지 않도록
        path = tmp_path / "monitor.db"
        monkeypatch.setattr(db, "DB_PATH", path)
        return path
    monkeypatch.setenv("DATABASE_URL", url)
    with db.connect() as conn:
        conn.execute("TRUNCATE raw_items, snapshots, change_events, reports, llm_calls RESTART IDENTITY")
    return url
