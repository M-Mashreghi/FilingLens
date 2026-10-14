"""Task 7.4: GitHub Actions CI/CD workflow structure."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows" / "ci.yml"


def load() -> dict[str, Any]:
    data = yaml.safe_load(WF.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def steps(job: dict[str, Any]) -> str:
    return "\n".join(str(s.get("run", "")) + str(s.get("uses", "")) for s in job["steps"])


def test_workflow_parses_with_required_structure() -> None:
    wf = load()
    triggers = wf[True] if True in wf else wf["on"]  # YAML 1.1 parses `on` as True
    assert {"push", "pull_request", "schedule", "workflow_dispatch"} <= set(triggers)
    assert set(wf["jobs"]) == {"quality", "docker", "release", "live-smoke"}
    for name, job in wf["jobs"].items():
        assert job["runs-on"] == "ubuntu-latest", name
        assert job["steps"], name
        for s in job["steps"]:
            assert ("run" in s) != ("uses" in s), (name, s)


def test_quality_gates_present() -> None:
    q = steps(load()["jobs"]["quality"])
    for needle in (
        "uv lock --check",
        "uv sync --frozen",
        "ruff format --check",
        "ruff check",
        "mypy",
        "pytest",
        "scripts/run_eval.py",
        "pip-audit",
    ):
        assert needle in q, needle
    assert "not live_sec and not live_llm" in q  # mandatory CI has no live SEC/LLM dependency


def test_failing_tests_block_release() -> None:
    jobs = load()["jobs"]
    assert jobs["docker"]["needs"] == "quality"
    assert set(jobs["release"]["needs"]) == {"quality", "docker"}
    assert "refs/tags/v" in jobs["release"]["if"]
    assert "docker build" in steps(jobs["docker"]) and "/ready" in steps(jobs["docker"])


def test_live_smoke_is_optional_and_budgeted() -> None:
    live = load()["jobs"]["live-smoke"]
    assert live["continue-on-error"] is True
    assert "schedule" in live["if"] and "workflow_dispatch" in live["if"]
    assert live["env"]["SEC_MAX_RPS"] == "1" and live["env"]["FILINGLENS_LIVE_SEC"] == "1"
    assert "vars.SEC_USER_AGENT" in live["env"]["SEC_USER_AGENT"]
    assert "-m live_sec" in steps(live)


def test_least_privilege_permissions() -> None:
    wf = load()
    assert wf["permissions"] == {"contents": "read"}
    assert wf["jobs"]["release"]["permissions"]["packages"] == "write"
    text = WF.read_text(encoding="utf-8")
    assert "secrets." not in text.replace("secrets.GITHUB_TOKEN", "")  # only the built-in token
