#!/usr/bin/env python3
"""Classify repository changes and enforce LAN architecture boundaries."""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "governance" / "architecture-boundaries.toml"
CHANGE_RECORD_ROOT = REPO_ROOT / "governance" / "changes"

# These guardrails cannot be downgraded by editing the TOML they protect. This
# does not replace repository permissions or human review, but it prevents a
# boundary-only edit from reclassifying the gate itself as routine business.
IMMUTABLE_RED_PATHS = (
    "governance/**",
    "scripts/change_gate.py",
    "scripts/rule_preflight.py",
    "scripts/verify_delivery.py",
    "pyproject.toml",
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "docs/architecture.md",
    "docs/lan-dfx-operating-model.md",
    "docs/lan-development-guide.md",
    "docs/adr/**",
    "docs/rules/**",
    ".agents/skills/logparse-develop/**",
    ".claude/skills/logparse-develop/**",
)


@dataclass(frozen=True)
class Zone:
    name: str
    label: str
    severity: int
    policy: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class BoundaryConfig:
    schema_version: int
    default_zone: str
    precedence: tuple[str, ...]
    ignored_paths: tuple[str, ...]
    authority: Mapping[str, Any]
    zones: Mapping[str, Zone]


@dataclass(frozen=True)
class ClassifiedPath:
    path: str
    zone: Zone
    matched_pattern: str | None


def normalize_path(path: str | Path) -> str:
    """Return a stable repository-relative POSIX path."""

    candidate = Path(path)
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(REPO_ROOT.resolve())
        except ValueError:
            return candidate.as_posix().rstrip("/")
    normalized = candidate.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.rstrip("/")


def load_boundary_config(path: str | Path = DEFAULT_CONFIG_PATH) -> BoundaryConfig:
    config_path = Path(path)
    with config_path.open("rb") as stream:
        payload = tomllib.load(stream)

    if payload.get("schema_version") != 1:
        raise ValueError("architecture boundary schema_version must be 1")

    raw_zones = payload.get("zones")
    if not isinstance(raw_zones, dict) or not raw_zones:
        raise ValueError("architecture boundaries must define zones")
    if set(raw_zones) != {"green", "yellow", "red"}:
        raise ValueError("architecture boundaries must define green, yellow, and red")

    zones: dict[str, Zone] = {}
    for name, raw_zone in raw_zones.items():
        if not isinstance(raw_zone, dict):
            raise ValueError(f"zone {name!r} must be a table")
        zones[name] = Zone(
            name=name,
            label=_required_string(raw_zone, "label", f"zone {name}"),
            severity=_required_int(raw_zone, "severity", f"zone {name}"),
            policy=_required_string(raw_zone, "policy", f"zone {name}"),
            paths=tuple(_required_string_list(raw_zone, "paths", f"zone {name}")),
        )

    default_zone = _required_string(payload, "default_zone", "boundary config")
    if default_zone not in zones:
        raise ValueError(f"unknown default zone: {default_zone}")

    precedence = tuple(_required_string_list(payload, "precedence", "boundary config"))
    if set(precedence) != set(zones):
        raise ValueError("precedence must list every zone exactly once")

    return BoundaryConfig(
        schema_version=1,
        default_zone=default_zone,
        precedence=precedence,
        ignored_paths=tuple(payload.get("ignored_paths", ())),
        authority=dict(payload.get("authority", {})),
        zones=zones,
    )


def classify_path(path: str | Path, config: BoundaryConfig) -> ClassifiedPath | None:
    normalized = normalize_path(path)
    for pattern in IMMUTABLE_RED_PATHS:
        if fnmatch.fnmatchcase(normalized, pattern):
            return ClassifiedPath(normalized, config.zones["red"], pattern)
    if not normalized or _matches_any(normalized, config.ignored_paths):
        return None

    for zone_name in config.precedence:
        zone = config.zones[zone_name]
        for pattern in zone.paths:
            if fnmatch.fnmatchcase(normalized, pattern):
                return ClassifiedPath(normalized, zone, pattern)

    return ClassifiedPath(normalized, config.zones[config.default_zone], None)


def classify_paths(
    paths: Iterable[str | Path], config: BoundaryConfig
) -> list[ClassifiedPath]:
    seen: set[str] = set()
    classified: list[ClassifiedPath] = []
    for path in paths:
        item = classify_path(path, config)
        if item is None or item.path in seen:
            continue
        seen.add(item.path)
        classified.append(item)
    return classified


