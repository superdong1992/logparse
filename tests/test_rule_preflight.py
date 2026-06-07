from __future__ import annotations

from scripts import rule_preflight


def test_rule_preflight_maps_lifecycle_paths_to_cpu0_board_contract():
    rules = rule_preflight.select_rules_for_paths([
        "backend/parsing/lifecycle_splitter_v3.py",
    ])

    rendered = rule_preflight.render_rules(rules)

    assert "rules:cpu-id-board" in rendered
    assert "CPU_Id=0 is board-level" in rendered
    assert "docs/rules/index.md" in rendered


def test_rule_preflight_changed_uses_git_paths(monkeypatch):
    class Completed:
        stdout = "\n".join([
            " M backend/query.py",
            "M  backend/result_serializer.py",
            "?? backend/config_validation.py",
            "R  old/path.py -> cli.py",
            "",
        ])

    def fake_run(*_args, **_kwargs):
        return Completed()

    monkeypatch.setattr(rule_preflight.subprocess, "run", fake_run)

    changed = rule_preflight.changed_paths()
    rules = rule_preflight.select_rules_for_paths(changed)
    rendered = rule_preflight.render_rules(rules)

    assert changed == [
        "backend/query.py",
        "backend/result_serializer.py",
        "backend/config_validation.py",
        "cli.py",
    ]
    assert "rules:lifecycle-v3-config" in rendered
    assert "Module1Plugin always uses LifecycleSplitterV3" in rendered
    assert "rules:nested-cycle-output" in rendered
    assert "rules:compact-result-contract" in rendered


def test_rule_preflight_changed_allows_clean_tree(monkeypatch, capsys):
    class Completed:
        stdout = ""

    def fake_run(*_args, **_kwargs):
        return Completed()

    monkeypatch.setattr(rule_preflight.subprocess, "run", fake_run)

    assert rule_preflight.main(["--changed"]) == 0
    output = capsys.readouterr().out
    assert "No matching repo-specific rules" in output
