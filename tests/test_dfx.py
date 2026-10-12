from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from backend.infrastructure.artifact_layout import ArtifactLayout
from backend.infrastructure.artifact_repository import ArtifactRepository
from backend.extensions.products.current.dfx import check_task_artifacts
from backend.utils import safe_log_filename, safe_path_segment
from cli import cli


def test_dfx_output_reports_missing_result_first(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"

    result = CliRunner().invoke(cli, ["dfx-output", str(task_dir)])

    assert result.exit_code == 0, result.output
    summary = (task_dir / "dfx_summary.txt").read_text(encoding="utf-8").strip()
    report = json.loads((task_dir / "dfx_report.json").read_text(encoding="utf-8"))
    assert summary.startswith("LP_RESULT_MISSING:")
    assert "\n" not in summary
    assert report["summary"] == summary
    assert not (task_dir / "dfx_context").exists()


def test_dfx_output_reports_target_log_missing_without_log_body(tmp_path: Path) -> None:
    task_dir = _write_result(tmp_path, with_log=False)
    targets = {
        "problem_time": "2026-01-03T00:05:00",
        "targets": [
            {
                "label": "client",
                "module": "EXAMPLE",
                "slot": "1",
                "process_name": "SERVICE",
                "pid": "123",
            }
        ],
    }

    result = CliRunner().invoke(
        cli,
        ["dfx-output", str(task_dir), "--targets-json", json.dumps(targets)],
    )

    assert result.exit_code == 0, result.output
    summary = (task_dir / "dfx_summary.txt").read_text(encoding="utf-8").strip()
    report_text = (task_dir / "dfx_report.json").read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert summary.startswith("LP_TARGET_LOG_MISSING:")
    assert "SECRET RAW LOG" not in summary
    assert "SECRET RAW LOG" not in report_text
    assert report["targets"][0]["target_log"]["error_code"] == "LP_TARGET_LOG_MISSING"


def test_dfx_output_deep_writes_bounded_context_without_summary_leak(
    tmp_path: Path,
) -> None:
    task_dir = _write_result(tmp_path, with_log=True)
    targets = {
        "problem_time": "2026-01-03T00:05:00",
        "targets": [
            {
                "module": "EXAMPLE",
                "slot": "1",
                "process_name": "SERVICE",
                "pid": "123",
            }
        ],
    }

    result = CliRunner().invoke(
        cli,
        ["dfx-output", str(task_dir), "--deep", "--targets-json", json.dumps(targets)],
    )

    assert result.exit_code == 0, result.output
    summary = (task_dir / "dfx_summary.txt").read_text(encoding="utf-8").strip()
    report = json.loads((task_dir / "dfx_report.json").read_text(encoding="utf-8"))
    windows = report["deep_context"]["files"]
    assert summary.startswith("LP_DFX_OK:")
    assert "SECRET RAW LOG" not in summary
    assert len(windows) == 1
    window_text = (task_dir / windows[0]["relative_path"]).read_text(
        encoding="utf-8"
    )
    assert "SECRET RAW LOG" in window_text
    assert windows[0]["line_count"] <= 48
    assert windows[0]["selection_reason"] == "nearest_problem_time"
    assert windows[0]["nearest_timestamp"] == "2026-01-03T00:05:00"
    context_manifest = json.loads(
        (task_dir / "dfx_context" / "manifest.json").read_text(encoding="utf-8")
    )
    assert context_manifest["windows"][0]["sha256"] == windows[0]["sha256"]
    assert context_manifest["windows"][0]["anchor"]["process_name"] == "SERVICE"
    assert "path" not in context_manifest["windows"][0]
    assert "source_path" not in context_manifest["windows"][0]
    assert context_manifest["total_bytes"] <= 80 * 1024


def test_dfx_output_reads_optional_performance_metrics(tmp_path: Path) -> None:
    task_dir = _write_result(tmp_path, with_log=True)
    (task_dir / "performance.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "total_seconds": 2.5,
                "config": {},
                "stages": [
                    {
                        "name": "diagnostic_scan.shared",
                        "elapsed_seconds": 2.0,
                        "metrics": {
                            "files": 3,
                            "lines": 99,
                            "module1_entries": 7,
                        },
                    }
                ],
                "stage_tree": {},
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(cli, ["dfx-output", str(task_dir)])

    assert result.exit_code == 0, result.output
    report = json.loads((task_dir / "dfx_report.json").read_text(encoding="utf-8"))
    assert report["performance"]["available"] is True
    assert report["performance"]["total_seconds"] == 2.5
    assert report["performance"]["observed_counters"] == {
        "files": 3,
        "lines": 99,
        "entries": 7,
        "errors": 0,
    }


def test_dfx_output_detects_manifest_integrity_mismatch(tmp_path: Path) -> None:
    task_dir = _write_result(tmp_path, with_log=True)
    repository = ArtifactRepository(ArtifactLayout.from_task_dir(task_dir))
    repository.refresh_manifest(product="default", status="success")
    with (task_dir / "result.json").open("a", encoding="utf-8") as stream:
        stream.write(" \n")

    result = CliRunner().invoke(cli, ["dfx-output", str(task_dir)])

    assert result.exit_code == 0, result.output
    report = json.loads((task_dir / "dfx_report.json").read_text(encoding="utf-8"))
    assert report["summary"].startswith("LP_ARTIFACT_INTEGRITY_FAILED:")
    assert report["manifest"]["integrity"]["ok"] is False


def test_dfx_output_deep_limits_windows_to_five(tmp_path: Path) -> None:
    task_dir = _write_result(tmp_path, with_log=True)
    target = {
        "module": "EXAMPLE",
        "slot": "1",
        "process_name": "SERVICE",
        "pid": "123",
    }
    targets = {
        "problem_time": "2026-01-03T00:05:00",
        "targets": [target] * 6,
    }

    result = CliRunner().invoke(
        cli,
        ["dfx-output", str(task_dir), "--deep", "--targets-json", json.dumps(targets)],
    )

    assert result.exit_code == 0, result.output
    manifest = json.loads(
        (task_dir / "dfx_context" / "manifest.json").read_text(encoding="utf-8")
    )
    assert len(manifest["windows"]) == 5
    assert all(window["line_count"] <= 48 for window in manifest["windows"])
    assert manifest["total_bytes"] <= 80 * 1024


def test_check_task_artifacts_validates_json_manifest_and_index_without_writes(
    tmp_path: Path,
) -> None:
    task_dir = _write_result(tmp_path, with_log=True)
    for name in ("metadata.json", "result.json"):
        path = task_dir / name
        data = json.loads(path.read_text(encoding="utf-8"))
        data["schema_version"] = 2
        path.write_text(json.dumps(data), encoding="utf-8")
    repository = ArtifactRepository(ArtifactLayout.from_task_dir(task_dir))
    repository.refresh_manifest(product="default", status="success")

    report = check_task_artifacts(task_dir)

    assert report["ok"] is True
    assert report["checks"]["manifest"]["integrity"]["ok"] is True
    assert report["checks"]["index_vs_files"] == {
        "ok": True,
        "status": "checked",
        "expected_process_log_count": 1,
        "claimed_file_count": 1,
        "actual_file_count": 1,
        "missing": [],
        "orphan_files": [],
    }
    assert not (task_dir / "dfx_report.json").exists()
    assert not (task_dir / "dfx_summary.txt").exists()


@pytest.mark.parametrize(
    ("name", "section", "invalid_code"),
    [
        ("result.json", "result", "LP_RESULT_INVALID"),
        ("metadata.json", "metadata", "LP_METADATA_INVALID"),
        ("parse_manifest.json", "manifest", "LP_MANIFEST_INVALID"),
    ],
)
@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"{broken", "invalid_json"),
        (b"\xff", "invalid_json"),
        (b"[]", "invalid_root"),
        (b"{}", "missing_schema"),
        (b'{"schema_version": 99}', "unsupported_schema"),
    ],
)
def test_dfx_and_artifact_check_report_the_same_invalid_artifact_reason(
    tmp_path: Path, name: str, section: str, invalid_code: str,
    content: bytes, reason: str,
) -> None:
    task_dir = _write_result(tmp_path, with_log=True)
    (task_dir / name).write_bytes(content)

    check = check_task_artifacts(task_dir)
    assert check["checks"][section]["reason"] == reason
    assert invalid_code in {item["code"] for item in check["issues"]}

    result = CliRunner().invoke(cli, ["dfx-output", str(task_dir)])

    assert result.exit_code == 0, result.output
    report = json.loads((task_dir / "dfx_report.json").read_text(encoding="utf-8"))
    assert report[section]["reason"] == reason
    issue = next(item for item in report["issues"] if item["code"] == invalid_code)
    assert issue["detail"]["reason"] == reason
    assert "Traceback" not in result.output


