"""Tests for cli.py."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import importlib

from click.testing import CliRunner
import yaml

from cli import cli
from cli import _print_summary
from backend.models import (
    LogEntry,
    MechBoardCycle,
    MechBoundaryIssue,
    MechLogEntry,
    MechProcessLifecycle,
    MechResult,
    MechSlotOutput,
    ParseResult,
    SlotInfo,
)


def test_test_pattern_reads_nested_mechanism_config(sample_config, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "products": {
                    "default": {
                        "log_parser": {
                            "config": sample_config,
                        },
                    },
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    line = (
        "2026-01-03T00:01:00 EXAMPLE Service=SERVICE; Slot=1; "
        "CPU-Id=0; ProcessName=SERVICE-12345; Context=No[1] hello)"
    )

    result = CliRunner().invoke(
        cli,
        [
            "test-pattern",
            "-c",
            str(config_path),
            "-m",
            "module1",
            "-t",
            "diag",
            line,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Slot: 1" in result.output
    assert "CPU_Id: 0" in result.output


def test_parse_prints_result_errors_without_verbose(tmp_path, monkeypatch):
    package_path = tmp_path / "package.zip"
    package_path.write_text("placeholder", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "products": {
                    "default": {
                        "discovery": {"plugin": "unused", "config": {}},
                        "log_parser": {"plugin": "unused", "config": {}},
                    },
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    cli_module = importlib.import_module("cli")

    class FakePipeline:
        def __init__(self, _config):
            pass

        def run(self, source, output_dir, product="default", verbose=False):
            return ParseResult(
                task_id="task",
                package_name=source.name,
                errors=[
                    "unsafe cycle split adjusted_backward: module=module1 slot=1 split=2026-01-03T06:00:00 "
                    "same_pid_conflicts=other-500@board "
                    "protected_boundaries=dhcp@board role=indicator old_pids=100 new_pid=200",
                    "cycle split diagnostic: same_pid_adjusted_backward "
                    "m=module1 s=1 scope=board sp=2026-01-03T06:00:00 "
                    "ad=2026-01-03T05:00:00 reason=adjusted_backward",
                    "unsafe cycle split kept: module=module1 slot=1 split=2026-01-03T06:00:00 "
                    "same_pid_conflicts=other-500@board reason=no_safe_gap_candidate",
                    "普通解析错误: bad file",
                ],
                mech_results=[
                    MechResult(
                        module_name="EXAMPLE",
                        module_key="module1",
                        slots=[
                            MechSlotOutput(
                                slot_id="1",
                                lifecycle_reliable=False,
                                boundary_issues=[
                                    MechBoundaryIssue(
                                        kind="unsafe_cycle_split",
                                        severity="error",
                                    ),
                                    MechBoundaryIssue(
                                        kind="same_pid_adjusted_backward",
                                        severity="warning",
                                    ),
                                    MechBoundaryIssue(
                                        kind="scoped_cpu_split",
                                        severity="info",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            )

    monkeypatch.setattr(cli_module, "Pipeline", FakePipeline)

    result = CliRunner().invoke(
        cli,
        [
            "-c",
            str(config_path),
            "parse",
            str(package_path),
            "-o",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "生命周期切分诊断: ERROR=1 WARNING=1 INFO=1" in result.output
    assert "定位: python cli.py mech-lifecycles task -s <slot_id> -m <module_name> --show-boundaries" in result.output
    assert "普通解析错误: bad file" in result.output
    assert "unsafe cycle split adjusted_backward" not in result.output
    assert "cycle split diagnostic: same_pid_adjusted_backward" not in result.output
    assert "unsafe cycle split kept" not in result.output
    assert "protected_boundaries=dhcp@board role=indicator" not in result.output


def test_print_summary_writes_compact_result_by_default(tmp_path):
    ts = datetime(2026, 1, 3, tzinfo=timezone.utc)
    diag_entry = LogEntry(
        path="diag.zip",
        name="diag.zip",
        size_bytes=100,
        compressed=True,
        content_timestamps=[ts],
    )
    slot = SlotInfo(
        slot_id="1",
        name="slot_1",
        path="/tmp/slot_1",
        diagnostic_logs=[diag_entry],
    )
    mech_log = MechLogEntry(
        timestamp=ts,
        source="diagnostic",
        source_file="slot_1/diag.zip",
        slot="1",
        process_name="svc",
        pid="123",
        sequence=1,
        raw="raw line that should only live in mech_modules output",
    )
    process = MechProcessLifecycle(
        process_name="svc",
        pid="123",
        logs=[mech_log],
        total_count=1,
        missing_sequences=[2],
    )
    cycle = MechBoardCycle(
        dir_name="cycle",
        start_time=ts,
        end_time=ts,
        processes=[process],
    )
    mech_result = MechResult(
        module_name="EXAMPLE",
        module_key="module1",
        slots=[MechSlotOutput(slot_id="1", board_cycles=[cycle])],
        diag_entry_count=1,
    )
    result = ParseResult(
        task_id="task",
        package_name="package.zip",
        diagnostic_slots=[slot],
        mech_results=[mech_result],
    )

    _print_summary(result, tmp_path)

    data = json.loads((tmp_path / "task" / "result.json").read_text(encoding="utf-8"))
    diag_log = data["diagnostic_slots"][0]["diagnostic_logs"][0]
    assert diag_log["content_timestamp_count"] == 1
    assert "content_timestamps" not in diag_log
    proc = data["mech_results"][0]["slots"][0]["board_cycles"][0]["processes"][0]
    assert proc["total_count"] == 1
    assert proc["missing_sequences"] == [2]
    assert "logs" not in proc
    assert "raw line that should only live" not in json.dumps(data, ensure_ascii=False)


def test_test_pattern_journal_without_sequence(sample_config, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "products": {
                    "default": {
                        "log_parser": {
                            "config": sample_config,
                        },
                    },
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    line = "2026-01-03T00:01:00 host SERVICE-12345: EXAMPLE without sequence"

    result = CliRunner().invoke(
        cli,
        [
            "test-pattern",
            "-c",
            str(config_path),
            "-m",
            "module1",
            "-t",
            "journal",
            line,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "journal.line_pattern2.auto_no_sequence" in result.output
    assert "SERVICE" in result.output
    assert "12345" in result.output
    assert "序号: 无" in result.output
    assert "EXAMPLE without sequence" in result.output


def test_test_pattern_journal_with_malformed_sequence_does_not_fallback(sample_config, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "products": {
                    "default": {
                        "log_parser": {
                            "config": sample_config,
                        },
                    },
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    line = "2026-01-03T00:01:00 host SERVICE-12345: No[bad] EXAMPLE corrupt"

    result = CliRunner().invoke(
        cli,
        [
            "test-pattern",
            "-c",
            str(config_path),
            "-m",
            "module1",
            "-t",
            "journal",
            line,
        ],
    )

    assert result.exit_code == 1
    assert "不匹配 journal.line_pattern 及 line_pattern2" in result.output


def test_test_pattern_journal_with_sequence(sample_config, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "products": {
                    "default": {
                        "log_parser": {
                            "config": sample_config,
                        },
                    },
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    line = "2026-01-03T00:01:00 host SERVICE-12345: No[7] EXAMPLE with sequence"

    result = CliRunner().invoke(
        cli,
        [
            "test-pattern",
            "-c",
            str(config_path),
            "-m",
            "module1",
            "-t",
            "journal",
            line,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "SERVICE" in result.output
    assert "12345" in result.output
    assert "序号: 7" in result.output
    assert "EXAMPLE with sequence" in result.output


def test_mech_lifecycles_show_boundaries(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "result.json").write_text(
        json.dumps(
            {
                "mech_results": [
                    {
                        "module_name": "EXAMPLE",
                        "slots": [
                            {
                                "slot_id": "1",
                                "lifecycle_reliable": False,
                                "boundary_issues": [
                                    {
                                        "kind": "unsafe_cycle_split",
                                        "severity": "error",
                                        "action": "kept",
                                        "reason": "no_safe_gap_candidate",
                                        "scope": "board",
                                        "split_time": "2026-01-03T00:00:10+08:00",
                                        "adjusted_time": "2026-01-03T00:00:12+08:00",
                                        "conflicts": [
                                            {
                                                "process_name": "other",
                                                "pid": "500",
                                                "cpu_id": "",
                                                "before_time": "2026-01-03T00:00:05+08:00",
                                                "after_time": "2026-01-03T00:00:12+08:00",
                                                "before_log": {
                                                    "source": "diagnostic",
                                                    "source_file": "slot_1/diag.log",
                                                    "sequence": 0,
                                                    "raw_excerpt": "before raw",
                                                },
                                            },
                                        ],
                                        "suggested_commands": [
                                            "python cli.py mech-logs <task_id> -s 1 -c <board_cycle> -p other-500 -m EXAMPLE",
                                        ],
                                    },
                                ],
                                "board_cycles": [
                                    {
                                        "dir_name": "c1",
                                        "processes": [
                                            {
                                                "process_name": "other",
                                                "pid": "500",
                                                "total_count": 1,
                                                "missing_sequences": [],
                                            },
                                        ],
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "mech-lifecycles",
            "task",
            "-s",
            "1",
            "-m",
            "EXAMPLE",
            "-o",
            str(tmp_path),
            "--show-boundaries",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "生命周期可靠性: false" in result.output
    assert "生命周期切分诊断: ERROR=1 WARNING=0 INFO=0" in result.output
    assert "[ERROR] unsafe_cycle_split action=kept reason=no_safe_gap_candidate" in result.output
    assert "conflict other-500@board before=2026-01-03T00:00:05+08:00 after=2026-01-03T00:00:12+08:00" in result.output
    assert "before diagnostic|slot_1/diag.log seq=0 raw=before raw" in result.output
    assert "evidence diagnostic|slot_1/diag.log" not in result.output
    assert "hint python cli.py mech-logs task -s 1 -c <board_cycle> -p other-500 -m EXAMPLE" in result.output


def test_mech_lifecycles_compact_restart_overlap_shows_only_endpoint_processes(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "result.json").write_text(
        json.dumps(
            {
                "mech_results": [
                    {
                        "module_name": "EXAMPLE",
                        "slots": [
                            {
                                "slot_id": "1",
                                "lifecycle_reliable": False,
                                "boundary_issues": [
                                    {
                                        "kind": "restart_boundary_overlap",
                                        "severity": "error",
                                        "reason": "new_pid_start_le_old_pid_end",
                                        "scope": "board",
                                        "split_time": "2026-01-03T00:00:10.000001+08:00",
                                        "old_pid_end": "2026-01-03T00:00:10+08:00",
                                        "new_pid_start": "2026-01-03T00:00:09+08:00",
                                        "protected_boundaries": [
                                            {
                                                "slot": "1",
                                                "process_name": "dhcp",
                                                "cpu_id": "",
                                                "role": "indicator",
                                                "old_pids": ["100"],
                                                "old_end": "2026-01-03T00:00:00+08:00",
                                                "new_pid": "200",
                                                "new_start": "2026-01-03T00:00:09+08:00",
                                                "old_log": {"raw_excerpt": "dhcp old"},
                                                "new_log": {
                                                    "source": "diagnostic",
                                                    "source_file": "slot_1/dhcp.log",
                                                    "sequence": 0,
                                                    "raw_excerpt": "dhcp new",
                                                },
                                            },
                                            {
                                                "slot": "1",
                                                "process_name": "svc_a",
                                                "cpu_id": "",
                                                "role": "whitelist",
                                                "old_pids": ["300"],
                                                "old_end": "2026-01-03T00:00:10+08:00",
                                                "new_pid": "400",
                                                "new_start": "2026-01-03T00:00:11+08:00",
                                                "old_log": {
                                                    "source": "diagnostic",
                                                    "source_file": "slot_1/svc_a.log",
                                                    "sequence": 0,
                                                    "raw_excerpt": "svc old",
                                                },
                                                "new_log": {"raw_excerpt": "svc new"},
                                            },
                                            {
                                                "slot": "1",
                                                "process_name": "noise",
                                                "cpu_id": "",
                                                "role": "whitelist",
                                                "old_pids": ["900"],
                                                "old_end": "2026-01-03T00:00:03+08:00",
                                                "new_pid": "901",
                                                "new_start": "2026-01-03T00:00:20+08:00",
                                                "old_log": {"raw_excerpt": "noise old"},
                                                "new_log": {"raw_excerpt": "noise new"},
                                            },
                                        ],
                                        "suggested_commands": [
                                            "python cli.py mech-lifecycles <task_id> -s 1 -m EXAMPLE --show-boundaries",
                                            "python cli.py mech-logs <task_id> -s 1 -c <board_cycle> -p dhcp-200 -m EXAMPLE",
                                            "python cli.py mech-logs <task_id> -s 1 -c <board_cycle> -p svc_a-400 -m EXAMPLE",
                                        ],
                                    },
                                ],
                                "board_cycles": [],
                            },
                        ],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "mech-lifecycles",
            "task",
            "-s",
            "1",
            "-m",
            "EXAMPLE",
            "-o",
            str(tmp_path),
            "--show-boundaries",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "overlap new_start=2026-01-03T00:00:09+08:00 <= old_end=2026-01-03T00:00:10+08:00" in result.output
    assert "old-side svc_a-300@board role=whitelist old_end=2026-01-03T00:00:10+08:00 raw=svc old" in result.output
    assert "new-side dhcp-200@board role=indicator new_start=2026-01-03T00:00:09+08:00 raw=dhcp new" in result.output
    assert "noise" not in result.output
    assert result.output.count("hint ") == 1


def test_mech_lifecycles_boundary_detail_full_expands_all_evidence(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "result.json").write_text(
        json.dumps(
            {
                "mech_results": [
                    {
                        "module_name": "EXAMPLE",
                        "slots": [
                            {
                                "slot_id": "1",
                                "lifecycle_reliable": False,
                                "boundary_issues": [
                                    {
                                        "kind": "restart_boundary_overlap",
                                        "severity": "error",
                                        "reason": "new_pid_start_le_old_pid_end",
                                        "scope": "board",
                                        "split_time": "2026-01-03T00:00:10.000001+08:00",
                                        "old_pid_end": "2026-01-03T00:00:10+08:00",
                                        "new_pid_start": "2026-01-03T00:00:09+08:00",
                                        "protected_boundaries": [
                                            {
                                                "slot": "1",
                                                "process_name": "noise",
                                                "cpu_id": "",
                                                "role": "whitelist",
                                                "old_pids": ["900"],
                                                "old_end": "2026-01-03T00:00:03+08:00",
                                                "new_pid": "901",
                                                "new_start": "2026-01-03T00:00:20+08:00",
                                            },
                                        ],
                                        "evidence": [
                                            {
                                                "source": "diagnostic",
                                                "source_file": "slot_1/noise.log",
                                                "sequence": 0,
                                                "raw_excerpt": "noise raw",
                                            },
                                        ],
                                        "suggested_commands": [
                                            "python cli.py mech-lifecycles <task_id> -s 1 -m EXAMPLE --show-boundaries",
                                            "python cli.py mech-logs <task_id> -s 1 -c <board_cycle> -p noise-901 -m EXAMPLE",
                                        ],
                                    },
                                ],
                                "board_cycles": [],
                            },
                        ],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "mech-lifecycles",
            "task",
            "-s",
            "1",
            "-m",
            "EXAMPLE",
            "-o",
            str(tmp_path),
            "--show-boundaries",
            "--boundary-detail",
            "full",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "protected noise@board role=whitelist old_pids=900 new_pid=901" in result.output
    assert "evidence diagnostic|slot_1/noise.log seq=0 raw=noise raw" in result.output
    assert result.output.count("hint ") == 2
