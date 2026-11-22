import pytest

from app.infra.storage import db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """테스트마다 빈 SQLite 파일을 쓴다."""
    path = tmp_path / "monitor.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    return path
