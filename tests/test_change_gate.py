from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import yaml

from scripts import change_gate


def _record(*, zones: list[str], paths: list[str]) -> dict:
    return {
        "schema_version": 1,
        "change_id": "2026-07-11-test",
        "summary": "exercise the governance gate",
        "zones": zones,
        "paths": paths,
        "validation": {
            "tests": ["pytest focused-tests"],
            "lan_scenario": "synthetic LAN scenario",
            "contract_tests": ["pytest contract-tests"],
            "security_tests": ["pytest security-tests"],
            "smoke_tests": ["parse smoke package"],
        },
        "evidence": {
            "real_case_id": "LAN-CASE-1",
            "fixture": "tests/fixtures/case-1",
            "corpus_regression": "passed historical corpus",
        },
        "compatibility": {"schema_impact": "none"},
        "architecture": {
            "adr": "docs/adr/0002-architecture-boundaries.md",
            "approval": "owner accepted",
            "rollback": "revert the change",
        },
    }


def test_boundary_classifies_product_topology_as_green():
    config = change_gate.load_boundary_config()

    change = change_gate.classify_path(
        "backend/extensions/products/current/topology.py", config
    )

    assert change is not None
    assert change.zone.name == "green"


def test_boundary_uses_protected_policy_precedence_over_green_tests():
    config = change_gate.load_boundary_config()

    change = change_gate.classify_path(
        "tests/extensions/mechanisms/test_correlation.py", config
    )

    assert change is not None
    assert change.zone.name == "yellow"


def test_plugin_base_is_frozen_architecture_even_inside_mechanism_tree():
    config = change_gate.load_boundary_config()

    change = change_gate.classify_path(
        "backend/extensions/mechanisms/base.py", config
    )

    assert change is not None
    assert change.zone.name == "red"


def test_compatibility_pipeline_is_red_even_inside_product_tree():
    config = change_gate.load_boundary_config()

    change = change_gate.classify_path(
        "backend/extensions/products/current/pipeline.py", config
    )

    assert change is not None
    assert change.zone.name == "red"


def test_artifact_and_query_contracts_stay_red_inside_product_tree():
    config = change_gate.load_boundary_config()

    changes = change_gate.classify_paths(
        [
            "backend/extensions/products/current/query.py",
            "backend/extensions/products/current/dfx.py",
            "backend/extensions/products/current/artifacts.py",
            "backend/extensions/products/current/engine.py",
            "backend/extensions/products/current/metadata.py",
            "backend/extensions/products/current/result_serializer.py",
        ],
        config,
    )

    assert {change.zone.name for change in changes} == {"red"}


def test_delivery_verifier_and_coverage_config_are_frozen_red():
    config = change_gate.load_boundary_config()

    changes = change_gate.classify_paths(
        ["scripts/verify_delivery.py", "pyproject.toml"], config
    )

    assert {change.zone.name for change in changes} == {"red"}


def test_boundary_defaults_unclassified_source_to_red_and_ignores_outputs():
    config = change_gate.load_boundary_config()

    defaulted = change_gate.classify_path("unknown/new_core.py", config)

    assert defaulted is not None
    assert defaulted.zone.name == "red"
    assert defaulted.matched_pattern is None
    assert change_gate.classify_path("outputs/private-log/archive.txt", config) is None


def test_boundary_preserves_hidden_directory_names():
    config = change_gate.load_boundary_config()

    diagnose = change_gate.classify_path(
        ".agents/skills/logparse-diagnose/SKILL.md", config
    )
    develop = change_gate.classify_path(
        ".agents/skills/logparse-develop/SKILL.md", config
    )

    assert diagnose is not None and diagnose.zone.name == "yellow"
    assert develop is not None and develop.zone.name == "red"


def test_guardrails_cannot_be_downgraded_by_boundary_patterns():
    config = change_gate.load_boundary_config()
    malicious_zones = dict(config.zones)
    malicious_zones["green"] = replace(
        config.zones["green"], paths=("governance/**",)
    )
    malicious_zones["red"] = replace(config.zones["red"], paths=())
    malicious_config = replace(config, zones=malicious_zones)

    change = change_gate.classify_path(
        "governance/architecture-boundaries.toml", malicious_config
    )

    assert change is not None
    assert change.zone.name == "red"
    assert change.matched_pattern == "governance/**"


def test_green_change_record_requires_tests_and_lan_scenario():
    config = change_gate.load_boundary_config()
    changes = change_gate.classify_paths(
        ["backend/extensions/products/current/topology.py"], config
    )
    record = _record(
        zones=["green"], paths=["backend/extensions/products/current/**"]
    )

    assert change_gate.validate_change_record(record, changes) == []

    record["validation"]["lan_scenario"] = ""
    assert "validation.lan_scenario is required" in change_gate.validate_change_record(
        record, changes
    )

    record["validation"]["lan_scenario"] = "TBD"
    assert "validation.lan_scenario is required" in change_gate.validate_change_record(
        record, changes
    )


