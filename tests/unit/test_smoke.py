"""Import, configuration, logging and health smoke tests (Task 1.1)."""

from __future__ import annotations

import importlib
import json
import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import filinglens
from filinglens.api.app import create_app
from filinglens.bootstrap import Container
from filinglens.config import Settings
from filinglens.logging_setup import JsonFormatter, scrub


def test_package_imports() -> None:
    assert filinglens.__version__
    for mod in (
        "filinglens.config",
        "filinglens.logging_setup",
        "filinglens.api.app",
        "filinglens.ui.app",
    ):
        importlib.import_module(mod)


def test_health_endpoint() -> None:
    client = TestClient(create_app(Settings(env="test"), container=Container.in_memory()))
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["version"] == filinglens.__version__


def test_settings_defaults_are_conservative(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("SEC_MAX_RPS", "SEC_MAX_CONCURRENCY", "SEC_LIVE_MODE"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.sec_max_rps < 10
    assert s.sec_live_mode is False


def test_settings_read_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_MAX_RPS", "3")
    monkeypatch.setenv("SEC_USER_AGENT", "FilingLens test test@example.com")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.sec_max_rps == 3
    assert s.sec_user_agent.endswith("example.com")


def test_settings_reject_rate_above_sec_ceiling() -> None:
    with pytest.raises(ValidationError):
        Settings(sec_max_rps=11)


def test_secret_not_exposed_in_repr() -> None:
    s = Settings(llm_api_key="sk-supersecretvalue123")
    assert "supersecret" not in repr(s)


def test_scrub_redacts_credentials() -> None:
    out = scrub("api_key=abc123 Authorization: Bearer tok.en-1 sk-abcdefghijkl")
    assert "abc123" not in out
    assert "tok.en-1" not in out
    assert "sk-abcdefghijkl" not in out


def test_json_formatter_scrubs_extra_fields() -> None:
    record = logging.makeLogRecord(
        {"msg": "calling with token=xyz", "levelname": "INFO", "api_key": "real", "run_id": "r1"}
    )
    payload = json.loads(JsonFormatter().format(record))
    assert payload["api_key"] == "***"
    assert "xyz" not in payload["msg"]
    assert payload["run_id"] == "r1"
