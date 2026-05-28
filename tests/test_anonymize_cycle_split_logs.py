"""Tests for scripts/anonymize_cycle_split_logs.py."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "anonymize_cycle_split_logs.py"


def load_module():
    spec = importlib.util.spec_from_file_location("anonymize_cycle_split_logs", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_timestamps_are_numbered_by_chronological_order():
    module = load_module()
    text = (
        "unsafe cycle split adjusted_backward: "
        "split=2026-01-03T06:00:00 adjusted=2026-01-03T05:59:59"
    )

    assert module.sanitize_text(text) == "ucs ab: sp=t2 ad=t1"


def test_process_names_are_stable_and_preserve_pid_suffixes():
    module = load_module()
    text = (
        "unsafe cycle split kept: "
        "same_pid_conflicts=other-500@board before=2026-01-03T05:59:58 "
        "after=2026-01-03T06:00:01 last=2026-01-03T06:00:02 "
        "same_pid_conflicts=other-501@board "
        "protected_boundaries=dhcp@board role=indicator"
    )

    sanitized = module.sanitize_text(text)

    assert "sc=p1-500@b" in sanitized
    assert "sc=p1-501@b" in sanitized
    assert "pb=p2@b" in sanitized
    assert "b=t1" in sanitized
    assert "a=t2" in sanitized
    assert "l=t3" in sanitized
    assert "r=i" in sanitized


def test_process_field_names_are_anonymized():
    module = load_module()
    text = (
        "ProcessName=SERVICE-12345 process=worker proc=helper "
        "split=2026-01-03 06:00:00.123456"
    )

    assert module.sanitize_text(text) == "pn=p1-12345 pr=p2 pc=p3 sp=t1"


def test_diagnostic_words_and_module_values_are_shortened():
    module = load_module()
    text = (
        "unsafe cycle split adjusted_backward: module=module1 slot=1 "
        "old_pids=100,101 old_end=2026-01-03T05:59:58 "
        "new_pid=200 new_start=2026-01-03T06:00:00 "
        "protected_gap=(2026-01-03T05:59:58, 2026-01-03T06:00:00] "
        "reason=no_safe_gap_candidate"
    )

    assert module.sanitize_text(text) == (
        "ucs ab: m=m1 s=1 op=100,101 oe=t1 np=200 ns=t2 "
        "pg=(t1, t2] rs=nsg"
    )


def test_cli_reads_stdin_and_writes_stdout():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "-"],
        input="split=2026-01-03T06:00:00 adjusted=2026-01-03T05:59:59\n",
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout == "sp=t2 ad=t1\n"
    assert result.stderr == ""


def test_cli_writes_output_file(tmp_path):
    source = tmp_path / "unsafe.log"
    target = tmp_path / "sanitized.log"
    source.write_text(
        "same_pid_conflicts=other-500@board split=2026-01-03T06:00:00\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), "-o", str(target)],
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout == ""
    assert result.stderr == ""
    assert target.read_text(encoding="utf-8") == (
        "sc=p1-500@b sp=t1\n"
    )
