from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from backend.application.configuration import load_config_file
from backend.contracts.runtime import ParseRequest, ParseRuntimeOptions
from backend.dfx import check_task_artifacts
from backend.presentation.cli.composition import build_parse_application
from cli import cli


ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    ("product", "source"),
    [
        ("default", ROOT / "tests/mock_data/diagnostic_information_20260103.zip"),
        ("compact", ROOT / "tests/mock_data_compact/compact_package_20260103.zip"),
    ],
)
def test_parse_service_artifact_query_and_dfx_closed_loop(
    tmp_path: Path,
    product: str,
    source: Path,
) -> None:
    application = build_parse_application(load_config_file(ROOT / "config.yaml"))
    run = application.service.run(
        ParseRequest(
            source=source,
            output_root=tmp_path,
            product=product,
            options=ParseRuntimeOptions(),
        )
    )
    task_dir = tmp_path / run.result.task_id

    assert check_task_artifacts(task_dir)["ok"] is True
    assert not (task_dir / "extracted").exists()
    assert (task_dir / "mech_modules").is_dir()
    assert {item.name for item in run.artifacts} >= {
        "parse_manifest",
        "metadata",
        "result",
        "mech_modules",
    }
    assert _golden_snapshot(task_dir) == json.loads(
        (ROOT / "tests" / "golden" / f"{product}.json").read_text(
            encoding="utf-8"
        )
    )
    result_payload = json.loads((task_dir / "result.json").read_text(encoding="utf-8"))
    serialized = json.dumps(result_payload, ensure_ascii=False).lower()
    for forbidden in ('"raw"', '"context"', '"logs"', '"extracted_root"'):
        assert forbidden not in serialized

    dfx = CliRunner().invoke(cli, ["dfx-output", str(task_dir)])
    assert dfx.exit_code == 0, dfx.output
    assert (task_dir / "dfx_report.json").is_file()
    assert (task_dir / "dfx_summary.txt").read_text(encoding="utf-8").count("\n") <= 1


def test_default_mock_target_resolution_cli_contract(tmp_path: Path) -> None:
    source = ROOT / "tests/mock_data/diagnostic_information_20260103.zip"
    parse_result = CliRunner().invoke(
        cli,
        [
            "parse",
            str(source),
            "-c",
            str(ROOT / "config.yaml"),
            "-o",
            str(tmp_path),
        ],
    )
    assert parse_result.exit_code == 0, parse_result.output

    target = CliRunner().invoke(
        cli,
        [
            "mech-target-logs",
            source.stem,
            "-o",
            str(tmp_path),
            "--problem-time",
            "2026-01-03T00:01:30",
            "--module",
            "module1",
            "--slot",
            "1",
            "--process-name",
            "SERVICE",
            "--pid",
            "12345",
            "--explain",
        ],
    )

    assert target.exit_code == 0, target.output
    payload = json.loads(target.output)
    assert payload["target_logs"][0]["match_status"] == "exact"
    assert payload["target_logs"][0]["error_code"] == "LP_TARGET_OK"


def test_repeated_parse_is_deterministic_except_created_time(tmp_path: Path) -> None:
    source = ROOT / "tests/mock_data/diagnostic_information_20260103.zip"
    raw = load_config_file(ROOT / "config.yaml")
    task_dirs: list[Path] = []
    for name in ("first", "second"):
        output_root = tmp_path / name
        application = build_parse_application(raw)
        run = application.service.run(
            ParseRequest(source=source, output_root=output_root, product="default")
        )
        task_dirs.append(output_root / run.result.task_id)

    for filename in ("metadata.json", "result.json"):
        payloads = [
            json.loads((task_dir / filename).read_text(encoding="utf-8"))
            for task_dir in task_dirs
        ]
        for payload in payloads:
            payload.pop("created_at", None)
        assert payloads[0] == payloads[1]

    evidence = []
    for task_dir in task_dirs:
        root = task_dir / "mech_modules"
        evidence.append(
            {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*.log")
            }
        )
    assert evidence[0] == evidence[1]


def test_keep_workspace_is_explicit_and_recorded_in_manifest(tmp_path: Path) -> None:
    source = ROOT / "tests/mock_data_compact/compact_package_20260103.zip"
    application = build_parse_application(load_config_file(ROOT / "config.yaml"))
    run = application.service.run(
        ParseRequest(
            source=source,
            output_root=tmp_path,
            product="compact",
            options=ParseRuntimeOptions(keep_workspace=True),
        )
    )
    task_dir = tmp_path / run.result.task_id
    manifest = json.loads(
        (task_dir / "parse_manifest.json").read_text(encoding="utf-8")
    )

    assert run.workspace == task_dir / "extracted"
    assert run.workspace.is_dir()
    assert manifest["workspace"] == {
        "retained": True,
        "path": str(task_dir / "extracted"),
    }


def _golden_snapshot(task_dir: Path) -> dict:
    metadata = json.loads((task_dir / "metadata.json").read_text(encoding="utf-8"))
    result = json.loads((task_dir / "result.json").read_text(encoding="utf-8"))
    modules = []
    for item in result["mech_results"]:
        modules.append(
            {
                "module_key": item["module_key"],
                "module_name": item["module_name"],
                "diag_entry_count": item["diag_entry_count"],
                "journal_entry_count": item["journal_entry_count"],
                "scope_count": len(item["slots"]),
                "cycle_count": sum(
                    len(scope["board_cycles"]) for scope in item["slots"]
                ),
                "process_count": sum(
                    len(cycle["processes"])
                    + sum(
                        len(child["processes"])
                        for child in cycle.get("cpu_cycles", [])
                    )
                    for scope in item["slots"]
                    for cycle in scope["board_cycles"]
                ),
            }
        )
    evidence_root = task_dir / "mech_modules"
    return {
        "coverage": metadata["coverage"],
        "modules": modules,
        "evidence": [
            path.relative_to(evidence_root).as_posix()
            for path in sorted(evidence_root.rglob("*.log"))
        ],
    }