def test_change_record_rejects_unknown_zone_name():
    config = change_gate.load_boundary_config()
    changes = change_gate.classify_paths(
        ["backend/extensions/products/current/topology.py"], config
    )
    record = _record(
        zones=["green", "purple"], paths=["backend/extensions/products/current/**"]
    )

    errors = change_gate.validate_change_record(record, changes)

    assert "zones contains unknown values: purple" in errors


def test_change_record_rejects_template_change_id():
    config = change_gate.load_boundary_config()
    changes = change_gate.classify_paths(
        ["backend/extensions/products/current/topology.py"], config
    )
    record = _record(
        zones=["green"], paths=["backend/extensions/products/current/**"]
    )
    record["change_id"] = "YYYY-MM-DD-short-name"

    errors = change_gate.validate_change_record(record, changes)

    assert "change_id must use YYYY-MM-DD-lowercase-slug" in errors


def test_yellow_change_record_requires_real_evidence():
    config = change_gate.load_boundary_config()
    changes = change_gate.classify_paths(
        ["backend/domain/lifecycle/policy.py"], config
    )
    record = _record(zones=["yellow"], paths=["backend/domain/lifecycle/**"])
    record["evidence"]["real_case_id"] = ""

    errors = change_gate.validate_change_record(record, changes)

    assert "evidence.real_case_id is required" in errors


def test_red_change_record_requires_adr_approval_and_full_validation():
    config = change_gate.load_boundary_config()
    changes = change_gate.classify_paths(["backend/application/service.py"], config)
    record = _record(zones=["red"], paths=["backend/application/**"])
    record["architecture"]["approval"] = ""
    record["validation"]["security_tests"] = []

    errors = change_gate.validate_change_record(record, changes)

    assert "architecture.approval is required" in errors
    assert "validation.security_tests is required" in errors


def test_red_change_record_rejects_missing_accepted_adr():
    config = change_gate.load_boundary_config()
    changes = change_gate.classify_paths(["backend/application/service.py"], config)
    record = _record(zones=["red"], paths=["backend/application/**"])
    record["architecture"]["adr"] = "docs/adr/missing.md"

    errors = change_gate.validate_change_record(record, changes)

    assert "architecture.adr does not exist: docs/adr/missing.md" in errors


def test_main_enforces_completed_record(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(change_gate, "CHANGE_RECORD_ROOT", tmp_path)
    record_path = tmp_path / "test-completed-record.yaml"
    payload = _record(
        zones=["green"], paths=["backend/extensions/products/current/**"]
    )
    record_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    exit_code = change_gate.main([
        "--paths",
        "backend/extensions/products/current/topology.py",
        "--enforce",
        "--change-record",
        str(record_path),
    ])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "ALLOW" in output.out
    assert output.err == ""


def test_main_reports_git_range_failure(monkeypatch, capsys):
    def fail_range(_base: str, _head: str):
        raise RuntimeError("unknown revision")

    monkeypatch.setattr(change_gate, "commit_range_paths", fail_range)

    exit_code = change_gate.main(["--base", "missing", "--head", "HEAD"])

    output = capsys.readouterr()
    assert exit_code == 2
    assert "change gate git error: unknown revision" in output.err


def test_changed_paths_combines_tracked_staged_and_untracked(monkeypatch):
    outputs = iter([
        b"backend/application/service.py\0",
        b"backend/contracts/runtime.py\0",
        b"backend/extensions/products/current/new.py\0",
    ])

    class Completed:
        returncode = 0
        stderr = b""

        def __init__(self, stdout: bytes):
            self.stdout = stdout

    def fake_run(_command, **_kwargs):
        return Completed(next(outputs))

    monkeypatch.setattr(change_gate.subprocess, "run", fake_run)

    assert change_gate.changed_paths() == [
        "backend/application/service.py",
        "backend/contracts/runtime.py",
        "backend/extensions/products/current/new.py",
    ]


def test_enforce_requires_change_record(capsys):
    exit_code = change_gate.main([
        "--paths",
        "backend/extensions/products/current/topology.py",
        "--enforce",
    ])

    output = capsys.readouterr()
    assert exit_code == 2
    assert "--enforce requires --change-record" in output.err


def test_commit_range_paths_uses_explicit_base_and_head(monkeypatch):
    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        stdout = b"backend/application/service.py\0cli.py\0"
        stderr = b""

    def fake_run(command, **_kwargs):
        calls.append(command)
        return Completed()

    monkeypatch.setattr(change_gate.subprocess, "run", fake_run)

    paths = change_gate.commit_range_paths("origin/main", "HEAD")

    assert paths == ["backend/application/service.py", "cli.py"]
    assert calls == [[
        "git",
        "diff",
        "--name-only",
        "-z",
        "--diff-filter=ACMRDTUXB",
        "origin/main",
        "HEAD",
    ]]
