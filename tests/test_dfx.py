from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

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


def test_dfx_output_deep_writes_bounded_context_without_summary_leak(tmp_path: Path) -> None:
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
    window_text = Path(windows[0]["path"]).read_text(encoding="utf-8")
    assert "SECRET RAW LOG" in window_text


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
            "\n".join(f"line {i} SECRET RAW LOG" for i in range(60)),
            encoding="utf-8",
        )
    return task_dir
