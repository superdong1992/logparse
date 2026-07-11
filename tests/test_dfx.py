from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from backend.infrastructure.artifact_layout import ArtifactLayout
from backend.infrastructure.artifact_repository import ArtifactRepository
from backend.dfx import check_task_artifacts
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


def _write_result(tmp_path: Path, *, with_log: bool) -> Path:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "metadata.json").write_text(
        json.dumps({"diagnostic_slots": [], "private_slots": [], "errors": []}),
        encoding="utf-8",
    )
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
    return task_dir