def changed_paths() -> list[str]:
    """Return tracked and untracked worktree paths without parsing rename text."""

    commands = (
        ["git", "diff", "--name-only", "-z", "--diff-filter=ACMRDTUXB"],
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRDTUXB"],
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
    )
    return _collect_git_paths(commands)


def commit_range_paths(base: str, head: str) -> list[str]:
    return _collect_git_paths(([
        "git",
        "diff",
        "--name-only",
        "-z",
        "--diff-filter=ACMRDTUXB",
        base,
        head,
    ],))


def load_change_record(path: str | Path) -> Mapping[str, Any]:
    record_path = Path(path)
    with record_path.open("r", encoding="utf-8") as stream:
        payload = yaml.safe_load(stream)
    if not isinstance(payload, dict):
        raise ValueError("change record must be a YAML mapping")
    return payload


def validate_change_record(
    record: Mapping[str, Any],
    changes: Sequence[ClassifiedPath],
) -> list[str]:
    errors: list[str] = []

    if record.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for field in ("change_id", "summary"):
        if not _present(record.get(field)):
            errors.append(f"{field} is required")
    change_id = record.get("change_id")
    if _present(change_id) and not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*",
        str(change_id),
    ):
        errors.append("change_id must use YYYY-MM-DD-lowercase-slug")

    declared_zones = record.get("zones")
    if not _nonempty_string_list(declared_zones):
        errors.append("zones must be a non-empty list")
        declared_zone_set: set[str] = set()
    else:
        declared_zone_set = {str(value).lower() for value in declared_zones}
        unknown_zones = sorted(declared_zone_set - {"green", "yellow", "red"})
        if unknown_zones:
            errors.append(f"zones contains unknown values: {', '.join(unknown_zones)}")

    actual_zone_set = {change.zone.name for change in changes}
    missing_zones = sorted(actual_zone_set - declared_zone_set)
    if missing_zones:
        errors.append(f"zones does not declare: {', '.join(missing_zones)}")

    coverage = record.get("paths")
    if not _nonempty_string_list(coverage):
        errors.append("paths must be a non-empty list of paths or globs")
    else:
        normalized_coverage = [normalize_path(value) for value in coverage]
        uncovered = [
            change.path
            for change in changes
            if not _matches_any(change.path, normalized_coverage)
        ]
        if uncovered:
            errors.append(f"paths does not cover: {', '.join(uncovered)}")

    _require_fields(
        record,
        (
            "validation.tests",
            "validation.lan_scenario",
        ),
        errors,
    )

    if "yellow" in actual_zone_set:
        _require_fields(
            record,
            (
                "evidence.real_case_id",
                "evidence.fixture",
                "evidence.corpus_regression",
                "compatibility.schema_impact",
            ),
            errors,
        )

    if "red" in actual_zone_set:
        _require_fields(
            record,
            (
                "architecture.adr",
                "architecture.approval",
                "architecture.rollback",
                "validation.contract_tests",
                "validation.security_tests",
                "validation.smoke_tests",
            ),
            errors,
        )
        _validate_accepted_adrs(record, errors)

    return errors


