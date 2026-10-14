"""Task 7.3: Docker & production configuration.

Static checks validate the image definition (Docker may be unavailable on developer machines;
CI builds the image in Task 7.4). The runtime test starts the exact production command
(uvicorn factory) as a subprocess with production settings, probes health/readiness and
verifies graceful shutdown.
"""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()


def test_image_runs_as_non_root_with_locked_deps() -> None:
    assert re.search(r"^USER filinglens$", DOCKERFILE, re.M)
    assert "uv sync --frozen --no-dev" in DOCKERFILE
    assert "FROM python:3.12-slim" in DOCKERFILE
    assert "HEALTHCHECK" in DOCKERFILE and "/healthz" in DOCKERFILE
    assert "STOPSIGNAL SIGTERM" in DOCKERFILE and "--timeout-graceful-shutdown" in DOCKERFILE


def test_durable_and_rebuildable_paths_separated() -> None:
    assert 'VOLUME ["/app/data", "/app/cache"]' in DOCKERFILE
    assert "FILINGLENS_DB_PATH=/app/data/filinglens.db" in DOCKERFILE
    assert "FILINGLENS_RAW_DIR=/app/data/raw" in DOCKERFILE
    assert "FILINGLENS_CACHE_DIR=/app/cache" in DOCKERFILE
    assert "filinglens-data:/app/data" in COMPOSE and "filinglens-cache:/app/cache" in COMPOSE


def test_no_secret_baked_into_image() -> None:
    for name in ("LLM_API_KEY", "SEC_USER_AGENT", "API_KEY", "TOKEN", "PASSWORD", "SECRET"):
        assert not re.search(rf"^\s*(ENV|ARG)\b.*{name}", DOCKERFILE, re.M | re.I), name
    assert ".env" in DOCKERIGNORE and "!.env.example" in DOCKERIGNORE
    assert "COPY . " not in DOCKERFILE  # explicit, minimal copies only
    assert "env_file: .env" in COMPOSE


def test_api_and_ui_from_same_image() -> None:
    assert COMPOSE.count("image: filinglens:local") == 2
    assert "streamlit" in COMPOSE and "uvicorn" in DOCKERFILE


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.mark.timeout_sensitive
def test_production_process_starts_probes_and_shuts_down_gracefully(tmp_path: Path) -> None:
    port = _free_port()
    env = {
        **os.environ,
        "FILINGLENS_ENV": "production",
        "FILINGLENS_LOG_JSON": "true",
        "FILINGLENS_DB_PATH": str(tmp_path / "data" / "filinglens.db"),
        "FILINGLENS_RAW_DIR": str(tmp_path / "data" / "raw"),
        "FILINGLENS_CACHE_DIR": str(tmp_path / "cache"),
        "SEC_LIVE_MODE": "false",
    }
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "filinglens.api.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--timeout-graceful-shutdown",
            "5",
        ],
        env=env,
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=flags,
    )
    try:
        deadline = time.time() + 40
        while True:
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1)
                if r.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.time() > deadline or proc.poll() is not None:
                out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
                pytest.fail(f"server did not start: {out[-2000:]}")
            time.sleep(0.3)
        assert r.json()["env"] == "production"
        ready = httpx.get(f"http://127.0.0.1:{port}/ready", timeout=3)
        assert ready.status_code == 200 and ready.json()["checks"]["raw_store"] == "ok"
        assert (tmp_path / "data" / "filinglens.db").exists()
    finally:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGTERM)
        try:
            code = proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("server did not shut down gracefully")
    out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
    assert (
        "Shutting down" in out
        or "Finished server process" in out
        or code in (0, 1, 3221225786, -15)
    )
