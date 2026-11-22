import os

import pytest

from app.infra.storage import db
from app.infra.timeutil import use_korean_time


@pytest.fixture(autouse=True)
def _korean_time(monkeypatch):
    """CLI가 실행 때 맞추는 것과 같은 시간대에서 테스트한다. 운영 워크플로 전용 설정은 끈다."""
    use_korean_time()
    monkeypatch.delenv("REQUIRE_DATABASE_URL", raising=False)


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
        tables = ", ".join(t.name for t in db.metadata.sorted_tables)  # 새 테이블이 생겨도 빠지지 않게
        conn.execute(f"TRUNCATE {tables} RESTART IDENTITY")
    return url
