#!/usr/bin/env python3
"""Run the repeatable handoff acceptance suite and enforce coverage budgets."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_PREFIXES = (
    "backend/application/",
    "backend/contracts/",
    "backend/ports/",
    "backend/infrastructure/",
    "backend/presentation/",
)


def core_line_percent(files: dict) -> float:
    summaries = [
        value["summary"]
        for name, value in files.items()
        if name.replace("\\", "/").startswith(CORE_PREFIXES)
    ]
    covered = sum(summary["covered_lines"] for summary in summaries)
    total = sum(summary["num_statements"] for summary in summaries)
    return 100.0 * covered / total if total else 0.0


def main() -> int:
    if sys.version_info < (3, 12):
        print("delivery verification requires Python 3.12+", file=sys.stderr)
        return 2
    static_checks = (
        [sys.executable, "-m", "ruff", "check", "."],
        [
            sys.executable,
            "-m",
            "compileall",
            "-q",
            "backend",
            "cli.py",
            "scripts",
        ],
        [sys.executable, "cli.py", "check-config", "-c", "config.yaml"],
    )
    for command in static_checks:
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    with tempfile.TemporaryDirectory(prefix="logparse-verify-") as temp_dir:
        coverage_path = Path(temp_dir) / "coverage.json"
        command = [
            sys.executable,
            "-m",
            "pytest",
            "tests",
            "-q",
            "--cov=backend",
            "--cov=cli",
            "--cov-branch",
            "--cov-report=term",
            f"--cov-report=json:{coverage_path}",
            "--basetemp",
            str(Path(temp_dir) / "pytest"),
            "-p",
            "no:cacheprovider",
        ]
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))

    totals = coverage["totals"]
    line_percent = float(totals["percent_statements_covered"])
    branch_percent = float(totals["percent_branches_covered"])
    core_percent = core_line_percent(coverage["files"])
    print(
        f"coverage: line={line_percent:.2f}% branch={branch_percent:.2f}% "
        f"architecture_core={core_percent:.2f}%"
    )
    failures = []
    if line_percent < 80:
        failures.append("overall line coverage is below 80%")
    if branch_percent < 70:
        failures.append("overall branch coverage is below 70%")
    if core_percent < 90:
        failures.append("architecture core line coverage is below 90%")
    if failures:
        print("; ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
