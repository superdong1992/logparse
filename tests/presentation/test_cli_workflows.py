"""Exercise supported CLI workflows against generated, versioned artifacts."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil

from click.testing import CliRunner
import pytest
import yaml

from backend.application.configuration import load_config_file
from cli import cli


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tests/mock_data/diagnostic_information_20260103.zip"
CONFIG = ROOT / "config.yaml"


@pytest.fixture(scope="module")
def parsed_task(tmp_path_factory):
    output = tmp_path_factory.mktemp("cli-workflow")
    result = CliRunner().invoke(cli, [
        "parse", str(SOURCE), "-c", str(CONFIG), "-o", str(output),
    ])
    assert result.exit_code == 0, result.output
    return output, SOURCE.stem


@pytest.mark.parametrize(
    ("command", "options", "expected"),
    [
        ("info", [], '"diagnostic_slots"'),
        ("list-slots", [], "主控时段"),
        ("query-diag", ["--slot", "1"], '"diagnostic_logs"'),
        ("mech-slots", [], "EXAMPLE"),
        ("mech-lifecycles", ["--slot", "1"], "SERVICE-12345"),
    ],
)
def test_metadata_and_mechanism_queries_after_parse(parsed_task, command, options, expected):
    output, task_id = parsed_task
    result = CliRunner().invoke(cli, [command, task_id, *options, "-o", str(output)])

    assert result.exit_code == 0, result.output
    assert expected in result.output


@pytest.mark.parametrize(
    ("command", "options"),
    [
        ("info", []),
        ("list-slots", []),
        ("query-diag", ["--slot", "1"]),
        ("mech-slots", []),
        ("mech-lifecycles", ["--slot", "1"]),
    ],
)
def test_query_missing_task_returns_readable_failure(tmp_path, command, options):
    result = CliRunner().invoke(cli, [command, "missing", *options, "-o", str(tmp_path)])

    assert result.exit_code == 1
    assert "不存在" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("command", ["query-diag", "mech-lifecycles"])
def test_query_unknown_slot_reports_no_match(parsed_task, command):
    output, task_id = parsed_task
    result = CliRunner().invoke(cli, [command, task_id, "-s", "999", "-o", str(output)])

    assert result.exit_code == 0, result.output
    assert "未找到 slot_999" in result.output


def test_mech_slots_unknown_module_reports_no_results(parsed_task):
    output, task_id = parsed_task
    result = CliRunner().invoke(cli, [
        "mech-slots", task_id, "--module", "missing", "-o", str(output),
    ])
    assert result.exit_code == 0, result.output
    assert "无机制模块解析结果" in result.output


def test_metadata_survives_missing_result_and_empty_discovery(parsed_task, tmp_path):
    output, task_id = parsed_task
    task = tmp_path / task_id
    task.mkdir()
    metadata = json.loads((output / task_id / "metadata.json").read_text(encoding="utf-8"))
    metadata["diagnostic_slots"] = []
    (task / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    slots = CliRunner().invoke(cli, ["list-slots", task_id, "-o", str(tmp_path)])
    mechanisms = CliRunner().invoke(cli, ["mech-slots", task_id, "-o", str(tmp_path)])

    assert slots.exit_code == 0, slots.output
    assert slots.output == ""
    assert mechanisms.exit_code == 1
    assert "result.json 不存在" in mechanisms.output


def test_mech_logs_reads_selected_evidence_and_reports_missing_pid(parsed_task):
    output, task_id = parsed_task
    payload = json.loads((output / task_id / "result.json").read_text(encoding="utf-8"))
    module = payload["mech_results"][0]
    slot = module["slots"][0]
    cycle = slot["board_cycles"][0]
    process = cycle["processes"][0]
    args = [
        "mech-logs", task_id, "-s", slot["slot_id"], "-c", cycle["dir_name"],
        "-p", process["process_name"], "--module", module["module_name"], "-o", str(output),
    ]

    found = CliRunner().invoke(cli, [*args, "--pid", process["pid"]])
    missing = CliRunner().invoke(cli, [*args, "--pid", "does-not-exist"])

    assert found.exit_code == 0, found.output
    assert process["process_name"] in found.output
    assert missing.exit_code == 1
    assert "文件不存在" in missing.output


def test_nested_cpu_lifecycle_display_and_log_selection(tmp_path):
    from backend.extensions.products.current.artifacts import MechOutputWriter
    from backend.extensions.products.current.models import (
        MechBoardCycle, MechCpuCycle, MechLogEntry, MechProcessLifecycle,
        MechResult, MechSlotOutput, ParseResult,
    )
    from backend.extensions.products.current.result_serializer import result_to_dict

    process = MechProcessLifecycle(
        process_name="worker", pid="22", total_count=1, missing_sequences=[2],
        logs=[MechLogEntry(raw="CPU worker evidence")],
    )
    module = MechResult(module_name="EXAMPLE", module_key="module1", slots=[MechSlotOutput(
        slot_id="1", board_cycles=[MechBoardCycle(
            dir_name="board", cpu_cycles=[MechCpuCycle(
                cpu_id="3", dir_name="cpu-cycle", processes=[process],
            )],
        )],
    )])
    task_dir = tmp_path / "nested"
    MechOutputWriter().write(module, task_dir)
    (task_dir / "result.json").write_text(
        json.dumps(result_to_dict(ParseResult(task_id="nested", mech_results=[module]))),
        encoding="utf-8",
    )

    cycles = CliRunner().invoke(cli, ["mech-lifecycles", "nested", "-s", "1", "-o", str(tmp_path)])
    evidence = CliRunner().invoke(cli, [
        "mech-logs", "nested", "-s", "1", "-c", "board", "-p", "worker",
        "--pid", "22", "--module", "EXAMPLE", "--cpu", "3", "--cpu-cycle", "cpu-cycle",
        "-o", str(tmp_path),
    ])

    assert cycles.exit_code == evidence.exit_code == 0
    assert "cpu_3/cpu-cycle" in cycles.output
    assert "worker-22: 1 条 丢号:[2]" in cycles.output
    assert "CPU worker evidence" in evidence.output


@pytest.mark.parametrize("command", ["doctor", "explain-config", "check-config"])
def test_human_configuration_commands_accept_shipped_config(command):
    result = CliRunner().invoke(cli, [command, "-c", str(CONFIG)])
    assert result.exit_code == 0, result.output
    assert "default" in result.output or "配置检查通过" in result.output or "config:" in result.output


@pytest.mark.parametrize("command", ["doctor", "explain-config", "check-config"])
@pytest.mark.parametrize("kind", ["missing", "malformed", "invalid"])
def test_configuration_commands_report_unusable_config(tmp_path, command, kind):
    path = tmp_path / "invalid.yaml"
    if kind != "missing":
        path.write_text("products: [" if kind == "malformed" else "schema_version: 2\nproducts: {}\n", encoding="utf-8")

    result = CliRunner().invoke(cli, [command, "-c", str(path)])

    assert result.exit_code == 1
    assert result.output.strip()
    assert "Traceback" not in result.output


@pytest.mark.parametrize("kind", ["missing", "malformed"])
def test_parse_config_failure_does_not_create_task(tmp_path, kind):
    path = tmp_path / "bad.yaml"
    if kind == "malformed":
        path.write_text("products: [", encoding="utf-8")
    output = tmp_path / "tasks"

    result = CliRunner().invoke(cli, ["parse", str(SOURCE), "-c", str(path), "-o", str(output)])

    assert result.exit_code == 1
    assert "配置文件不存在" in result.output or "配置加载失败" in result.output
    assert not (output / SOURCE.stem).exists()


def test_parse_corrupt_archive_reports_failed_manifest(tmp_path):
    source = tmp_path / "broken.zip"
    source.write_bytes(b"not a zip archive")
    output = tmp_path / "tasks"

    result = CliRunner().invoke(cli, ["parse", str(source), "-c", str(CONFIG), "-o", str(output)])

    assert result.exit_code == 1
    assert "解析失败" in result.output
    assert "失败清单" in result.output
    manifest = json.loads((output / "broken/parse_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert any(item["code"] == "LP_EXTRACTION_FAILED" for item in manifest["diagnostics"])


def test_verbose_parse_prints_summary_and_dfx(tmp_path, monkeypatch):
    # Do not let the command replace pytest's process-wide logging handlers.
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: None)
    result = CliRunner().invoke(cli, [
        "parse", str(SOURCE), "-c", str(CONFIG), "-o", str(tmp_path),
        "--verbose", "--lifecycle-dfx", "full",
    ])

    assert result.exit_code == 0, result.output
    assert "pipeline.extract [OK]" in result.output
    assert "SERVICE-12345" in result.output
    assert "任务清单" in result.output


def test_human_artifact_check_and_dfx_report(parsed_task, tmp_path):
    output, task_id = parsed_task
    task = tmp_path / "task"
    shutil.copytree(output / task_id, task)
    checked = CliRunner().invoke(cli, ["artifact-check", str(task)])
    dfx = CliRunner().invoke(cli, ["dfx-output", str(task)])

    assert checked.exit_code == dfx.exit_code == 0
    assert "✓ artifact-check:" in checked.output
    assert "dfx_report:" in dfx.output
    assert (task / "dfx_report.json").is_file()

    (task / "result.json").unlink()
    broken = CliRunner().invoke(cli, ["artifact-check", str(task)])
    assert broken.exit_code == 1
    assert "✗ artifact-check:" in broken.output
    assert "LP_" in broken.output


def test_dfx_invalid_targets_are_a_readable_failure(parsed_task, tmp_path):
    output, task_id = parsed_task
    path = tmp_path / "bad-targets.json"
    path.write_text("not json", encoding="utf-8")

    result = CliRunner().invoke(cli, [
        "dfx-output", str(output / task_id), "--targets-json", str(path),
    ])

    assert result.exit_code == 1
    assert "dfx-output failed:" in result.output


def test_product_onboarding_is_available_from_root_cli(tmp_path):
    source = tmp_path / "device.log"
    source.write_text("2026-01-03 00:00:00 MODULE Slot=1; CPU-Id=1; ProcessName=worker; Context=ready; No[7]\n", encoding="utf-8")
    result = CliRunner().invoke(cli, ["product-onboarding", "analyze", "--input", str(source)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["operation"] == "analyze"
    assert payload["contract"] == "logparse.product_onboarding.report"


def test_scaffold_rejects_invalid_names_and_existing_files(tmp_path):
    runner = CliRunner()
    invalid = runner.invoke(cli, ["scaffold-extension", "--kind", "product", "--name", "../escape", "--root", str(tmp_path)])
    assert invalid.exit_code == 2
    assert "snake_case" in invalid.output
    args = ["scaffold-extension", "--kind", "product", "--name", "sample", "--root", str(tmp_path)]
    assert runner.invoke(cli, args).exit_code == 0
    conflict = runner.invoke(cli, args)
    assert conflict.exit_code == 1
    assert "文件已存在" in conflict.output


@pytest.mark.parametrize(
    ("mode", "change", "line", "expected"),
    [
        ("diag", "missing", "irrelevant", "diag_pattern 未配置"),
        ("diag", "unchanged", "irrelevant", "不匹配 diag_pattern"),
        ("journal", "missing", "irrelevant", "均未配置"),
        ("journal", "unchanged", "irrelevant", "不匹配 journal"),
        ("journal", "substring", "2026-01-03T00:01:00 host SERVICE-12: No[1] EXAMPLE ignored", "未命中"),
    ],
)
def test_pattern_explains_why_log_is_rejected(tmp_path, mode, change, line, expected):
    raw = load_config_file(CONFIG)
    module = raw["products"]["default"]["mechanisms"]["module1"]["config"]
    if change == "missing":
        module["diag_pattern" if mode == "diag" else "journal"] = "" if mode == "diag" else {}
    if change == "substring":
        module["journal"]["line_pattern2_required_substrings"] = ["REQUIRED_MARKER"]
    path = tmp_path / "patterns.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    result = CliRunner().invoke(cli, ["test-pattern", "-c", str(path), "-m", "module1", "-t", mode, line])

    assert result.exit_code == 1, result.output
    assert expected in result.output


def test_pattern_reports_unconfigured_module(tmp_path):
    result = CliRunner().invoke(cli, [
        "test-pattern", "-c", str(tmp_path / "missing.yaml"), "-m", "unknown", "-t", "diag", "line",
    ])
    assert result.exit_code == 1
    assert "模块 'unknown' 未配置" in result.output