def test_dfx_requires_manifest_for_current_artifacts(tmp_path: Path) -> None:
    task_dir = _write_result(tmp_path, with_log=True)
    (task_dir / "parse_manifest.json").unlink()

    result = CliRunner().invoke(cli, ["dfx-output", str(task_dir)])

    assert result.exit_code == 0, result.output
    report = json.loads((task_dir / "dfx_report.json").read_text(encoding="utf-8"))
    assert report["summary"].startswith("LP_MANIFEST_MISSING:")
    assert report["manifest"] == {
        "available": False, "valid": False, "reason": "missing",
    }
    assert not any(item["code"] == "LP_DFX_OK" for item in report["issues"])


def test_dfx_distinguishes_unreadable_result_from_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_dir = _write_result(tmp_path, with_log=True)
    original_read = Path.read_text

    def deny_result(path, *args, **kwargs):
        if path == task_dir / "result.json":
            raise PermissionError("private details")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_result)
    result = CliRunner().invoke(cli, ["dfx-output", str(task_dir)])

    assert result.exit_code == 0, result.output
    report = json.loads((task_dir / "dfx_report.json").read_text(encoding="utf-8"))
    assert report["summary"].startswith("LP_RESULT_INVALID:")
    assert report["result"]["reason"] == "unreadable"
    assert "private details" not in json.dumps(report)


