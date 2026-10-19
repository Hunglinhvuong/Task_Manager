import pytest

from core import database


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Database tạm cho từng test (không đụng tới data/app.db)."""
    path = tmp_path / "test.db"
    monkeypatch.setenv("TASKS_DB_PATH", str(path))
    database.init_db()
    return path
