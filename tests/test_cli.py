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
from backend.parsing.lifecycle_common import LifecycleSplitConfig
from backend.parsing.timestamp_extractor import TimestampExtractor
from backend.plugins.mechanisms.module1 import Module1Plugin
from backend.query import ResultQueryService
from backend.utils import safe_log_filename, safe_path_segment
from backend.models import (
    LogEntry,
    MechBoardCycle,
    MechLogEntry,
    MechProcessLifecycle,
    MechResult,
    MechSlotOutput,
    ParseResult,
    SlotInfo,
)


def _module1_v3_test_config() -> dict:
    return {
        "module_name": "EXAMPLE",
        "diag_pattern": (
            r"Service=(?P<Service>[^;]+).*?Slot=(?P<Slot>[^;,)]+).*?"
            r"CPU-Id=(?P<CPU_Id>[^;,)]*).*?"
            r"ProcessName=(?P<ProcessName>[^;,)]+).*?"
            r"Context=(?P<Context>.+?)\)$"
        ),
        "active_master_keyword": "",
        "journal": {
            "line_pattern": "",
            "line_pattern2": "",
            "identifying_keyword": "EXAMPLE",
        },
        "sequence_pattern": r"No\[(\d+)\]",
        "lifecycle_split": {
            "reliable_processes": ["dhcp"],
            "multi_instance_processes": [],
        },
    }


