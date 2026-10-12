from __future__ import annotations

from scripts import change_gate, rule_preflight


def test_rule_preflight_maps_lifecycle_paths_to_cpu0_board_contract():
    rules = rule_preflight.select_rules_for_paths([
        "backend/parsing/lifecycle_splitter_v3.py",
    ])

    rendered = rule_preflight.render_rules(rules)

    assert "rules:cpu-id-board" in rendered
    assert "CPU_Id=0 is board-level" in rendered
    assert "docs/rules/index.md" in rendered
    assert "governance:yellow" in rendered


def test_rule_preflight_changed_uses_git_paths(monkeypatch):
    outputs = iter([
        b"backend/query.py\0backend/result_serializer.py\0",
        b"old/path.py\0cli.py\0",
        "backend/config_validation.py\0新文件 目录/规则.py\0".encode(),
    ])

    class Completed:
        returncode = 0
        stderr = b""

        def __init__(self):
            self.stdout = next(outputs)

    def fake_run(*_args, **_kwargs):
        return Completed()

    monkeypatch.setattr(change_gate.subprocess, "run", fake_run)

    changed = rule_preflight.changed_paths()
    rules = rule_preflight.select_rules_for_paths(changed)
    rendered = rule_preflight.render_rules(rules)

    assert changed == [
        "backend/query.py",
        "backend/result_serializer.py",
        "old/path.py",
        "cli.py",
        "backend/config_validation.py",
        "新文件 目录/规则.py",
    ]
    assert "rules:lifecycle-v3-config" in rendered
    assert "Module1Plugin always uses LifecycleSplitterV3" in rendered
    assert "rules:nested-cycle-output" in rendered
    assert "rules:compact-result-contract" in rendered


def test_rule_preflight_changed_allows_clean_tree(monkeypatch, capsys):
    class Completed:
        stdout = b""
        returncode = 0
        stderr = b""

    def fake_run(*_args, **_kwargs):
        return Completed()

    monkeypatch.setattr(change_gate.subprocess, "run", fake_run)

    assert rule_preflight.main(["--changed"]) == 0
    output = capsys.readouterr().out
    assert "No matching repo-specific rules" in output


def test_rule_preflight_does_not_treat_git_failure_as_clean_tree(monkeypatch, capsys):
    def fail():
        raise RuntimeError("git failed")

    monkeypatch.setattr(rule_preflight, "changed_paths", fail)

    assert rule_preflight.main(["--changed"]) == 2
    captured = capsys.readouterr()
    assert "git failed" in captured.err
    assert "No matching" not in captured.out


def test_rule_preflight_reads_green_product_boundary():
    rules = rule_preflight.select_rules_for_paths([
        "backend/extensions/products/current/topology.py",
    ])

    rendered = rule_preflight.render_rules(rules)

    assert "governance:green" in rendered
    assert "Add focused tests" in rendered


def test_rule_preflight_reads_red_default_boundary():
    rules = rule_preflight.select_rules_for_paths(["new/unclassified/core.py"])

    rendered = rule_preflight.render_rules(rules)

    assert "governance:red" in rendered
    assert "accepted ADR" in rendered


def test_rule_preflight_preserves_hidden_skill_path():
    rules = rule_preflight.select_rules_for_paths([
        ".agents/skills/logparse-diagnose/SKILL.md",
    ])

    rendered = rule_preflight.render_rules(rules)

    assert "rules:nested-cycle-output" in rendered
    assert "governance:yellow" in rendered


def test_rule_preflight_maps_new_lifecycle_and_mechanism_paths():
    rules = rule_preflight.select_rules_for_paths([
        "backend/domain/lifecycle/splitter_v3.py",
        "backend/extensions/mechanisms/module2.py",
    ])

    rendered = rule_preflight.render_rules(rules)

    assert "rules:cpu-id-board" in rendered
    assert "rules:lifecycle-v3-config" in rendered
    assert "rules:lifecycle-v3-output" in rendered
    assert "rules:module2-upstream-lifecycle" in rendered
    assert "governance:yellow" in rendered


def test_rule_preflight_maps_new_product_artifact_and_scanner_paths():
    rules = rule_preflight.select_rules_for_paths([
        "backend/extensions/products/current/artifacts.py",
        "backend/extensions/products/current/scanner.py",
    ])

    rendered = rule_preflight.render_rules(rules)

    assert "rules:nested-cycle-output" in rendered
    assert "rules:artifact-contract" in rendered
    assert "rules:scanner-decompression-boundary" in rendered
    assert "governance:green" in rendered


def test_rule_preflight_maps_new_artifact_and_dfx_architecture_paths():
    rules = rule_preflight.select_rules_for_paths([
        "backend/infrastructure/artifact_repository.py",
        "backend/dfx.py",
    ])

    rendered = rule_preflight.render_rules(rules)

    assert "rules:artifact-contract" in rendered
    assert "rules:deterministic-dfx-boundary" in rendered
    assert "governance:red" in rendered


def test_rule_preflight_maps_product_result_projection_contract():
    rules = rule_preflight.select_rules_for_paths([
        "backend/extensions/products/current/result_serializer.py",
        "backend/extensions/products/current/metadata.py",
    ])

    rendered = rule_preflight.render_rules(rules)

    assert "rules:compact-result-contract" in rendered
    assert "rules:artifact-contract" in rendered
    assert "governance:red" in rendered
