"""[D] infra 테스트 공용 DB. 기본은 임시 SQLite, TEST_DATABASE_URL 이 있으면 그 PostgreSQL 로 같은 테스트를 돈다.

실수로 운영 DB 를 건드리지 않도록 환경의 DATABASE_URL 은 항상 무시한다.
TEST_DATABASE_URL 의 테이블은 테스트마다 모두 지우므로 일회용 DB 만 지정할 것.
"""
import os

import pytest

from app.infra.storage import db


@pytest.fixture
def database(tmp_path, monkeypatch):
    test_url = os.getenv("TEST_DATABASE_URL")
    if test_url:
        monkeypatch.setenv("DATABASE_URL", test_url)
        db.metadata.drop_all(db.engine())
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "monitor.db")
    db.init_db()
    yield
    if test_url:
        db.metadata.drop_all(db.engine())
