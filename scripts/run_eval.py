"""Run the golden evaluation corpus.

Usage: uv run python scripts/run_eval.py [--write-baseline]
Exit code 1 when a threshold is violated or a metric regresses against the stored baseline.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from filinglens.evaluation.harness import run_eval

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "eval" / "corpus_v1" / "manifest.json"
BASELINE = ROOT / "tests" / "fixtures" / "eval" / "corpus_v1" / "baseline.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()
    report = run_eval(MANIFEST, None if args.write_baseline else BASELINE)
    print(report.to_json())
    if args.write_baseline:
        if not report.passed:
            print("refusing to write a baseline that violates thresholds", file=sys.stderr)
            return 1
        BASELINE.write_text(report.to_json() + "\n", encoding="utf-8")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
