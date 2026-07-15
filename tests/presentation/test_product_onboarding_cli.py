from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from backend.presentation.cli.composition import build_product_onboarding_application
from backend.presentation.cli.product_onboarding import run_product_onboarding


def _line(timestamp: str, *, context: str = "ready") -> str:
    return f"{timestamp} MODULE Slot=1; CPU-Id=1; ProcessName=worker; Context={context}; No[7]"


def _candidate(path: Path, *, file_pattern: str = "device_*.log") -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "adapter": "current_module1",
                "file_patterns": [file_pattern],
                "timestamp_regex": (r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})()"),
                "module_name": "MODULE",
                "diag_pattern": (
                    r"Slot=(?P<Slot>[^;]+);\s*CPU-Id=(?P<CPU_Id>[^;]*);\s*"
                    r"ProcessName=(?P<ProcessName>[^;]+);\s*"
                    r"Context=(?P<Context>[^;]+)"
                ),
                "sequence_pattern": r"No\[(\d+)\]",
            }
        ),
        encoding="utf-8",
    )


def test_analyze_emits_one_json_without_raw_content_or_absolute_paths(
    tmp_path: Path,
    capsys,
) -> None:
    secret = "PRIVATE_LOG_BODY"
    log = tmp_path / "device_001.log"
    log.write_text(
        _line("2026-01-03 00:00:00", context=secret) + "\n",
        encoding="utf-8",
    )

    code = run_product_onboarding(
        ["analyze", "--input", str(log)],
        service_factory=build_product_onboarding_application,
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert payload["contract"] == "logparse.product_onboarding.report"
    assert payload["operation"] == "analyze"
    assert secret not in captured.out
    assert str(tmp_path) not in captured.out


def test_validate_then_build_draft_uses_codes_zero_and_four(
    tmp_path: Path,
    capsys,
) -> None:
    log = tmp_path / "device_001.log"
    log.write_text(_line("2026-01-03 00:00:00") + "\n", encoding="utf-8")
    candidate = tmp_path / "candidate.json"
    _candidate(candidate)
    common = ["--input", str(log), "--candidate", str(candidate)]

    validate_code = run_product_onboarding(
        ["validate", *common],
        service_factory=build_product_onboarding_application,
    )
    validation_streams = capsys.readouterr()
    build_code = run_product_onboarding(
        ["build-draft", *common],
        service_factory=build_product_onboarding_application,
    )
    draft_streams = capsys.readouterr()
    validation = json.loads(validation_streams.out)
    draft = json.loads(draft_streams.out)

    assert validate_code == 0
    assert validation["operation"] == "validate"
    assert validation["final_config_ready"] is False
    assert build_code == 4
    assert draft["operation"] == "build-draft"
    assert draft["status"] == "needs_policy_confirmation"
    assert draft["must_not_persist"] is True
    assert "lifecycle_split" in draft["unresolved"]


def test_candidate_document_error_is_one_safe_stderr_json(
    tmp_path: Path,
    capsys,
) -> None:
    secret = "PRIVATE_CANDIDATE_BODY"
    log = tmp_path / "device.log"
    log.write_text("line\n", encoding="utf-8")
    candidate = tmp_path / "candidate.json"
    candidate.write_text(secret, encoding="utf-8")

    code = run_product_onboarding(
        [
            "validate",
            "--input",
            str(log),
            "--candidate",
            str(candidate),
        ],
        service_factory=build_product_onboarding_application,
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.err)

    assert code == 2
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert payload["status"] == "error"
    assert payload["diagnostics"][0]["code"] == ("LP_ONBOARD_CANDIDATE_DOCUMENT_INVALID")
    assert secret not in captured.err
    assert str(tmp_path) not in captured.err


def test_usage_error_is_one_json_and_exit_two(capsys) -> None:
    code = run_product_onboarding(
        ["validate", "--input", "sample.log"],
        service_factory=build_product_onboarding_application,
    )
    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert json.loads(captured.err)["diagnostics"][0]["code"] == ("LP_ONBOARD_CLI_USAGE_INVALID")


def test_unknown_operation_never_echoes_a_path(tmp_path: Path, capsys) -> None:
    private_path = tmp_path / "PRIVATE_OPERATION.log"

    code = run_product_onboarding(
        [str(private_path)],
        service_factory=build_product_onboarding_application,
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.err)

    assert code == 2
    assert captured.out == ""
    assert payload["operation"] == "unknown"
    assert str(private_path) not in captured.err


def test_unexpected_failure_is_redacted_and_exit_three(capsys) -> None:
    def broken_service():
        raise RuntimeError("PRIVATE_INTERNAL_DETAIL")

    code = run_product_onboarding(
        ["analyze", "--input", "sample.log"],
        service_factory=broken_service,
    )
    captured = capsys.readouterr()

    assert code == 3
    assert "PRIVATE_INTERNAL_DETAIL" not in captured.err
    assert json.loads(captured.err)["diagnostics"][0]["code"] == ("LP_ONBOARD_INTERNAL_ERROR")


def test_real_root_cli_registers_product_onboarding_without_writes(
    tmp_path: Path,
) -> None:
    log = tmp_path / "device_001.log"
    log.write_text(_line("2026-01-03 00:00:00") + "\n", encoding="utf-8")
    candidate = tmp_path / "candidate.json"
    _candidate(candidate)
    repository_root = Path(__file__).resolve().parents[2]
    before = sorted(path.name for path in tmp_path.iterdir())
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    cases = (
        ("analyze", [], 0),
        ("validate", ["--candidate", str(candidate)], 0),
        ("build-draft", ["--candidate", str(candidate)], 4),
    )
    for operation, extra_arguments, expected_code in cases:
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                "cli.py",
                "product-onboarding",
                operation,
                "--input",
                str(log),
                *extra_arguments,
            ],
            cwd=repository_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
            env=environment,
        )
        payload = json.loads(completed.stdout)

        assert completed.returncode == expected_code
        assert completed.stderr == ""
        assert completed.stdout.count("\n") == 1
        assert payload["operation"] == operation
    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_root_help_lists_product_onboarding_operations() -> None:
    repository_root = Path(__file__).resolve().parents[2]

    completed = subprocess.run(
        [sys.executable, "-B", "cli.py", "product-onboarding", "--help"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0
    assert {"analyze", "validate", "build-draft"}.issubset(set(completed.stdout.split()))
