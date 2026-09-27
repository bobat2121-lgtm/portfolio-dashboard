import pytest

from portfolio import db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    db.reset_engine()
    yield db
    db.reset_engine()
