"""Tests for cli.py."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import importlib
import re

from click.testing import CliRunner
import yaml

from cli import cli
from cli import _print_summary
from backend.parsing.timestamp_extractor import TimestampExtractor
from backend.plugins.mechanisms.module1 import Module1Plugin
from backend.query import ResultQueryService
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


def _module1_v2_test_config() -> dict:
    return {
        "module_name": "EXAMPLE",
        "diag_pattern": (
            r"Service=(?P<Service>[^;]+).*?Slot=(?P<Slot>[^;,)]+).*?"
            r"CPU-Id=(?P<CPU_Id>[^;,)]+).*?"
            r"ProcessName=(?P<ProcessName>[^;,)]+).*?"
            r"Context=(?P<Context>.+?)\)$"
        ),
        "active_master_keyword": "",
        "board_restart_indicator": "",
        "board_restart_whitelist": [],
        "process_name_mapping": {},
        "journal": {
            "line_pattern": "",
            "line_pattern2": "",
            "identifying_keyword": "EXAMPLE",
        },
        "sequence_pattern": r"No\[(\d+)\]",
        "lifecycle_split": {
            "enabled": True,
            "reliable_processes": {"board": ["dhcp"], "cpu": []},
        },
    }


def _timestamp_extractor() -> TimestampExtractor:
    return TimestampExtractor(
        re.compile(r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?")
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
    issue = MechBoundaryIssue(
        kind="unsafe_cycle_split",
        severity="error",
        conflicts=[
            {
                "before_log": {"raw_excerpt": "before raw excerpt"},
                "after_log": {"raw_excerpt": "after raw excerpt"},
            },
        ],
        evidence=[
            {"raw_excerpt": "evidence raw excerpt"},
        ],
    )
    mech_result = MechResult(
        module_name="EXAMPLE",
        module_key="module1",
        slots=[
            MechSlotOutput(
                slot_id="1",
                board_cycles=[cycle],
                boundary_issues=[issue],
            ),
        ],
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
    serialized = json.dumps(data, ensure_ascii=False)
    assert "raw line that should only live" not in serialized
    assert "raw_excerpt" not in serialized
    assert "before raw excerpt" not in serialized


def test_lifecycle_split_v2_survives_serializer_query_and_cli(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        "\n".join([
            "2026-01-03T00:00:00 EXAMPLE Service=S; Slot=1; CPU-Id=0; "
            "ProcessName=dhcp-100; Context=old)",
            "2026-01-03T01:00:00 EXAMPLE Service=S; Slot=1; CPU-Id=0; "
            "ProcessName=dhcp-200; Context=new)",
        ]) + "\n",
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="1", name="slot_1", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    parse_result = ParseResult(
        task_id="task",
        package_name="package.zip",
        diagnostic_slots=[slot],
    )
    plugin = Module1Plugin(
        _module1_v2_test_config(),
        module_key="module1",
        ts_extractor=_timestamp_extractor(),
    )
    mech = plugin.parse(parse_result)
    assert mech is not None
    parse_result.mech_results.append(mech)

    _print_summary(parse_result, tmp_path)

    svc = ResultQueryService(tmp_path)
    groups = svc.mech_lifecycles("task", slot_id="1", module_name="EXAMPLE")
    v2 = groups[0]["lifecycle_split_result"]
    assert v2["lifecycle_reliable"] is True
    assert v2["boundaries"][0]["origin_scope"] == "board"
    assert v2["scopes"][0]["effective_boundaries"][0]["scope"] == "board"
    assert v2["cycles"][0]["cycle_index"] == 0
    assert v2["evidence"][0]["support_type"] == "tight_support"
    assert v2["issues"] == []

    cli_result = CliRunner().invoke(
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
    assert cli_result.exit_code == 0, cli_result.output
    assert "lifecycle_split_v2: reliable=true boundaries=1 evidence=1 issues=0" in cli_result.output
    assert "boundary reliable_process_pid_changed scope=board" in cli_result.output
    assert "evidence reliable_process_pid_changed scope=board support=tight_support covered=1" in cli_result.output


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
    assert (
        "conflict other-500@board spans split=2026-01-03T00:00:10+08:00 "
        "before=2026-01-03T00:00:05+08:00 after=2026-01-03T00:00:12+08:00"
    ) in result.output
    assert "before diagnostic|slot_1/diag.log seq=0 raw=before raw" in result.output
    assert "evidence diagnostic|slot_1/diag.log" not in result.output
    assert "hint python cli.py mech-logs task -s 1 -c <board_cycle> -p other-500 -m EXAMPLE" in result.output


def test_mech_lifecycles_show_boundaries_displays_lifecycle_split_v2(tmp_path):
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
                                "lifecycle_split_result": {
                                    "lifecycle_reliable": False,
                                    "boundaries": [
                                        {
                                            "origin_scope": "board",
                                            "timestamp": "2026-01-03T00:01:00+08:00",
                                            "type": "journal_sequence_wrapped",
                                            "support_evidence": [{"type": "journal_sequence_wrapped"}],
                                        },
                                    ],
                                    "evidence": [
                                        {
                                            "scope": "board",
                                            "type": "journal_sequence_wrapped",
                                            "support_type": "tight_support",
                                            "covered_boundaries": [{"id": "b1"}],
                                        },
                                    ],
                                    "issues": [
                                        {
                                            "type": "same_pid_single_boundary_conflict",
                                            "severity": "error",
                                            "scope": "cpu",
                                            "cpu_id": "1",
                                            "title_zh": "same PID conflict",
                                        },
                                    ],
                                },
                                "board_cycles": [
                                    {
                                        "dir_name": "c1",
                                        "processes": [],
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
            "--boundary-detail",
            "full",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "lifecycle_split_v2: reliable=false boundaries=1 evidence=1 issues=1" in result.output
    assert "[ERROR] same_pid_single_boundary_conflict scope=cpu_1 title=same PID conflict" in result.output
    assert "boundary journal_sequence_wrapped scope=board time=2026-01-03T00:01:00+08:00 support=1" in result.output
    assert "evidence journal_sequence_wrapped scope=board support=tight_support covered=1" in result.output


def test_mech_lifecycles_compact_other_dfx_events_are_human_readable(tmp_path):
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
                                        "kind": "same_pid_adjusted_backward",
                                        "severity": "warning",
                                        "action": "adjusted_backward",
                                        "reason": "adjusted_backward",
                                        "scope": "board",
                                        "split_time": "2026-01-03T13:04:18+08:00",
                                        "adjusted_time": "2026-01-03T13:04:12+08:00",
                                        "conflicts": [
                                            {
                                                "process_name": "other",
                                                "pid": "500",
                                                "cpu_id": "",
                                                "before_time": "2026-01-03T13:04:12+08:00",
                                                "after_time": "2026-01-03T13:04:21+08:00",
                                                "before_log": {
                                                    "source": "diagnostic",
                                                    "source_file": "slot_1/other.log",
                                                    "sequence": 0,
                                                    "raw_excerpt": "other before",
                                                },
                                                "after_log": {
                                                    "source": "diagnostic",
                                                    "source_file": "slot_1/other.log",
                                                    "sequence": 0,
                                                    "raw_excerpt": "other after",
                                                },
                                            },
                                        ],
                                        "protected_boundaries": [
                                            {
                                                "process_name": "noise",
                                                "cpu_id": "",
                                                "role": "whitelist",
                                                "old_pids": ["900"],
                                                "old_end": "2026-01-03T12:00:00+08:00",
                                                "new_pid": "901",
                                                "new_start": "2026-01-03T12:30:00+08:00",
                                            },
                                            {
                                                "process_name": "dhcp",
                                                "cpu_id": "",
                                                "role": "indicator",
                                                "old_pids": ["10"],
                                                "old_end": "2026-01-03T12:59:03+08:00",
                                                "new_pid": "20",
                                                "new_start": "2026-01-03T13:04:18+08:00",
                                            },
                                        ],
                                    },
                                    {
                                        "kind": "protected_forced_split",
                                        "severity": "warning",
                                        "reason": "protected_pid_change",
                                        "scope": "board",
                                        "split_time": "2026-01-03T00:00:06+08:00",
                                        "protected_boundaries": [
                                            {
                                                "process_name": "svc_a",
                                                "cpu_id": "",
                                                "role": "whitelist",
                                                "old_pids": ["300"],
                                                "old_end": "2026-01-03T00:00:05+08:00",
                                                "new_pid": "400",
                                                "new_start": "2026-01-03T00:00:06+08:00",
                                                "old_log": {
                                                    "source": "diagnostic",
                                                    "source_file": "slot_1/svc_a.log",
                                                    "sequence": 0,
                                                    "raw_excerpt": "svc old",
                                                },
                                                "new_log": {
                                                    "source": "diagnostic",
                                                    "source_file": "slot_1/svc_a.log",
                                                    "sequence": 0,
                                                    "raw_excerpt": "svc new",
                                                },
                                            },
                                        ],
                                    },
                                    {
                                        "kind": "suspect_pid_bounce",
                                        "severity": "warning",
                                        "reason": "indicator_pid_bounce",
                                        "scope": "board",
                                        "split_time": "2026-01-03T00:00:02+08:00",
                                        "evidence": [
                                            {
                                                "role": "pid_bounce_1",
                                                "source": "diagnostic",
                                                "source_file": "slot_1/dhcp.log",
                                                "process_name": "dhcp",
                                                "pid": "100",
                                                "cpu_id": "",
                                                "sequence": 0,
                                                "raw_excerpt": "dhcp 100",
                                            },
                                            {
                                                "role": "pid_bounce_2",
                                                "source": "diagnostic",
                                                "source_file": "slot_1/dhcp.log",
                                                "process_name": "dhcp",
                                                "pid": "200",
                                                "cpu_id": "",
                                                "sequence": 0,
                                                "raw_excerpt": "dhcp 200",
                                            },
                                            {
                                                "role": "pid_bounce_3",
                                                "source": "diagnostic",
                                                "source_file": "slot_1/dhcp.log",
                                                "process_name": "dhcp",
                                                "pid": "100",
                                                "cpu_id": "",
                                                "sequence": 0,
                                                "raw_excerpt": "dhcp 100 again",
                                            },
                                        ],
                                    },
                                    {
                                        "kind": "scoped_cpu_split",
                                        "severity": "info",
                                        "reason": "cpu_local_split",
                                        "scope": "cpu:1",
                                        "split_time": "2026-01-03T00:00:05+08:00",
                                        "evidence": [
                                            {
                                                "role": "context_before",
                                                "source": "diagnostic",
                                                "source_file": "slot_1/cpu.log",
                                                "process_name": "dhcp",
                                                "pid": "10",
                                                "cpu_id": "1",
                                                "sequence": 0,
                                                "raw_excerpt": "cpu before",
                                            },
                                        ],
                                    },
                                    {
                                        "kind": "suspect_over_split",
                                        "severity": "info",
                                        "reason": "protected_merge_has_no_pid_conflict",
                                        "scope": "board",
                                        "split_time": "2026-01-03T00:00:07+08:00",
                                        "evidence": [
                                            {
                                                "role": "over_split_left",
                                                "source": "diagnostic",
                                                "source_file": "slot_1/dhcp.log",
                                                "process_name": "dhcp",
                                                "pid": "100",
                                                "cpu_id": "",
                                                "sequence": 0,
                                                "raw_excerpt": "over split left",
                                            },
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
    assert (
        "conflict other-500@board spans split=2026-01-03T13:04:18+08:00 "
        "before=2026-01-03T13:04:12+08:00 after=2026-01-03T13:04:21+08:00"
    ) in result.output
    assert "blocked-by dhcp@board role=indicator safe_gap=(2026-01-03T12:59:03+08:00, 2026-01-03T13:04:18+08:00]" in result.output
    assert "blocked-by noise" not in result.output
    assert "pid-change svc_a@board role=whitelist 300 -> 400 split=2026-01-03T00:00:06+08:00" in result.output
    assert "pid-bounce dhcp@board 100 -> 200 -> 100" in result.output
    assert "[INFO] scoped_cpu_split reason=cpu_local_split scope=cpu:1 split=2026-01-03T00:00:05+08:00" in result.output
    assert "context dhcp-10@1 role=context_before" in result.output
    assert "[INFO] suspect_over_split reason=protected_merge_has_no_pid_conflict scope=board split=2026-01-03T00:00:07+08:00" in result.output
    assert "context dhcp-100@board role=over_split_left" in result.output
    assert "INFO 诊断 2 个: scoped_cpu_split=1 suspect_over_split=1" in result.output
    assert "cpu before" not in result.output
    assert "over split left" not in result.output


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
                                            "python cli.py mech-logs <task_id> -s 1 -c <board_cycle> -p svc_a-300 -m EXAMPLE",
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
    assert (
        "conflict-pair svc_a-300@board old_end=2026-01-03T00:00:10+08:00 "
        "overlaps dhcp-200@board new_start=2026-01-03T00:00:09+08:00"
    ) in result.output
    assert "old-side svc_a-300@board role=whitelist old_end=2026-01-03T00:00:10+08:00 raw=svc old" in result.output
    assert "new-side dhcp-200@board role=indicator new_start=2026-01-03T00:00:09+08:00 raw=dhcp new" in result.output
    assert "noise" not in result.output
    assert result.output.count("hint ") == 1
    assert "hint python cli.py mech-logs task -s 1 -c <board_cycle> -p svc_a-300 -m EXAMPLE" in result.output


def test_mech_lifecycles_restart_overlap_infers_conflict_pair_from_boundaries(tmp_path):
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
                                        "protected_boundaries": [
                                            {
                                                "process_name": "dhcp",
                                                "cpu_id": "",
                                                "role": "indicator",
                                                "old_pids": ["100"],
                                                "old_end": "2026-01-03T00:00:00+08:00",
                                                "new_pid": "200",
                                                "new_start": "2026-01-03T00:00:09+08:00",
                                                "new_log": {"raw_excerpt": "dhcp new"},
                                            },
                                            {
                                                "process_name": "svc_a",
                                                "cpu_id": "",
                                                "role": "whitelist",
                                                "old_pids": ["300"],
                                                "old_end": "2026-01-03T00:00:10+08:00",
                                                "new_pid": "400",
                                                "new_start": "2026-01-03T00:00:11+08:00",
                                                "old_log": {"raw_excerpt": "svc old"},
                                            },
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
    assert (
        "conflict-pair svc_a-300@board old_end=2026-01-03T00:00:10+08:00 "
        "overlaps dhcp-200@board new_start=2026-01-03T00:00:09+08:00"
    ) in result.output


def test_mech_lifecycles_restart_overlap_matches_naive_and_offset_times(tmp_path):
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
                                        "split_time": "2026-01-03T00:00:10.000001",
                                        "old_pid_end": "2026-01-03T00:00:10",
                                        "new_pid_start": "2026-01-03T00:00:09",
                                        "protected_boundaries": [
                                            {
                                                "process_name": "dhcp",
                                                "cpu_id": "",
                                                "role": "indicator",
                                                "old_pids": ["100"],
                                                "old_end": "2026-01-03T00:00:00+08:00",
                                                "new_pid": "200",
                                                "new_start": "2026-01-03T00:00:09+08:00",
                                            },
                                            {
                                                "process_name": "svc_a",
                                                "cpu_id": "",
                                                "role": "whitelist",
                                                "old_pids": ["300"],
                                                "old_end": "2026-01-03T00:00:10+08:00",
                                                "new_pid": "400",
                                                "new_start": "2026-01-03T00:00:11+08:00",
                                            },
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
    assert (
        "conflict-pair svc_a-300@board old_end=2026-01-03T00:00:10 "
        "overlaps dhcp-200@board new_start=2026-01-03T00:00:09"
    ) in result.output


def test_mech_lifecycles_restart_overlap_uses_evidence_when_boundaries_missing(tmp_path):
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
                                        "evidence": [
                                            {
                                                "role": "protected_new",
                                                "source": "diagnostic",
                                                "source_file": "slot_1/dhcp.log",
                                                "process_name": "dhcp",
                                                "pid": "200",
                                                "cpu_id": "",
                                                "timestamp": "2026-01-03T00:00:09+08:00",
                                                "sequence": 0,
                                                "raw_excerpt": "dhcp new",
                                            },
                                            {
                                                "role": "protected_old",
                                                "source": "diagnostic",
                                                "source_file": "slot_1/svc_a.log",
                                                "process_name": "svc_a",
                                                "pid": "300",
                                                "cpu_id": "",
                                                "timestamp": "2026-01-03T00:00:10+08:00",
                                                "sequence": 0,
                                                "raw_excerpt": "svc old",
                                            },
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
    assert (
        "conflict-pair svc_a-300@board old_end=2026-01-03T00:00:10+08:00 "
        "overlaps dhcp-200@board new_start=2026-01-03T00:00:09+08:00"
    ) in result.output
    assert "old-side svc_a-300@board role=protected_old old_end=2026-01-03T00:00:10+08:00 raw=svc old" in result.output
    assert "new-side dhcp-200@board role=protected_new new_start=2026-01-03T00:00:09+08:00 raw=dhcp new" in result.output


def test_mech_lifecycles_restart_overlap_same_process_fallback_shows_new_pid(tmp_path):
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
                                        "evidence": [
                                            {
                                                "role": "protected_new",
                                                "source": "diagnostic",
                                                "source_file": "slot_1/svc_a.log",
                                                "process_name": "svc_a",
                                                "pid": "400",
                                                "cpu_id": "",
                                                "timestamp": "2026-01-03T00:00:09+08:00",
                                                "sequence": 0,
                                                "raw_excerpt": "svc new",
                                            },
                                            {
                                                "role": "protected_old",
                                                "source": "diagnostic",
                                                "source_file": "slot_1/svc_a.log",
                                                "process_name": "svc_a",
                                                "pid": "300",
                                                "cpu_id": "",
                                                "timestamp": "2026-01-03T00:00:10+08:00",
                                                "sequence": 0,
                                                "raw_excerpt": "svc old",
                                            },
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
    assert "conflict-pair svc_a-300@board old_end=2026-01-03T00:00:10+08:00 overlaps svc_a-400@board new_start=2026-01-03T00:00:09+08:00" in result.output
    assert "boundary svc_a@board role=protected_old 300->400 old_end=2026-01-03T00:00:10+08:00 new_start=2026-01-03T00:00:09+08:00" in result.output


def test_mech_lifecycles_show_boundaries_accepts_old_result_without_boundary_fields(tmp_path):
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
                                "board_cycles": [
                                    {
                                        "dir_name": "cycle_1",
                                        "processes": [
                                            {
                                                "process_name": "svc",
                                                "pid": "100",
                                                "total_count": 1,
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
    assert "生命周期可靠性: true" in result.output
    assert "生命周期切分诊断" not in result.output
    assert "cycle_1" in result.output
    assert "svc-100: 1" in result.output


def test_mech_lifecycles_compact_infers_key_lines_from_evidence_or_detail(tmp_path):
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
                                        "kind": "same_pid_adjusted_backward",
                                        "severity": "warning",
                                        "action": "adjusted_backward",
                                        "reason": "adjusted_backward",
                                        "scope": "board",
                                        "split_time": "2026-01-03T13:04:18+08:00",
                                        "conflicts": [
                                            {
                                                "pid": "500",
                                            },
                                        ],
                                        "evidence": [
                                            {
                                                "role": "conflict_before",
                                                "source": "diagnostic",
                                                "source_file": "slot_1/other.log",
                                                "process_name": "other",
                                                "pid": "500",
                                                "cpu_id": "",
                                                "timestamp": "2026-01-03T13:04:12+08:00",
                                                "sequence": 0,
                                                "raw_excerpt": "other before",
                                            },
                                            {
                                                "role": "conflict_after",
                                                "source": "diagnostic",
                                                "source_file": "slot_1/other.log",
                                                "process_name": "other",
                                                "pid": "500",
                                                "cpu_id": "",
                                                "timestamp": "2026-01-03T13:04:21+08:00",
                                                "sequence": 0,
                                                "raw_excerpt": "other after",
                                            },
                                        ],
                                    },
                                    {
                                        "kind": "protected_forced_split",
                                        "severity": "warning",
                                        "reason": "protected_pid_change",
                                        "scope": "board",
                                        "split_time": "2026-01-03T00:00:06+08:00",
                                        "protected_boundaries": [
                                            {
                                                "new_pid": "400",
                                            },
                                        ],
                                        "evidence": [
                                            {
                                                "role": "protected_old",
                                                "source": "diagnostic",
                                                "source_file": "slot_1/svc_a.log",
                                                "process_name": "svc_a",
                                                "pid": "300",
                                                "cpu_id": "",
                                                "timestamp": "2026-01-03T00:00:05+08:00",
                                                "sequence": 0,
                                                "raw_excerpt": "svc old",
                                            },
                                            {
                                                "role": "protected_new",
                                                "source": "diagnostic",
                                                "source_file": "slot_1/svc_a.log",
                                                "process_name": "svc_a",
                                                "pid": "400",
                                                "cpu_id": "",
                                                "timestamp": "2026-01-03T00:00:06+08:00",
                                                "sequence": 0,
                                                "raw_excerpt": "svc new",
                                            },
                                        ],
                                    },
                                    {
                                        "kind": "suspect_pid_bounce",
                                        "severity": "warning",
                                        "reason": "indicator_pid_bounce",
                                        "scope": "board",
                                        "split_time": "2026-01-03T00:00:02+08:00",
                                        "detail": "proc=dhcp pids=100>200>100",
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
    assert (
        "conflict other-500@board spans split=2026-01-03T13:04:18+08:00 "
        "before=2026-01-03T13:04:12+08:00 after=2026-01-03T13:04:21+08:00"
    ) in result.output
    assert "before diagnostic|slot_1/other.log seq=0 raw=other before" in result.output
    assert "after diagnostic|slot_1/other.log seq=0 raw=other after" in result.output
    assert "pid-change svc_a@board role=- 300 -> 400 split=2026-01-03T00:00:06+08:00" in result.output
    assert "old diagnostic|slot_1/svc_a.log seq=0 raw=svc old" in result.output
    assert "new diagnostic|slot_1/svc_a.log seq=0 raw=svc new" in result.output
    assert "pid-bounce dhcp@board 100 -> 200 -> 100" in result.output


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


def test_mech_lifecycles_boundary_detail_full_keeps_compact_key_lines(tmp_path):
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
                                                "process_name": "dhcp",
                                                "cpu_id": "",
                                                "role": "indicator",
                                                "old_pids": ["100"],
                                                "old_end": "2026-01-03T00:00:00+08:00",
                                                "new_pid": "200",
                                                "new_start": "2026-01-03T00:00:09+08:00",
                                            },
                                            {
                                                "process_name": "svc_a",
                                                "cpu_id": "",
                                                "role": "whitelist",
                                                "old_pids": ["300"],
                                                "old_end": "2026-01-03T00:00:10+08:00",
                                                "new_pid": "400",
                                                "new_start": "2026-01-03T00:00:11+08:00",
                                            },
                                        ],
                                    },
                                    {
                                        "kind": "same_pid_adjusted_backward",
                                        "severity": "warning",
                                        "action": "adjusted_backward",
                                        "reason": "adjusted_backward",
                                        "scope": "board",
                                        "split_time": "2026-01-03T13:04:18+08:00",
                                        "conflicts": [
                                            {
                                                "process_name": "other",
                                                "pid": "500",
                                                "cpu_id": "",
                                                "before_time": "2026-01-03T13:04:12+08:00",
                                                "after_time": "2026-01-03T13:04:21+08:00",
                                            },
                                        ],
                                    },
                                    {
                                        "kind": "protected_forced_split",
                                        "severity": "warning",
                                        "reason": "protected_pid_change",
                                        "scope": "board",
                                        "split_time": "2026-01-03T00:00:06+08:00",
                                        "protected_boundaries": [
                                            {
                                                "process_name": "svc_a",
                                                "cpu_id": "",
                                                "role": "whitelist",
                                                "old_pids": ["300"],
                                                "old_end": "2026-01-03T00:00:05+08:00",
                                                "new_pid": "400",
                                                "new_start": "2026-01-03T00:00:06+08:00",
                                            },
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
    assert (
        "conflict-pair svc_a-300@board old_end=2026-01-03T00:00:10+08:00 "
        "overlaps dhcp-200@board new_start=2026-01-03T00:00:09+08:00"
    ) in result.output
    assert (
        "conflict other-500@board spans split=2026-01-03T13:04:18+08:00 "
        "before=2026-01-03T13:04:12+08:00 after=2026-01-03T13:04:21+08:00"
    ) in result.output
    assert "pid-change svc_a@board role=whitelist 300 -> 400 split=2026-01-03T00:00:06+08:00" in result.output
    assert "protected svc_a@board role=whitelist old_pids=300 new_pid=400" in result.output


def test_mech_lifecycles_compact_deduplicates_unsafe_and_same_pid_kept(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    conflict = {
        "process_name": "other",
        "pid": "500",
        "cpu_id": "",
        "before_time": "2026-01-03T00:00:05+08:00",
        "after_time": "2026-01-03T00:00:12+08:00",
    }
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
                                        "conflicts": [conflict],
                                    },
                                    {
                                        "kind": "same_pid_kept",
                                        "severity": "error",
                                        "action": "kept",
                                        "reason": "no_safe_gap_candidate",
                                        "scope": "board",
                                        "split_time": "2026-01-03T00:00:10+08:00",
                                        "conflicts": [conflict],
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
    assert result.output.count("conflict other-500@board spans split=2026-01-03T00:00:10+08:00") == 1
    assert "[ERROR] same_pid_kept action=kept reason=no_safe_gap_candidate" in result.output
    assert "same-evidence-as unsafe_cycle_split above" in result.output


def test_mech_lifecycles_compact_reports_unavailable_evidence_without_fake_process(tmp_path):
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
                                        "kind": "same_pid_adjusted",
                                        "severity": "warning",
                                        "reason": "adjusted",
                                        "scope": "board",
                                        "split_time": "2026-01-03T00:00:10+08:00",
                                    },
                                    {
                                        "kind": "protected_forced_split",
                                        "severity": "warning",
                                        "reason": "protected_pid_change",
                                        "scope": "board",
                                        "split_time": "2026-01-03T00:00:11+08:00",
                                    },
                                    {
                                        "kind": "suspect_pid_bounce",
                                        "severity": "warning",
                                        "reason": "indicator_pid_bounce",
                                        "scope": "board",
                                        "split_time": "2026-01-03T00:00:12+08:00",
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
    assert "-@board" not in result.output
    assert "conflict evidence unavailable; use --boundary-detail full" in result.output
    assert "pid-change evidence unavailable; use --boundary-detail full" in result.output
    assert "pid-bounce evidence unavailable; use --boundary-detail full" in result.output