def test_default_dfx_hashes_evidence_contents_without_deep_context(tmp_path: Path) -> None:
    task_dir = _write_result(tmp_path, with_log=True)
    evidence = next((task_dir / "mech_modules").rglob("*.log"))
    original = evidence.read_bytes()
    evidence.write_bytes(original.replace(b"SECRET", b"CHANGE"))
    assert evidence.stat().st_size == len(original)

    result = CliRunner().invoke(cli, ["dfx-output", str(task_dir)])

    assert result.exit_code == 0, result.output
    report = json.loads((task_dir / "dfx_report.json").read_text(encoding="utf-8"))
    assert report["summary"].startswith("LP_ARTIFACT_INTEGRITY_FAILED:")
    assert any(
        item["artifact"] == "mech_modules" and item["code"] == "sha256_mismatch"
        for item in report["manifest"]["integrity"]["issues"]
    )
    assert not (task_dir / "dfx_context").exists()


@pytest.mark.parametrize(
    ("content", "reason", "query_code"),
    [
        ("{broken", "invalid_json", "LP_RESULT_INVALID"),
        ('{"mech_results": []}', "missing_schema", "LP_SCHEMA_UNSUPPORTED"),
        ('{"schema_version": 2, "mech_results": 7}', "invalid_shape", "LP_RESULT_INVALID"),
        ('{"schema_version": 2, "mech_results": [1]}', "invalid_shape", "LP_RESULT_INVALID"),
        (
            '{"schema_version": 2, "mech_results": [{"slots": "PRIVATE RAW LOG"}]}',
            "invalid_shape", "LP_RESULT_INVALID",
        ),
    ],
)
def test_invalid_result_is_reported_across_query_check_and_dfx_commands(
    tmp_path: Path, content: str, reason: str, query_code: str,
) -> None:
    task_dir = _write_result(tmp_path, with_log=True)
    (task_dir / "result.json").write_text(content, encoding="utf-8")
    runner = CliRunner()

    target = runner.invoke(cli, [
        "mech-target-logs", task_dir.name, "-o", str(tmp_path),
        "--problem-time", "2026-01-03T00:05:00", "--module", "module1",
        "--slot", "1", "--process-name", "SERVICE", "--explain",
    ])
    assert target.exit_code == 0, target.output
    assert json.loads(target.output)["target_logs"][0]["error_code"] == query_code

    check = runner.invoke(cli, ["artifact-check", str(task_dir), "--json"])
    assert check.exit_code == 1, check.output
    check_payload = json.loads(check.output)
    assert check_payload["checks"]["result"]["reason"] == reason
    assert "LP_RESULT_INVALID" in {item["code"] for item in check_payload["issues"]}

    dfx = runner.invoke(cli, ["dfx-output", str(task_dir)])
    assert dfx.exit_code == 0, dfx.output
    assert dfx.output.startswith("LP_RESULT_INVALID:")
    report = json.loads((task_dir / "dfx_report.json").read_text(encoding="utf-8"))
    assert report["result"]["reason"] == reason
    assert all("Traceback" not in result.output for result in (target, check, dfx))
    assert "PRIVATE RAW LOG" not in target.output + check.output + dfx.output
    assert "PRIVATE RAW LOG" not in json.dumps(report)