def _timestamp_extractor() -> TimestampExtractor:
    return TimestampExtractor(
        re.compile(r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?")
    )


def _valid_parse_config(extra: dict | None = None) -> dict:
    cfg = {
        "products": {
            "default": {
                "discovery": {
                    "plugin": "backend.plugins.default.scanner.ScannerPlugin",
                    "config": {
                        "diagnostic_dir": "diag",
                        "private_dir": "varlog",
                        "slot_dir_pattern": "slot_*",
                        "diag_file_patterns": ["diag.zip"],
                    },
                },
                "log_parser": {
                    "plugin": "backend.plugins.default.parser.ParserPlugin",
                    "config": {
                        "timestamp_regex": r"(\d{4}-\d{2}-\d{2})([+-]\d{2}:\d{2})?",
                    },
                },
            },
        },
    }
    if extra:
        cfg.update(extra)
    return cfg


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


def test_parse_profile_passes_profile_and_prints_summary(tmp_path, monkeypatch):
    package_path = tmp_path / "package.zip"
    package_path.write_text("placeholder", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            _valid_parse_config({
                "pipeline": {"debug_expand_gz": False},
            }),
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    seen = {}
    cli_module = importlib.import_module("cli")

    class FakePerformance:
        def summary_lines(self):
            return [
                "性能DFX: total=1.2s",
                "慢阶段: pipeline.parse 1.0s",
            ]

    class FakePipeline:
        def __init__(self, config):
            assert config["pipeline"]["debug_expand_gz"] is False
            self.performance = FakePerformance()

        def run(self, source, output_dir, product="default", verbose=False, profile=False):
            seen["profile"] = profile
            return ParseResult(task_id="task", package_name=source.name)

    monkeypatch.setattr(cli_module, "Pipeline", FakePipeline)

    result = CliRunner().invoke(
        cli,
        [
            "parse",
            str(package_path),
            "-c",
            str(config_path),
            "-o",
            str(tmp_path / "out"),
            "--profile",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["profile"] is True
    assert "性能DFX: total=1.2s" in result.output
    assert "慢阶段: pipeline.parse 1.0s" in result.output


def _write_target_log_result(tmp_path, *, with_log=True):
    task_dir = tmp_path / "task"
    task_dir.mkdir(exist_ok=True)
    (task_dir / "result.json").write_text(
        json.dumps(
            {
                "mech_results": [
                    {
                        "module_key": "module1",
                        "module_name": "EXAMPLE",
                        "slots": [
                            {
                                "slot_id": "1",
                                "board_cycles": [
                                    {
                                        "dir_name": "cycle",
                                        "start_time": "2026-01-03T00:00:00",
                                        "end_time": "2026-01-03T00:10:00",
                                        "processes": [
                                            {
                                                "process_name": "SERVICE",
                                                "pid": "123",
                                                "total_count": 1,
                                                "missing_sequences": [],
                                                "missing_count": 0,
                                            },
                                        ],
                                        "cpu_cycles": [],
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
    if with_log:
        log_dir = (
            task_dir / "mech_modules" / safe_path_segment("EXAMPLE") / "slot_1"
            / safe_path_segment("cycle")
        )
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / safe_log_filename("SERVICE", "123")).write_text("matched log\n", encoding="utf-8")


def test_mech_target_logs_outputs_json_without_cycle_argument(tmp_path):
    _write_target_log_result(tmp_path, with_log=True)

    result = CliRunner().invoke(
        cli,
        [
            "mech-target-logs",
            "task",
            "--problem-time",
            "2026-01-03T00:05:00",
            "--module",
            "module1",
            "--slot",
            "slot_1",
            "--process-name",
            "service",
            "--pid",
            "123",
            "--label",
            "client",
            "-o",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    target = payload["target_logs"][0]
    assert target["label"] == "client"
    assert target["match_status"] == "exact"
    assert target["board_cycle"] == "cycle"
    assert target["log_path"].endswith(safe_log_filename("SERVICE", "123"))


def test_mech_target_logs_reports_missing_log_without_guessing(tmp_path):
    _write_target_log_result(tmp_path, with_log=False)

    result = CliRunner().invoke(
        cli,
        [
            "mech-target-logs",
            "task",
            "--problem-time",
            "2026-01-03T00:05:00",
            "--module",
            "EXAMPLE",
            "--slot",
            "1",
            "--process-name",
            "SERVICE",
            "--pid",
            "123",
            "-o",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    target = payload["target_logs"][0]
    assert target["match_status"] == "missing"
    assert "log_path" not in target
    assert any("log file missing" in caveat for caveat in target["caveats"])


def test_mech_target_logs_explain_includes_selection_diagnostics(tmp_path):
    _write_target_log_result(tmp_path, with_log=False)

    result = CliRunner().invoke(
        cli,
        [
            "mech-target-logs",
            "task",
            "--problem-time",
            "2026-01-03T00:05:00",
            "--module",
            "EXAMPLE",
            "--slot",
            "1",
            "--process-name",
            "SERVICE",
            "--pid",
            "123",
            "-o",
            str(tmp_path),
            "--explain",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    target = payload["target_logs"][0]
    diagnostics = payload["selection_diagnostics"]
    assert target["error_code"] == "LP_TARGET_LOG_MISSING"
    assert diagnostics["candidate_count"] == 1
    assert diagnostics["error_code"] == "LP_TARGET_LOG_MISSING"


def _write_mech_log_file(tmp_path, process_name, pid, content):
    log_dir = (
        tmp_path / "task" / "mech_modules" / safe_path_segment("EXAMPLE") / "slot_1"
        / safe_path_segment("cycle")
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / safe_log_filename(process_name, pid)
    path.write_text(content, encoding="utf-8")
    return path


def test_mech_logs_uses_explicit_pid_option(tmp_path):
    _write_mech_log_file(tmp_path, "SERVICE", "123", "matched log\n")

    result = CliRunner().invoke(
        cli,
        [
            "mech-logs",
            "task",
            "-s",
            "1",
            "-c",
            "cycle",
            "-p",
            "SERVICE",
            "--pid",
            "123",
            "-m",
            "EXAMPLE",
            "-o",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "matched log"


def test_mech_logs_uses_legacy_dash_filename_without_pid(tmp_path):
    _write_mech_log_file(tmp_path, "svc", "100", "legacy log\n")

    result = CliRunner().invoke(
        cli,
        [
            "mech-logs",
            "task",
            "-s",
            "1",
            "-c",
            "cycle",
            "-p",
            "svc-100",
            "-m",
            "EXAMPLE",
            "-o",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "legacy log"


def test_mech_logs_explicit_pid_uses_same_legacy_dash_filename(tmp_path):
    _write_mech_log_file(tmp_path, "svc", "100", "legacy log\n")

    result = CliRunner().invoke(
        cli,
        [
            "mech-logs",
            "task",
            "-s",
            "1",
            "-c",
            "cycle",
            "-p",
            "svc",
            "--pid",
            "100",
            "-m",
            "EXAMPLE",
            "-o",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "legacy log"


def test_parse_validates_config_before_running_pipeline(tmp_path, monkeypatch):
    package_path = tmp_path / "package.zip"
    package_path.write_text("placeholder", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "products": {
                    "default": {
                        "discovery": {
                            "plugin": "backend.plugins.default.scanner.ScannerPlugin",
                            "config": {
                                "diagnostic_dir": "diag",
                                "private_dir": "varlog",
                                "slot_dir_pattern": "slot_*",
                                "diag_file_patterns": ["diag.zip"],
                            },
                        },
                        "log_parser": {
                            "plugin": "backend.plugins.default.parser.ParserPlugin",
                            "config": {
                                "timestamp_regex": r"(\d{4}-\d{2}-\d{2})([+-]\d{2}:\d{2})?",
                                "mechanism_modules": {
                                    "module1": {
                                        "plugin": "backend.plugins.mechanisms.module1.Module1Plugin",
                                        "enabled": True,
                                            "config": {
                                                "module_name": "EXAMPLE",
                                                "lifecycle_split": {
                                                    "reliable_processes": ["anchor"],
                                                    "multi_instance_processes": ["anchor"],
                                                },
                                        },
                                    },
                                },
                            },
                        },
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
            raise AssertionError("Pipeline should not run when config is invalid")

    monkeypatch.setattr(cli_module, "Pipeline", FakePipeline)

    result = CliRunner().invoke(
        cli,
        [
            "parse",
            str(package_path),
            "-c",
            str(config_path),
        ],
    )

    assert result.exit_code == 1, result.output
    assert "配置检查失败" in result.output
    assert "config conflict" in result.output
    assert "reliable_processes" in result.output
    assert "multi_instance_processes" in result.output
    assert "anchor" in result.output


def test_parse_lifecycle_dfx_decisions_prints_v3_chinese_report(tmp_path, monkeypatch):
    package_path = tmp_path / "package.zip"
    package_path.write_text("placeholder", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(_valid_parse_config(), allow_unicode=True),
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
                mech_results=[
                    MechResult(
                        module_name="EXAMPLE",
                        module_key="module1",
                        slots=[
                            MechSlotOutput(
                                slot_id="1",
                                lifecycle_reliable=True,
                                lifecycle_split_result={
                                    "algorithm": "interval_v3",
                                    "lifecycle_reliable": True,
                                    "candidate_segments": [
                                        {
                                            "scope": "board",
                                            "slot": "1",
                                            "candidate_index": 0,
                                            "start_time": "2026-01-03T00:00:00+08:00",
                                            "end_time": "2026-01-03T00:01:00+08:00",
                                            "log_count": 2,
                                        },
                                        {
                                            "scope": "board",
                                            "slot": "1",
                                            "candidate_index": 1,
                                            "start_time": "2026-01-03T00:01:30+08:00",
                                            "end_time": "2026-01-03T00:02:00+08:00",
                                            "log_count": 1,
                                        },
                                    ],
                                    "merge_decisions": [
                                        {
                                            "scope": "board",
                                            "slot": "1",
                                            "left_candidate_indices": [0],
                                            "right_candidate_indices": [1],
                                            "left_end_time": "2026-01-03T00:01:00+08:00",
                                            "right_start_time": "2026-01-03T00:01:30+08:00",
                                            "silent_gap_seconds": 30,
                                            "decision": "merged",
                                            "blocking_reason": "",
                                            "reliable_pid_counts": [
                                                {
                                                    "process_name": "procA",
                                                    "pids": ["100"],
                                                    "count": 1,
                                                }
                                            ],
                                            "reason_zh": (
                                                "所有白名单进程 PID 数均不超过 1，没有白名单进程 PID 冲突，"
                                                "判断为同一生命周期内日志分段打印。"
                                            ),
                                        },
                                    ],
                                    "lifecycles": [
                                        {
                                            "scope": "board",
                                            "slot": "1",
                                            "lifecycle_index": 0,
                                            "candidate_indices": [0, 1],
                                            "start_time": "2026-01-03T00:00:00+08:00",
                                            "end_time": "2026-01-03T00:02:00+08:00",
                                            "lifecycle_reliable": True,
                                        },
                                    ],
                                    "journal_evidence": [],
                                    "issues": [],
                                },
                            ),
                        ],
                    ),
                ],
            )

    monkeypatch.setattr(cli_module, "Pipeline", FakePipeline)

    result = CliRunner().invoke(
        cli,
        [
            "parse",
            str(package_path),
            "-c",
            str(config_path),
            "--lifecycle-dfx",
            "decisions",
            "-o",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "[结论摘要]" in result.output
    assert "最终切成 1 段，可靠性=true" in result.output
    assert "[候选切分]" in result.output
    assert "静默间隔：30 秒" in result.output
    assert "[聚合检查]" in result.output
    assert "最终决策：聚合为同一个生命周期" in result.output
    assert "没有白名单进程 PID 冲突" in result.output
    assert "[最终生命周期]" in result.output


def test_parse_verbose_does_not_print_v3_lifecycle_dfx_without_lifecycle_dfx(tmp_path, monkeypatch):
    package_path = tmp_path / "package.zip"
    package_path.write_text("placeholder", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(_valid_parse_config(), allow_unicode=True),
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
                mech_results=[
                    MechResult(
                        module_name="EXAMPLE",
                        module_key="module1",
                        slots=[
                            MechSlotOutput(
                                slot_id="1",
                                lifecycle_reliable=True,
                                lifecycle_split_result={
                                    "algorithm": "interval_v3",
                                    "candidate_segments": [
                                        {
                                            "scope": "board",
                                            "slot": "1",
                                            "candidate_index": 0,
                                            "start_time": "2026-01-03T00:00:00+08:00",
                                            "end_time": "2026-01-03T00:00:00+08:00",
                                            "log_count": 1,
                                        }
                                    ],
                                    "merge_decisions": [],
                                    "lifecycles": [
                                        {
                                            "scope": "board",
                                            "slot": "1",
                                            "lifecycle_index": 0,
                                            "candidate_indices": [0],
                                            "start_time": "2026-01-03T00:00:00+08:00",
                                            "end_time": "2026-01-03T00:00:00+08:00",
                                            "lifecycle_reliable": True,
                                        }
                                    ],
                                    "journal_evidence": [],
                                    "issues": [],
                                },
                            ),
                        ],
                    ),
                ],
            )

    monkeypatch.setattr(cli_module, "Pipeline", FakePipeline)

    result = CliRunner().invoke(
        cli,
        [
            "parse",
            str(package_path),
            "-c",
            str(config_path),
            "--verbose",
            "-o",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "lifecycle_split_v3:" not in result.output
    assert "[候选切分]" not in result.output


def test_parse_lifecycle_dfx_full_labels_no_wrap_evidence_sources(tmp_path, monkeypatch):
    package_path = tmp_path / "package.zip"
    package_path.write_text("placeholder", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(_valid_parse_config(), allow_unicode=True),
        encoding="utf-8",
    )
    cli_module = importlib.import_module("cli")

    evidence = {
        "support_type": "boundary_support",
        "scope": "board",
        "slot": "1",
        "old_sequence": 99,
        "new_sequence": 1,
        "old_source": "diagnostic",
        "new_source": "journal",
        "old_observed_time": "2026-01-03T00:01:00+08:00",
        "new_observed_time": "2026-01-03T00:01:30+08:00",
        "explanation_zh": "No 回绕成立条件：可靠进程，忽略 PID，source 可为 diagnostic 或 journal。",
    }

    class FakePipeline:
        def __init__(self, _config):
            pass

        def run(self, source, output_dir, product="default", verbose=False):
            return ParseResult(
                task_id="task",
                package_name=source.name,
                mech_results=[
                    MechResult(
                        module_name="EXAMPLE",
                        module_key="module1",
                        slots=[
                            MechSlotOutput(
                                slot_id="1",
                                lifecycle_reliable=True,
                                lifecycle_split_result={
                                    "algorithm": "interval_v3",
                                    "candidate_segments": [
                                        {
                                            "scope": "board",
                                            "slot": "1",
                                            "candidate_index": 0,
                                            "start_time": "2026-01-03T00:00:00+08:00",
                                            "end_time": "2026-01-03T00:01:00+08:00",
                                            "log_count": 1,
                                        },
                                        {
                                            "scope": "board",
                                            "slot": "1",
                                            "candidate_index": 1,
                                            "start_time": "2026-01-03T00:01:30+08:00",
                                            "end_time": "2026-01-03T00:02:00+08:00",
                                            "log_count": 1,
                                        },
                                    ],
                                    "merge_decisions": [
                                        {
                                            "scope": "board",
                                            "slot": "1",
                                            "left_candidate_indices": [0],
                                            "right_candidate_indices": [1],
                                            "left_end_time": "2026-01-03T00:01:00+08:00",
                                            "right_start_time": "2026-01-03T00:01:30+08:00",
                                            "silent_gap_seconds": 30,
                                            "decision": "kept_split",
                                            "blocking_reason": "journal_wrap",
                                            "reliable_pid_counts": [],
                                            "journal_evidence": [evidence],
                                            "reason_zh": "No 回绕只作为阻断合并的证据。",
                                        },
                                    ],
                                    "lifecycles": [],
                                    "journal_evidence": [evidence],
                                    "issues": [],
                                },
                            ),
                        ],
                    ),
                ],
            )

    monkeypatch.setattr(cli_module, "Pipeline", FakePipeline)

    result = CliRunner().invoke(
        cli,
        [
            "parse",
            str(package_path),
            "-c",
            str(config_path),
            "--lifecycle-dfx",
            "full",
            "-o",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "No 回绕证据" in result.output
    assert "old_source=diagnostic new_source=journal" in result.output
    assert "journal 回绕证据" not in result.output


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


def test_test_pattern_journal_line_pattern2_required_substrings_pass(sample_config, tmp_path):
    sample_config["mechanism_modules"]["module1"]["config"]["journal"][
        "line_pattern2_required_substrings"
    ] = ["MODULE1"]
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
    line = "2026-01-03T00:01:00 host SERVICE-12345: No[7] EXAMPLE MODULE1 with sequence"

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
    assert "line_pattern2_required_substrings" in result.output
    assert "MODULE1" in result.output


def test_test_pattern_journal_line_pattern2_required_substrings_fail(sample_config, tmp_path):
    sample_config["mechanism_modules"]["module1"]["config"]["journal"][
        "line_pattern2_required_substrings"
    ] = ["MODULE1"]
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
    line = "2026-01-03T00:01:00 host SERVICE-12345: No[7] EXAMPLE module1 lower"

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
    assert "line_pattern2_required_substrings 未命中" in result.output


def test_mech_lifecycles_show_boundaries_dispatches_lifecycle_split_v3(tmp_path):
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
                                "lifecycle_reliable": True,
                                "lifecycle_split_result": {
                                    "algorithm": "interval_v3",
                                    "lifecycle_reliable": True,
                                    "candidate_segments": [
                                        {
                                            "scope": "board",
                                            "slot": "1",
                                            "candidate_index": 0,
                                            "start_time": "2026-01-03T00:00:00+08:00",
                                            "end_time": "2026-01-03T00:01:00+08:00",
                                            "log_count": 2,
                                        },
                                        {
                                            "scope": "board",
                                            "slot": "1",
                                            "candidate_index": 1,
                                            "start_time": "2026-01-03T00:01:30+08:00",
                                            "end_time": "2026-01-03T00:02:00+08:00",
                                            "log_count": 1,
                                        },
                                    ],
                                    "merge_decisions": [
                                        {
                                            "scope": "board",
                                            "slot": "1",
                                            "left_candidate_indices": [0],
                                            "right_candidate_indices": [1],
                                            "left_end_time": "2026-01-03T00:01:00+08:00",
                                            "right_start_time": "2026-01-03T00:01:30+08:00",
                                            "silent_gap_seconds": 30,
                                            "decision": "kept_split",
                                            "blocking_reason": "reliable_pid_conflict",
                                            "reliable_pid_counts": [
                                                {
                                                    "process_name": "anchor",
                                                    "pids": ["10", "11"],
                                                    "count": 2,
                                                }
                                            ],
                                            "reason_zh": "合并后白名单进程出现多个 PID：anchor PID=10,11。保留候选切分。",
                                        },
                                    ],
                                    "lifecycles": [
                                        {
                                            "scope": "board",
                                            "slot": "1",
                                            "lifecycle_index": 0,
                                            "candidate_indices": [0],
                                            "start_time": "2026-01-03T00:00:00+08:00",
                                            "end_time": "2026-01-03T00:01:00+08:00",
                                            "lifecycle_reliable": True,
                                        },
                                        {
                                            "scope": "board",
                                            "slot": "1",
                                            "lifecycle_index": 1,
                                            "candidate_indices": [1],
                                            "start_time": "2026-01-03T00:01:30+08:00",
                                            "end_time": "2026-01-03T00:02:00+08:00",
                                            "lifecycle_reliable": True,
                                        },
                                    ],
                                    "journal_evidence": [],
                                    "issues": [],
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
            "--lifecycle-dfx",
            "decisions",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "lifecycle_split_v3: reliable=true candidates=2 merged=0 kept_splits=1 lifecycles=2" in result.output
    assert "[候选切分]" in result.output
    assert "[聚合检查]" in result.output
    assert "候选边界 #1：board slot_1 #1 -> #2" in result.output
    assert "可靠边界进程 PID 统计（白名单）" in result.output
    assert "最终决策：保留切分" in result.output
    assert "保留原因：合并后可靠边界进程会出现多个 PID" in result.output
    assert "[最终生命周期]" in result.output


def test_mech_lifecycles_v3_compact_prints_invalid_evidence_chinese_reason(tmp_path):
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
                                        "algorithm": "interval_v3",
                                        "lifecycle_reliable": False,
                                        "candidate_segments": [],
                                        "merge_decisions": [],
                                        "lifecycles": [],
                                        "journal_evidence": [],
                                        "issues": [
                                        {
                                            "type": "invalid_lifecycle_evidence",
                                            "severity": "error",
                                            "scope": "board",
                                            "slot": "1",
                                            "related_process": "anchor",
                                            "reason_zh": "可靠进程 PID 变化证据缺少 timestamp 或 PID。",
                                            "source": "diagnostic",
                                            "source_file": "slot_1/diag.log",
                                            "raw_excerpt": "anchor without pid",
                                        },
                                    ],
                                },
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
    assert "[ERROR] invalid_lifecycle_evidence scope=board process=anchor" in result.output
    assert "原因: 可靠进程 PID 变化证据缺少 timestamp 或 PID。" in result.output


def test_mech_lifecycles_show_boundaries_reports_legacy_result_unsupported(tmp_path):
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
    assert "lifecycle_reliable: true" in result.output
    assert ("boundary_" + "issues") not in result.output
    assert "cycle_1" in result.output
    assert "svc-100: 1" in result.output
