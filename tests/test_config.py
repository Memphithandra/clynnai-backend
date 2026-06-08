from pathlib import Path

from clynnai_backend.config import Settings
from clynnai_backend.main import _sqlite_path_from_database_url


def test_sqlite_absolute_url_maps_to_path():
    assert _sqlite_path_from_database_url("sqlite:////data/clynnai/clynnai.db") == Path("/data/clynnai/clynnai.db")


def test_non_sqlite_database_url_returns_none():
    assert _sqlite_path_from_database_url("postgresql://user:***@db/clynnai") is None


def test_clynn_prefixed_env_names_are_supported(monkeypatch):
    monkeypatch.setenv("CLYNN_AI_HOST", "127.0.0.1")
    monkeypatch.setenv("CLYNN_AI_PORT", "18088")
    settings = Settings(_env_file=None)
    assert settings.host == "127.0.0.1"
    assert settings.port == 18088
