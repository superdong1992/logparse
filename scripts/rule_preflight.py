#!/usr/bin/env python3
"""List repository rules that must be read before touching selected files."""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from scripts.change_gate import classify_path, load_boundary_config
except ModuleNotFoundError:  # Direct execution: python scripts/rule_preflight.py
    from change_gate import classify_path, load_boundary_config


RULE_DOC = "docs/rules/index.md"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    doc: str
    summary: str
    checks: tuple[str, ...]
    globs: tuple[str, ...]


RULES: tuple[Rule, ...] = (
    Rule(
        rule_id="rules:cpu-id-board",
        doc=f"{RULE_DOC}#rulescpu-id-board",
        summary="CPU_Id=0 is board-level",
        checks=(
            "Treat CPU_Id=0 and empty cpu_id as board-level.",
            "Only non-zero CPU ids are nested CPU cycles.",
        ),
        globs=(
            "backend/parsing/*",
            "backend/domain/lifecycle/*",
            "backend/plugins/mechanisms/module*.py",
            "backend/extensions/mechanisms/module*.py",
            "backend/extensions/products/current/models.py",
            "backend/extensions/products/current/scopes.py",
            "backend/models.py",
            "tests/test_module*_plugin.py",
            "tests/generate_mock_data.py",
            "docs/lifecycle*",
        ),
    ),
    Rule(
        rule_id="rules:nested-cycle-output",
        doc=f"{RULE_DOC}#rulesnested-cycle-output",
        summary="Board cycles are top-level; CPU cycles are nested.",
        checks=(
            "Board logs stay under slot_<id>/<board_cycle>/.",
            "CPU logs stay under slot_<id>/<board_cycle>/cpu_<id>/<cpu_cycle>/.",
        ),
        globs=(
            "backend/models.py",
            "backend/extensions/products/current/models.py",
            "backend/extensions/products/current/scopes.py",
            "backend/extensions/products/current/artifacts.py",
            "backend/parsing/output_writer.py",
            "backend/result_serializer.py",
            "backend/query.py",
            "backend/metadata.py",
            "cli.py",
            "tests/test_output_writer.py",
            "tests/test_query.py",
            "tests/test_cli.py",
            ".agents/skills/logparse-diagnose/**",
        ),
    ),
    Rule(
        rule_id="rules:module2-upstream-lifecycle",
        doc=f"{RULE_DOC}#rulesmodule2-upstream-lifecycle",
        summary="module2 reuses module1 lifecycle cycles.",
        checks=(
            "Do not add independent lifecycle splitting to module2.",
            "Preserve slot, board cycle, and nested CPU-cycle matching.",
        ),
        globs=(
            "backend/plugins/mechanisms/module2.py",
            "backend/plugins/mechanisms/module1.py",
            "backend/extensions/mechanisms/module2.py",
            "backend/extensions/mechanisms/module1.py",
            "backend/domain/correlation/*",
            "backend/parsing/output_writer.py",
            "tests/test_module2_plugin.py",
            ".agents/skills/logparse-diagnose/**",
        ),
    ),
    Rule(
        rule_id="rules:compact-result-contract",
        doc=f"{RULE_DOC}#rulescompact-result-contract",
        summary="Compact result.json is a query index, not raw log storage.",
        checks=(
            "Keep raw per-line logs out of compact process summaries.",
            "New query fields must survive serializer -> query -> CLI.",
        ),
        globs=(
            "backend/result_serializer.py",
            "backend/extensions/products/*/result_serializer.py",
            "backend/query.py",
            "backend/metadata.py",
            "cli.py",
            "tests/test_query.py",
            "tests/test_cli.py",
        ),
    ),
    Rule(
        rule_id="rules:scanner-decompression-boundary",
        doc=f"{RULE_DOC}#rulesscanner-decompression-boundary",
        summary="Decompressor extracts; scanners only inspect extracted workspaces.",
        checks=(
            "Do not extract archives inside scanner plugins.",
            "Plain .gz logs are streamed unless debug expansion is enabled.",
        ),
        globs=(
            "backend/decompressor.py",
            "backend/pipeline.py",
            "backend/extensions/products/current/pipeline.py",
            "backend/application/parse_service.py",
            "backend/application/mechanism_execution.py",
            "backend/infrastructure/archive*.py",
            "backend/infrastructure/decompress*.py",
            "backend/infrastructure/workspace*.py",
            "backend/plugins/*/scanner.py",
            "backend/extensions/products/*/scanner.py",
            "backend/parsing/file_iter.py",
            "tests/test_decompressor.py",
            "tests/test_scanner_plugin.py",
            "tests/test_pipeline.py",
        ),
    ),
    Rule(
        rule_id="rules:lifecycle-v3-config",
        doc=f"{RULE_DOC}#ruleslifecycle-v3-config",
        summary="Module1Plugin always uses LifecycleSplitterV3.",
        checks=(
            "Current lifecycle_split supports process_name_mapping, reliable_processes, and multi_instance_processes only.",
            "Reliable and multi-instance process sets must be flat lists.",
            "Conflict checks use canonicalized, casefolded names.",
            "The final result algorithm is always interval_v3.",
        ),
        globs=(
            "backend/config_validation.py",
            "backend/extensions/mechanisms/validation.py",
            "backend/plugins/mechanisms/module1.py",
            "backend/extensions/mechanisms/module1.py",
            "backend/parsing/lifecycle_common.py",
            "backend/parsing/lifecycle_splitter_v3.py",
            "backend/domain/lifecycle/common.py",
            "backend/domain/lifecycle/splitter_v3.py",
            "config.yaml",
            "tests/test_config_validation.py",
            "tests/test_module1_plugin.py",
            "tests/test_lifecycle_splitter_v3.py",
            "docs/lifecycle-dfx-guide.md",
        ),
    ),
    Rule(
        rule_id="rules:lifecycle-v3-output",
        doc=f"{RULE_DOC}#ruleslifecycle-v3-output",
        summary="V3 lifecycle output is the only current query/CLI contract.",
        checks=(
            "V3 output contains candidate_segments, merge_decisions, lifecycles, journal_evidence, issues, and lifecycle_reliable.",
            "Lifecycle issues live under lifecycle_split_result.issues.",
            "mech-lifecycles --show-boundaries displays V3 DFX only.",
            "Legacy result files may be reported as unsupported.",
        ),
        globs=(
            "backend/parsing/lifecycle_splitter_v3.py",
            "backend/plugins/mechanisms/module1.py",
            "backend/domain/lifecycle/splitter_v3.py",
            "backend/extensions/mechanisms/module1.py",
            "backend/result_serializer.py",
            "backend/query.py",
            "cli.py",
            "tests/test_lifecycle_splitter_v3.py",
            "tests/test_module1_plugin.py",
            "tests/test_cli.py",
            "docs/lifecycle-dfx-guide.md",
        ),
    ),
    Rule(
        rule_id="rules:artifact-contract",
        doc=f"{RULE_DOC}#rulesartifact-contract",
        summary="Formal artifacts have separate, versioned responsibilities.",
        checks=(
            "parse_manifest is integrity/status, metadata is coverage, result is a compact index, and mech_modules is evidence.",
            "Use ArtifactLayout/ArtifactRepository and atomic writes for formal artifacts.",
            "Treat extraction as temporary workspace and never persist raw/context/per-line logs in result.json.",
        ),
        globs=(
            "backend/contracts/artifacts.py",
            "backend/infrastructure/artifact_layout.py",
            "backend/infrastructure/artifact_repository.py",
            "backend/infrastructure/parse_artifact_session.py",
            "backend/metadata.py",
            "backend/result_serializer.py",
            "backend/extensions/products/*/metadata.py",
            "backend/extensions/products/*/result_serializer.py",
            "backend/parsing/output_writer.py",
            "backend/extensions/products/current/artifacts.py",
            "backend/pipeline.py",
            "backend/extensions/products/current/pipeline.py",
            "backend/application/parse_service.py",
            "tests/test_artifacts.py",
            "tests/test_output_writer.py",
            "tests/test_query.py",
        ),
    ),
    Rule(
        rule_id="rules:deterministic-dfx-boundary",
        doc=f"{RULE_DOC}#rulesdeterministic-dfx-boundary",
        summary="logparse selects deterministic evidence; models consume bounded context only.",
        checks=(
            "Do not invoke Claude CLI or GLM5.1 from standalone logparse.",
            "Keep summaries to one ERROR_CODE: 中文结论 line without raw log text.",
            "Deep DFX is opt-in and limited to 5 windows, 48 lines each, and 80 KiB total.",
        ),
        globs=(
            "backend/dfx.py",
            "backend/application/*dfx*.py",
            "backend/presentation/**",
            "cli.py",
            "tests/test_dfx.py",
            "tests/test_cli.py",
            "docs/lan-dfx-operating-model.md",
            ".agents/skills/logparse-diagnose/**",
        ),
    ),
)


