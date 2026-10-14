"""Produce a release qualification record (Task 7.6).

Usage: uv run python scripts/qualify_release.py [--out docs/release/qualification-v1.json]
Exit code 1 if any non-waived automated check fails. Human sign-off is recorded separately by
editing `signed_off_by` / `signed_off_at` after review; automation never signs off.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from filinglens.evaluation.qualification import run_qualification

ROOT = Path(__file__).resolve().parents[1]


def _commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", type=Path, default=ROOT / "docs" / "release" / "qualification-v1.json"
    )
    args = parser.parse_args()
    corpus = ROOT / "tests" / "fixtures" / "eval" / "corpus_v1"
    record = run_qualification(
        eval_manifest=corpus / "manifest.json",
        eval_baseline=corpus / "baseline.json",
        submissions_fixture=ROOT
        / "tests"
        / "fixtures"
        / "sec"
        / "submissions"
        / "CIK0001234567.json",
        code_commit=_commit(),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(record.to_json() + "\n", encoding="utf-8")
    print(record.to_json())
    return 0 if record.automated_passed else 1


if __name__ == "__main__":
    sys.exit(main())
