"""Task 7.2: observability, structured logging and health checks."""

from __future__ import annotations

import importlib.util
import io
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from filinglens.api.app import create_app
from filinglens.bootstrap import Container
from filinglens.config import Settings
from filinglens.domain.models import RunState
from filinglens.logging_setup import JsonFormatter
from filinglens.observability import ContextFilter, Telemetry, current_context, log_context

E2E = Path(__file__).resolve().parents[1] / "e2e" / "test_end_to_end.py"


def _load_e2e():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("e2e_mod", E2E)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["e2e_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def json_logs():  # type: ignore[no-untyped-def]
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(ContextFilter())
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield stream
    root.removeHandler(handler)
    root.setLevel(old_level)


def _records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]


def test_log_context_binds_ids(json_logs: io.StringIO) -> None:
    with log_context(run_id="r1", accession="0001234567-24-000031") as ctx:
        logging.getLogger("x").info("hello")
        assert current_context()["run_id"] == "r1"
        with log_context(stage="parse"):
            logging.getLogger("x").info("nested")
    logging.getLogger("x").info("outside")
    recs = _records(json_logs)
    assert recs[0]["run_id"] == "r1" and recs[0]["correlation_id"] == ctx["correlation_id"]
    assert recs[1]["stage"] == "parse" and recs[1]["run_id"] == "r1"
    assert "run_id" not in recs[2]


def test_telemetry_stage_timing_and_error_categories() -> None:
    t = Telemetry()
    with t.stage("parse"):
        pass
    with pytest.raises(ValueError), t.stage("parse", error_category="parser"):
        raise ValueError("boom")
    t.error("weird")
    snap = t.snapshot()
    assert snap["stages"]["parse"]["count"] == 2 and snap["stages"]["parse"]["errors"] == 1
    assert snap["errors_by_category"] == {"internal": 1, "parser": 1}


def test_failed_run_traceable_end_to_end(tmp_path: Path, json_logs: io.StringIO) -> None:
    e2e = _load_e2e()
    env = e2e.Env(tmp_path, fail=lambda u: u.endswith("exdv-20240928.htm"))
    res = env.pipeline.run(
        cik=1234567, focal_accession="0001234567-24-000031", as_of=datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert res.run.state is RunState.BLOCKED_BY_SOURCE
    recs = [r for r in _records(json_logs) if r.get("run_id") == res.run.run_id]
    msgs = [r["msg"] for r in recs]
    assert "run transition" in msgs and "run stopped" in msgs
    stopped = next(r for r in recs if r["msg"] == "run stopped")
    assert stopped["error_category"] == "source" and stopped["accession"] == "0001234567-24-000031"
    assert len({r["correlation_id"] for r in recs}) == 1
    assert env.pipeline.d.telemetry.snapshot()["errors_by_category"]["source"] == 1


def test_no_secrets_in_logs(json_logs: io.StringIO) -> None:
    logging.getLogger("x").info(
        "calling provider with api_key=sk-livesecretvalue123", extra={"llm_api_key": "sk-x"}
    )
    out = json_logs.getvalue()
    assert "sk-livesecretvalue123" not in out and '"llm_api_key": "***"' in out


def test_health_ready_and_detailed(tmp_path: Path) -> None:
    c = Container.in_memory()
    c.raw_dir = tmp_path / "raw"
    settings = Settings(env="test", llm_api_key="sk-supersecret-should-not-leak")
    client = TestClient(create_app(settings, container=c))
    assert client.get("/healthz").json()["status"] == "ok"
    ready = client.get("/ready")
    assert ready.status_code == 200 and ready.json()["checks"] == {
        "database": "ok",
        "raw_store": "ok",
    }
    c.runs.create(cik=1234567, as_of=datetime(2026, 1, 1, tzinfo=UTC))
    detailed = client.get("/health/detailed")
    body = detailed.json()
    assert body["runs_by_state"] == {"Created": 1} and body["schema_version"] >= 6
    assert "telemetry" in body and body["sec"] is None
    assert "supersecret" not in detailed.text


def test_ready_reports_failures(tmp_path: Path) -> None:
    c = Container.in_memory()
    blocker = tmp_path / "file"
    blocker.write_text("x")
    c.raw_dir = blocker / "raw"  # parent is a file -> not writable
    client = TestClient(create_app(Settings(env="test"), container=c))
    resp = client.get("/ready")
    assert resp.status_code == 503 and resp.json()["checks"]["raw_store"].startswith("error")