def normalize_path(path: str | Path) -> str:
    normalized = str(path).replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.rstrip("/")


def changed_paths() -> list[str]:
    completed = subprocess.run(
        ["git", "status", "--short"],
        check=False,
        capture_output=True,
        text=True,
    )
    paths: list[str] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:] if len(line) > 3 and line[2] == " " else line.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(normalize_path(path))
    return paths


def select_rules_for_paths(paths: Iterable[str | Path]) -> list[Rule]:
    normalized = [normalize_path(path) for path in paths]
    selected: list[Rule] = []
    for rule in RULES:
        if any(_matches_any(path, rule.globs) for path in normalized):
            selected.append(rule)

    boundary_config = load_boundary_config()
    selected_ids = {rule.rule_id for rule in selected}
    for path in normalized:
        classified = classify_path(path, boundary_config)
        if classified is None:
            continue
        boundary_rule = _boundary_rule(classified.zone.name, classified.zone.label, classified.zone.policy)
        if boundary_rule.rule_id not in selected_ids:
            selected.append(boundary_rule)
            selected_ids.add(boundary_rule.rule_id)
    return selected


def render_rules(rules: Iterable[Rule]) -> str:
    lines = ["Rule preflight: read these before analysis or edits."]
    rendered_any = False
    for rule in rules:
        rendered_any = True
        lines.append(f"- {rule.rule_id} ({rule.doc})")
        lines.append(f"  summary: {rule.summary}")
        for check in rule.checks:
            lines.append(f"  check: {check}")
    if not rendered_any:
        lines.append("- No matching repo-specific rules. Still read CLAUDE.md.")
    return "\n".join(lines)


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _boundary_rule(zone: str, label: str, policy: str) -> Rule:
    checks_by_zone = {
        "green": (
            "Keep product topology and log-format knowledge inside the green extension area.",
            "Add focused tests and record the LAN validation scenario.",
        ),
        "yellow": (
            "Change only when real LAN evidence proves the current policy wrong.",
            "Record a case id, minimal fixture, corpus regression, and schema conclusion.",
        ),
        "red": (
            "Do not modify frozen architecture by default.",
            "Require an accepted ADR, human approval, full validation, and rollback plan.",
        ),
    }
    return Rule(
        rule_id=f"governance:{zone}",
        doc="governance/architecture-boundaries.toml",
        summary=f"{label}: {policy}",
        checks=checks_by_zone[zone],
        globs=(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", nargs="*", default=[], help="Files you plan to inspect or edit")
    parser.add_argument("--changed", action="store_true", help="Use git status --short paths")
    args = parser.parse_args(argv)

    if not args.paths and not args.changed:
        parser.error("pass --paths FILE... or --changed")

    paths = [normalize_path(path) for path in args.paths]
    if args.changed:
        paths.extend(changed_paths())

    print(render_rules(select_rules_for_paths(paths)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
