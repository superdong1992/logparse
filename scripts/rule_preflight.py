#!/usr/bin/env python3
"""List repository rules that must be read before touching selected files."""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


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
            "backend/plugins/mechanisms/module*.py",
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
            "backend/plugins/*/scanner.py",
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
            "backend/plugins/mechanisms/module1.py",
            "backend/parsing/lifecycle_common.py",
            "backend/parsing/lifecycle_splitter_v3.py",
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
            "backend/result_serializer.py",
            "backend/query.py",
            "cli.py",
            "tests/test_lifecycle_splitter_v3.py",
            "tests/test_module1_plugin.py",
            "tests/test_cli.py",
            "docs/lifecycle-dfx-guide.md",
        ),
    ),
)


def normalize_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").lstrip("./")


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