@pytest.mark.parametrize(
    "slots",
    [7, [1], [{"slot_id": "1", "role": "active", "diagnostic_logs": 7}]],
)
def test_malformed_metadata_has_same_failure_in_query_and_dfx(tmp_path, slots):
    task_dir = _write_result(tmp_path, with_log=True)
    (task_dir / "metadata.json").write_text(
        json.dumps({"schema_version": 2, "diagnostic_slots": slots}), encoding="utf-8",
    )
    ArtifactRepository.for_task_dir(task_dir).refresh_manifest(
        product="default", status="success",
    )
    runner = CliRunner()
    query = runner.invoke(cli, ["list-slots", task_dir.name, "-o", str(tmp_path)])
    assert query.exit_code == 1
    assert "metadata.json" in query.output
    assert "字段结构或类型无效" in query.output
    assert isinstance(query.exception, SystemExit)

    dfx = runner.invoke(cli, ["dfx-output", str(task_dir)])
    assert dfx.exit_code == 0, dfx.output
    assert dfx.output.startswith("LP_METADATA_INVALID:")
    report = json.loads((task_dir / "dfx_report.json").read_text(encoding="utf-8"))
    assert report["metadata"]["reason"] == "invalid_shape"
    check = check_task_artifacts(task_dir)
    assert check["checks"]["metadata"]["reason"] == "invalid_shape"


def _write_result(tmp_path: Path, *, with_log: bool) -> Path:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "metadata.json").write_text(
        json.dumps({
            "schema_version": 2,
            "diagnostic_slots": [],
            "private_slots": [],
            "errors": [],
        }),
        encoding="utf-8",
    )
    (task_dir / "result.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "mech_results": [
                    {
                        "module_key": "module1",
                        "module_name": "EXAMPLE",
                        "slots": [
                            {
                                "slot_id": "1",
                                "lifecycle_split_result": {
                                    "algorithm": "interval_v3",
                                    "candidate_segments": [],
                                    "merge_decisions": [],
                                    "lifecycles": [],
                                    "journal_evidence": [],
                                    "issues": [],
                                    "lifecycle_reliable": True,
                                },
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
                                            }
                                        ],
                                        "cpu_cycles": [],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if with_log:
        log_dir = (
            task_dir
            / "mech_modules"
            / safe_path_segment("EXAMPLE")
            / "slot_1"
            / safe_path_segment("cycle")
        )
        log_dir.mkdir(parents=True)
        (log_dir / safe_log_filename("SERVICE", "123")).write_text(
            "\n".join(
                f"2026-01-03T00:{i // 60:02d}:{i % 60:02d} line {i} SECRET RAW LOG"
                for i in range(600)
            ),
            encoding="utf-8",
        )
    ArtifactRepository.for_task_dir(task_dir).refresh_manifest(
        product="default", status="success",
    )
    return task_dir