def highest_zone(
    changes: Sequence[ClassifiedPath], config: BoundaryConfig
) -> Zone | None:
    if not changes:
        return None
    return max((change.zone for change in changes), key=lambda zone: zone.severity)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", nargs="+", default=[], help="Repository paths to classify")
    parser.add_argument("--changed", action="store_true", help="Classify worktree changes")
    parser.add_argument("--base", help="Base revision for a commit-range check")
    parser.add_argument("--head", help="Head revision; defaults to HEAD with --base")
    parser.add_argument("--enforce", action="store_true", help="Reject missing governance evidence")
    parser.add_argument("--change-record", help="YAML change record used by --enforce")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Architecture boundary TOML path",
    )
    args = parser.parse_args(argv)

    if args.head and not args.base:
        parser.error("--head requires --base")
    if not args.paths and not args.changed and not args.base:
        parser.error("pass --paths, --changed, or --base")

    paths = list(args.paths)
    try:
        if args.changed:
            paths.extend(changed_paths())
        if args.base:
            paths.extend(commit_range_paths(args.base, args.head or "HEAD"))
    except RuntimeError as error:
        print(f"change gate git error: {error}", file=sys.stderr)
        return 2

    record_path = normalize_path(args.change_record) if args.change_record else None
    governed_paths = [path for path in paths if normalize_path(path) != record_path]

    try:
        config = load_boundary_config(args.config)
        changes = classify_paths(governed_paths, config)
    except (OSError, ValueError) as error:
        print(f"change gate configuration error: {error}", file=sys.stderr)
        return 2

    for change in changes:
        match = change.matched_pattern or "<default>"
        print(f"{change.zone.name.upper():6} {change.path}  [{match}]")

    top_zone = highest_zone(changes, config)
    if top_zone is None:
        print("No governed source changes.")
        return 0

    print(f"Highest zone: {top_zone.name} ({top_zone.label})")
    print(f"Policy: {top_zone.policy}")

    if not args.enforce:
        return 0
    if not args.change_record:
        print("DENY: --enforce requires --change-record", file=sys.stderr)
        return 2

    try:
        record_input_path = Path(args.change_record)
        if not record_input_path.is_absolute():
            record_input_path = REPO_ROOT / record_input_path
        record_fs_path = record_input_path.resolve()
        record_fs_path.relative_to(CHANGE_RECORD_ROOT.resolve())
        if "template" in record_fs_path.name.lower():
            raise ValueError("the template is not a completed change record")
        record = load_change_record(record_fs_path)
    except (OSError, ValueError) as error:
        print(f"DENY: invalid change record: {error}", file=sys.stderr)
        return 2

    errors = validate_change_record(record, changes)
    if errors:
        for error in errors:
            print(f"DENY: {error}", file=sys.stderr)
        return 2

    print("ALLOW: change record satisfies the enforced boundary requirements.")
    return 0


def _collect_git_paths(commands: Iterable[list[str]]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(stderr or f"git command failed: {' '.join(command)}")
        for raw_path in completed.stdout.split(b"\0"):
            if not raw_path:
                continue
            path = normalize_path(raw_path.decode("utf-8", errors="surrogateescape"))
            if path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _required_string(payload: Mapping[str, Any], key: str, owner: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{owner}.{key} must be a non-empty string")
    return value


def _required_int(payload: Mapping[str, Any], key: str, owner: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise ValueError(f"{owner}.{key} must be an integer")
    return value


def _required_string_list(
    payload: Mapping[str, Any], key: str, owner: str
) -> list[str]:
    value = payload.get(key)
    if not _nonempty_string_list(value):
        raise ValueError(f"{owner}.{key} must be a non-empty string list")
    return list(value)


def _nonempty_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def _present(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.strip().casefold()
        return bool(normalized) and normalized not in {
            "todo",
            "tbd",
            "pending",
            "fixme",
            "placeholder",
        }
    if isinstance(value, (list, tuple)):
        return bool(value) and all(_present(item) for item in value)
    if isinstance(value, dict):
        return bool(value) and all(_present(item) for item in value.values())
    return value is not None


def _nested_value(payload: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _require_fields(
    payload: Mapping[str, Any], fields: Iterable[str], errors: list[str]
) -> None:
    for field in fields:
        if not _present(_nested_value(payload, field)):
            errors.append(f"{field} is required")


def _validate_accepted_adrs(
    payload: Mapping[str, Any], errors: list[str]
) -> None:
    value = _nested_value(payload, "architecture.adr")
    if not _present(value):
        return
    adr_paths = [value] if isinstance(value, str) else value
    if not _nonempty_string_list(adr_paths):
        errors.append("architecture.adr must be a path or non-empty path list")
        return

    adr_root = (REPO_ROOT / "docs" / "adr").resolve()
    for raw_path in adr_paths:
        normalized = normalize_path(raw_path)
        candidate = (REPO_ROOT / normalized).resolve()
        try:
            candidate.relative_to(adr_root)
        except ValueError:
            errors.append(f"architecture.adr is outside docs/adr: {raw_path}")
            continue
        try:
            content = candidate.read_text(encoding="utf-8")
        except OSError:
            errors.append(f"architecture.adr does not exist: {raw_path}")
            continue
        accepted = any(
            line.strip().lower() in {"status: accepted", "- status: accepted"}
            for line in content.splitlines()
        )
        if not accepted:
            errors.append(f"architecture.adr is not Accepted: {raw_path}")


if __name__ == "__main__":
    raise SystemExit(main())
