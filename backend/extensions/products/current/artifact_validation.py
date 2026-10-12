"""Validate the product fields traversed by queries and DFX, without coercion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.infrastructure.artifact_reader import ArtifactReadError, read_json_artifact


def read_current_artifact(path: Path, *, expected_schema: int) -> dict[str, Any]:
    """Reject malformed projections before a consumer starts traversing them."""
    payload = read_json_artifact(path, expected_schema=expected_schema)
    if path.name == "result.json":
        _validate_result(payload, path)
    elif path.name == "metadata.json":
        _validate_metadata(payload, path)
    return payload


def _require(condition: bool, path: Path) -> None:
    if not condition:
        # Neither field values nor JSON snippets belong in diagnostics.
        raise ArtifactReadError(path, "invalid_shape")


def _fields(
    item: dict[str, Any], path: Path, kind: type | tuple[type, ...],
    *names: str, required: bool = False,
) -> None:
    kinds = kind if isinstance(kind, tuple) else (kind,)
    for name in names:
        if name in item or required:
            _require(name in item and type(item[name]) in kinds, path)


def _items(item: dict[str, Any], name: str, path: Path, kind: type) -> list:
    values = item.get(name, [])
    _require(isinstance(values, list), path)
    _require(all(type(value) is kind for value in values), path)
    return values


def _validate_result(payload: dict[str, Any], path: Path) -> None:
    _items(payload, "errors", path, str)
    for module in _items(payload, "mech_results", path, dict):
        _fields(module, path, str, "module_name", "module_key")
        _fields(module, path, int, "diag_entry_count", "journal_entry_count")
        _items(module, "active_master_slots", path, str)
        for slot in _items(module, "slots", path, dict):
            _fields(slot, path, str, "slot_id", required=True)
            _fields(slot, path, list, "board_cycles", required=True)
            _fields(slot, path, bool, "lifecycle_reliable")
            _items(slot, "assignment_decisions", path, dict)
            split = slot.get("lifecycle_split_result")
            if split is not None:
                _require(isinstance(split, dict), path)
                _validate_split(split, path)
            for board in _items(slot, "board_cycles", path, dict):
                _validate_cycle(board, path)
                for cpu in _items(board, "cpu_cycles", path, dict):
                    _fields(cpu, path, str, "cpu_id", required=True)
                    _validate_cycle(cpu, path)


def _validate_cycle(cycle: dict[str, Any], path: Path) -> None:
    _fields(cycle, path, str, "dir_name", required=True)
    _fields(cycle, path, list, "processes", required=True)
    _fields(cycle, path, (str, type(None)), "start_time", "end_time")
    for process in _items(cycle, "processes", path, dict):
        _fields(process, path, str, "process_name", "pid", required=True)
        _fields(process, path, int, "total_count", required=True)
        _fields(process, path, int, "missing_count")
        _items(process, "missing_sequences", path, int)


def _validate_split(split: dict[str, Any], path: Path) -> None:
    _fields(split, path, str, "algorithm")
    _fields(split, path, bool, "lifecycle_reliable")
    for name in ("candidate_segments", "lifecycles"):
        for segment in _items(split, name, path, dict):
            _fields(segment, path, str, "scope", "slot")
            _fields(
                segment, path, (str, type(None)),
                "cpu_id", "parent_lifecycle_id", "start_time", "end_time",
            )
            _fields(segment, path, int, "candidate_index", "lifecycle_index", "log_count")
            _items(segment, "candidate_indices", path, int)
    for decision in _items(split, "merge_decisions", path, dict):
        _fields(decision, path, str, "decision", "blocking_reason", "reason_zh")
        _items(decision, "left_candidate_indices", path, int)
        _items(decision, "right_candidate_indices", path, int)
        for counts in _items(decision, "reliable_pid_counts", path, dict):
            _fields(counts, path, str, "process_name")
            _fields(counts, path, int, "count")
            _items(counts, "pids", path, str)
        _items(decision, "journal_evidence", path, dict)
    _items(split, "journal_evidence", path, dict)
    for issue in _items(split, "issues", path, dict):
        _items(issue, "observed_pids", path, str)


def _validate_metadata(payload: dict[str, Any], path: Path) -> None:
    _items(payload, "errors", path, str)
    for slot in _items(payload, "diagnostic_slots", path, dict):
        _fields(slot, path, str, "slot_id", "role", required=True)
        _items(slot, "diagnostic_logs", path, dict)
        _items(slot, "active_periods", path, dict)
    for slot in _items(payload, "private_slots", path, dict):
        _items(slot, "journal_logs", path, dict)
