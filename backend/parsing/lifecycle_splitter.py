"""Lifecycle split v2 skeleton.

This module is intentionally independent from ``CycleDetector``.  It uses
positive boundary constraints from reliable processes, then exposes a v2 result
object while still being able to build the legacy board/cpu cycle tree needed by
the current writer and query paths.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.models import (
    MechBoardCycle,
    MechCpuCycle,
    MechCycleSplitTrace,
    MechLogEntry,
    MechProcessLifecycle,
)


class LifecycleSolverInvariantError(RuntimeError):
    """Raised when valid constraints cannot be satisfied by their candidates."""


class LifecycleSplitConfig(BaseModel):
    enabled: bool = False
    process_name_mapping: dict[str, list[str]] = Field(default_factory=dict)
    reliable_processes: list[str] = Field(default_factory=list)
    multi_instance_processes: list[str] = Field(default_factory=list)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> "LifecycleSplitConfig":
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError("lifecycle_split config must be an object")
        enabled = raw.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("lifecycle_split.enabled must be a boolean")

        mapping_raw = raw.get("process_name_mapping", {})
        if not isinstance(mapping_raw, dict):
            raise ValueError("lifecycle_split.process_name_mapping must be an object")
        mapping: dict[str, list[str]] = {}
        for canonical, aliases in mapping_raw.items():
            if aliases is None:
                mapping[str(canonical)] = []
            elif isinstance(aliases, str):
                mapping[str(canonical)] = [aliases]
            else:
                try:
                    mapping[str(canonical)] = [str(alias) for alias in aliases]
                except TypeError as exc:
                    raise ValueError(
                        f"lifecycle_split.process_name_mapping.{canonical} must be a list"
                    ) from exc

        reliable_raw = raw.get("reliable_processes", [])
        reliable_processes = _parse_reliable_processes(
            reliable_raw,
            base_path="lifecycle_split.reliable_processes",
        )
        multi_raw = raw.get("multi_instance_processes", [])
        if multi_raw is not None and not isinstance(multi_raw, list):
            raise ValueError("lifecycle_split.multi_instance_processes must be a list")

        return cls(
            enabled=enabled,
            process_name_mapping=mapping,
            reliable_processes=reliable_processes,
            multi_instance_processes=[str(name) for name in (multi_raw or [])],
        )


class PositiveBoundaryConstraint(BaseModel):
    scope: str
    slot: str
    cpu_id: str | None = None
    type: str
    process_name: str = ""
    old_pid: str = ""
    new_pid: str = ""
    old_sequence: int = 0
    new_sequence: int = 0
    old_observed_time: datetime
    new_observed_time: datetime
    candidate_time: datetime
    old_raw: str = ""
    new_raw: str = ""


class BoundaryCandidate(BaseModel):
    scope: str
    slot: str
    cpu_id: str | None = None
    timestamp: datetime
    source_type: str
    constraint_index: int


class LifecycleBoundary(BaseModel):
    id: str
    origin_scope: str
    origin_scope_label_zh: str
    effective_scopes: list[dict[str, Any]] = Field(default_factory=list)
    slot: str
    cpu_id: str | None = None
    timestamp: datetime
    support_evidence: list[dict[str, Any]] = Field(default_factory=list)
    type: str
    type_label_zh: str
    title_zh: str
    explanation_zh: str


class LifecycleCycle(BaseModel):
    scope: str
    scope_label_zh: str
    slot: str
    cpu_id: str | None = None
    cycle_index: int
    start_time: datetime | None = None
    end_time: datetime | None = None
    next_boundary_time: datetime | None = None
    lifecycle_reliable: bool = True


class LifecycleEvidence(BaseModel):
    type: str
    type_label_zh: str
    scope: str
    scope_label_zh: str
    slot: str
    cpu_id: str | None = None
    process_name: str = ""
    old_pid: str = ""
    new_pid: str = ""
    old_sequence: int = 0
    new_sequence: int = 0
    old_observed_time: datetime | None = None
    new_observed_time: datetime | None = None
    old_raw: str = ""
    new_raw: str = ""
    support_type: str
    support_type_label_zh: str
    covered_boundaries: list[dict[str, Any]] = Field(default_factory=list)
    title_zh: str
    explanation_zh: str


class LifecycleIssue(BaseModel):
    type: str
    type_label_zh: str
    severity: str
    severity_label_zh: str
    scope: str
    scope_label_zh: str
    slot: str
    cpu_id: str | None = None
    related_process: str = ""
    related_boundaries: list[dict[str, Any]] = Field(default_factory=list)
    affected_cycles: list[dict[str, Any]] = Field(default_factory=list)
    conflicting_cycle_pairs: list[dict[str, Any]] = Field(default_factory=list)
    observed_pids: list[str] = Field(default_factory=list)
    observed_time: datetime | None = None
    source: str = ""
    source_file: str = ""
    raw_excerpt: str = ""
    reason_zh: str = ""
    cycle_window: dict[str, Any] = Field(default_factory=dict)
    pid_runs: list[dict[str, Any]] = Field(default_factory=list)
    expected_boundary_intervals: list[dict[str, Any]] = Field(default_factory=list)
    covered_boundaries: list[dict[str, Any]] = Field(default_factory=list)
    rule_zh: str = ""
    facts_zh: str = ""
    current_result_zh: str = ""
    conflict_reason_zh: str = ""
    impact_zh: str = ""
    action_zh: str = ""
    title_zh: str
    explanation_zh: str


class LifecycleScopeResult(BaseModel):
    scope_key: dict[str, Any]
    scope: str
    scope_label_zh: str
    slot: str
    cpu_id: str | None = None
    origin_boundaries: list[dict[str, Any]] = Field(default_factory=list)
    effective_boundaries: list[dict[str, Any]] = Field(default_factory=list)
    cycle_indices: list[int] = Field(default_factory=list)
    lifecycle_reliable: bool = True


class LifecycleSplitResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    scopes: list[LifecycleScopeResult] = Field(default_factory=list)
    boundaries: list[LifecycleBoundary] = Field(default_factory=list)
    cycles: list[LifecycleCycle] = Field(default_factory=list)
    evidence: list[LifecycleEvidence] = Field(default_factory=list)
    issues: list[LifecycleIssue] = Field(default_factory=list)
    lifecycle_reliable: bool = True
    constraints: list[PositiveBoundaryConstraint] = Field(default_factory=list, exclude=True)
    candidates: list[BoundaryCandidate] = Field(default_factory=list, exclude=True)
    canonical_entries: list[MechLogEntry] = Field(default_factory=list, exclude=True)


ScopeKey = tuple[str, str, str | None]

_SCOPE_LABEL_ZH = {
    "board": "板卡",
    "cpu": "CPU",
}
_BOUNDARY_TYPE_LABEL_ZH = {
    "reliable_process_pid_changed": "可靠进程 PID 变化",
    "journal_sequence_wrapped": "journal 序号回绕",
}
_SUPPORT_TYPE_LABEL_ZH = {
    "tight_support": "精确支撑",
    "wide_support": "宽区间支撑",
}
_ISSUE_TYPE_LABEL_ZH = {
    "invalid_lifecycle_evidence": "生命周期证据结构无效",
    "reliable_process_multiple_pid_in_cycle": "可靠进程同一生命周期内出现多个 PID",
    "same_pid_single_boundary_conflict": "唯一进程 same PID 跨相邻生命周期",
}
_SEVERITY_LABEL_ZH = {
    "error": "错误",
    "warning": "警告",
}


class LifecycleSplitter:
    """Split mechanism logs using v2 lifecycle constraints."""

    def __init__(
        self,
        config: LifecycleSplitConfig,
        *,
        module_key: str = "",
        module_name: str = "",
    ):
        self.config = config
        self.module_key = module_key
        self.module_name = module_name
        self._alias_to_canonical = self._build_alias_map(config)
        self._reliable_processes = {
            self._canonicalize(name) for name in config.reliable_processes
        }
        self._multi_instance = {
            self._canonicalize(name) for name in config.multi_instance_processes
        }
        self._validate_config()

    def split(self, entries: list[MechLogEntry]) -> LifecycleSplitResult:
        canonical_entries = self._canonicalize_entries(entries)
        scope_keys = self._scope_keys(canonical_entries)
        constraints, candidates, issues = self._build_positive_constraints(canonical_entries)

        board_boundaries = self._solve_origin_boundaries(
            [c for c in constraints if c.scope == "board"],
            fixed_boundaries=[],
        )
        board_by_slot: dict[str, list[LifecycleBoundary]] = defaultdict(list)
        for boundary in board_boundaries:
            board_by_slot[boundary.slot].append(boundary)

        cpu_constraints_by_scope: dict[ScopeKey, list[PositiveBoundaryConstraint]] = defaultdict(list)
        for constraint in constraints:
            if constraint.scope == "cpu":
                cpu_constraints_by_scope[
                    ("cpu", constraint.slot, constraint.cpu_id)
                ].append(constraint)

        cpu_boundaries: list[LifecycleBoundary] = []
        for key in sorted(cpu_constraints_by_scope):
            _, slot, _cpu_id = key
            cpu_boundaries.extend(
                self._solve_origin_boundaries(
                    cpu_constraints_by_scope[key],
                    fixed_boundaries=board_by_slot.get(slot, []),
                )
            )

        boundaries = sorted(
            board_boundaries + cpu_boundaries,
            key=lambda b: (b.slot, b.timestamp, b.origin_scope, b.cpu_id or ""),
        )
        self._fill_effective_scopes(boundaries, scope_keys)

        effective_by_scope = self._effective_boundaries_by_scope(scope_keys, boundaries)
        scopes = self._build_scope_results(scope_keys, boundaries, effective_by_scope)
        cycles = self._build_lifecycle_cycles(canonical_entries, effective_by_scope)
        evidence = self._build_evidence(constraints, boundaries, effective_by_scope)
        self._attach_invalid_evidence_cycles(issues, effective_by_scope)
        issues.extend(self._find_reliable_multi_pid_issues(
            canonical_entries,
            effective_by_scope,
            cycles,
        ))
        issues.extend(self._find_same_pid_issues(canonical_entries, effective_by_scope))

        result = LifecycleSplitResult(
            scopes=scopes,
            boundaries=boundaries,
            cycles=cycles,
            evidence=evidence,
            issues=issues,
            lifecycle_reliable=True,
            constraints=constraints,
            candidates=candidates,
            canonical_entries=canonical_entries,
        )
        self._propagate_reliability(result)
        return result

    def build_board_cycles(self, result: LifecycleSplitResult) -> list[MechBoardCycle]:
        entries = result.canonical_entries
        if not entries:
            return []

        board_cycles: list[MechBoardCycle] = []
        board_cycles_by_slot_index: dict[tuple[str, int], MechBoardCycle] = {}
        slots = sorted({entry.slot for entry in entries})

        for slot in slots:
            slot_entries = [entry for entry in entries if entry.slot == slot]
            board_boundaries = self._boundary_times_for_scope(result, ("board", slot, None))
            max_index = max(
                [self._cycle_index(entry.timestamp, board_boundaries) for entry in slot_entries if entry.timestamp]
                or [0]
            )
            for cycle_index in range(max_index + 1):
                cycle_entries = [
                    entry
                    for entry in slot_entries
                    if entry.timestamp
                    and self._cycle_index(entry.timestamp, board_boundaries) == cycle_index
                ]
                board_entries = [entry for entry in cycle_entries if not entry.cpu_id]
                start, end = _entry_time_bounds(cycle_entries)
                board_cycle = MechBoardCycle(
                    dir_name=_format_cycle_dir(start, end),
                    start_time=start,
                    end_time=end,
                    split_traces=self._split_traces_for_scope(
                        result, ("board", slot, None), cycle_index
                    ),
                    processes=_build_process_lifecycles(board_entries),
                )
                board_cycles.append(board_cycle)
                board_cycles_by_slot_index[(slot, cycle_index)] = board_cycle

        for key in sorted(
            {("cpu", entry.slot, entry.cpu_id) for entry in entries if entry.cpu_id}
        ):
            _, slot, cpu_id = key
            cpu_entries = [
                entry for entry in entries if entry.slot == slot and entry.cpu_id == cpu_id
            ]
            board_boundaries = self._boundary_times_for_scope(result, ("board", slot, None))
            cpu_boundaries = self._boundary_times_for_scope(result, key)
            grouped: dict[tuple[int, int], list[MechLogEntry]] = defaultdict(list)
            for entry in cpu_entries:
                if entry.timestamp is None:
                    continue
                board_index = self._cycle_index(entry.timestamp, board_boundaries)
                cpu_index = self._cycle_index(entry.timestamp, cpu_boundaries)
                grouped[(board_index, cpu_index)].append(entry)

            for (board_index, cpu_index), group_entries in sorted(grouped.items()):
                board_cycle = board_cycles_by_slot_index.get((slot, board_index))
                if board_cycle is None:
                    continue
                start, end = _entry_time_bounds(group_entries)
                board_cycle.cpu_cycles.append(
                    MechCpuCycle(
                        cpu_id=cpu_id or "",
                        dir_name=_format_cycle_dir(start, end),
                        start_time=start,
                        end_time=end,
                        split_traces=self._split_traces_for_scope(result, key, cpu_index),
                        processes=_build_process_lifecycles(group_entries),
                    )
                )

        return board_cycles

    def _build_alias_map(self, config: LifecycleSplitConfig) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for canonical, raw_aliases in config.process_name_mapping.items():
            aliases[_norm(canonical)] = canonical
            for alias in raw_aliases:
                aliases[_norm(alias)] = canonical
        return aliases

    def _canonicalize(self, process_name: str) -> str:
        return self._alias_to_canonical.get(_norm(process_name), process_name)

    def _canonicalize_entries(self, entries: list[MechLogEntry]) -> list[MechLogEntry]:
        canonical_entries: list[MechLogEntry] = []
        for entry in entries:
            canonical_entries.append(
                entry.model_copy(
                    update={
                        "process_name": self._canonicalize(entry.process_name),
                        "cpu_id": "" if entry.cpu_id in (None, "0") else entry.cpu_id,
                    }
                )
            )
        return canonical_entries

    def _validate_config(self) -> None:
        reliable = {_norm(name) for name in self._reliable_processes}
        multi = {_norm(name) for name in self._multi_instance}
        conflicts = {
            "reliable_processes/multi_instance_processes": reliable & multi,
        }
        active_conflicts = {
            name: sorted(values) for name, values in conflicts.items() if values
        }
        if active_conflicts:
            raise ValueError(
                "invalid lifecycle_split config: each canonical process may appear in only one of "
                "reliable_processes, multi_instance_processes; "
                f"conflicts={active_conflicts}"
            )

    def _scope_keys(self, entries: list[MechLogEntry]) -> list[ScopeKey]:
        keys: set[ScopeKey] = set()
        for entry in entries:
            if not entry.slot:
                continue
            keys.add(("board", entry.slot, None))
            if entry.cpu_id:
                keys.add(("cpu", entry.slot, entry.cpu_id))
        return sorted(keys, key=lambda key: (key[1], key[0], key[2] or ""))

    def _build_positive_constraints(
        self,
        entries: list[MechLogEntry],
    ) -> tuple[list[PositiveBoundaryConstraint], list[BoundaryCandidate], list[LifecycleIssue]]:
        constraints: list[PositiveBoundaryConstraint] = []
        candidates: list[BoundaryCandidate] = []
        issues: list[LifecycleIssue] = []

        by_scope: dict[ScopeKey, list[MechLogEntry]] = defaultdict(list)
        for entry in entries:
            scope = "cpu" if entry.cpu_id else "board"
            cpu_id = entry.cpu_id if entry.cpu_id else None
            by_scope[(scope, entry.slot, cpu_id)].append(entry)
        for key, scope_entries in by_scope.items():
            scope, _slot, _cpu_id = key
            for process_name in sorted(self._reliable_processes):
                process_entries = [
                    entry for entry in scope_entries if entry.process_name == process_name
                ]
                observations: list[MechLogEntry] = []
                for entry in process_entries:
                    if not entry.pid:
                        if entry.source == "journal":
                            continue
                        issues.append(self._invalid_evidence_issue(
                            entry,
                            scope=scope,
                            reason="可靠进程 PID 变化证据缺少 timestamp 或 PID。",
                        ))
                        continue
                    if entry.timestamp is None:
                        issues.append(self._invalid_evidence_issue(
                            entry,
                            scope=scope,
                            reason="可靠进程 PID 变化证据缺少 timestamp 或 PID。",
                        ))
                        continue
                    observations.append(entry)
                observations.sort(key=_entry_sort_key)
                runs = _compress_pid_runs(observations)
                for old_entry, new_entry in zip(runs, runs[1:]):
                    if old_entry.pid == new_entry.pid:
                        continue
                    if old_entry.timestamp >= new_entry.timestamp:
                        continue
                    constraint = PositiveBoundaryConstraint(
                        scope=scope,
                        slot=new_entry.slot,
                        cpu_id=new_entry.cpu_id or None,
                        type="reliable_process_pid_changed",
                        process_name=process_name,
                        old_pid=old_entry.pid,
                        new_pid=new_entry.pid,
                        old_sequence=old_entry.sequence,
                        new_sequence=new_entry.sequence,
                        old_observed_time=old_entry.timestamp,
                        new_observed_time=new_entry.timestamp,
                        candidate_time=new_entry.timestamp,
                        old_raw=old_entry.raw,
                        new_raw=new_entry.raw,
                    )
                    constraints.append(constraint)
                    candidates.append(BoundaryCandidate(
                        scope=scope,
                        slot=new_entry.slot,
                        cpu_id=new_entry.cpu_id or None,
                        timestamp=new_entry.timestamp,
                        source_type=constraint.type,
                        constraint_index=len(constraints) - 1,
                    ))

            journal_observations: list[MechLogEntry] = []
            for entry in [entry for entry in scope_entries if entry.source == "journal"]:
                if entry.sequence <= 0:
                    continue
                if entry.timestamp is None:
                    issues.append(self._invalid_evidence_issue(
                        entry,
                        scope=scope,
                        reason="journal 序号回绕证据缺少 timestamp。",
                    ))
                    continue
                journal_observations.append(entry)
            journal_observations.sort(key=_entry_timestamp_key)
            for old_entry, new_entry in zip(journal_observations, journal_observations[1:]):
                if new_entry.sequence >= old_entry.sequence:
                    continue
                if old_entry.timestamp >= new_entry.timestamp:
                    issues.append(self._invalid_evidence_issue(
                        new_entry,
                        scope=scope,
                        reason="journal 序号回绕证据时间顺序非法，无法定位到正向边界区间。",
                    ))
                    continue
                constraint = PositiveBoundaryConstraint(
                    scope=scope,
                    slot=new_entry.slot,
                    cpu_id=new_entry.cpu_id or None,
                    type="journal_sequence_wrapped",
                    process_name=new_entry.process_name,
                    old_sequence=old_entry.sequence,
                    new_sequence=new_entry.sequence,
                    old_observed_time=old_entry.timestamp,
                    new_observed_time=new_entry.timestamp,
                    candidate_time=new_entry.timestamp,
                    old_raw=old_entry.raw,
                    new_raw=new_entry.raw,
                )
                constraints.append(constraint)
                candidates.append(BoundaryCandidate(
                    scope=scope,
                    slot=new_entry.slot,
                    cpu_id=new_entry.cpu_id or None,
                    timestamp=new_entry.timestamp,
                    source_type=constraint.type,
                    constraint_index=len(constraints) - 1,
                ))

        return constraints, candidates, issues

    def _solve_origin_boundaries(
        self,
        constraints: list[PositiveBoundaryConstraint],
        *,
        fixed_boundaries: list[LifecycleBoundary],
    ) -> list[LifecycleBoundary]:
        selected: list[LifecycleBoundary] = []
        ordered = sorted(
            constraints,
            key=lambda c: (c.new_observed_time, c.old_observed_time, c.process_name),
        )
        for constraint in ordered:
            fixed = _covering_boundary(fixed_boundaries, constraint)
            if fixed is not None:
                continue

            current = _covering_boundary(selected, constraint)
            if current is not None:
                continue

            if not _constraint_covers_time(constraint, constraint.candidate_time):
                raise LifecycleSolverInvariantError(
                    "positive lifecycle constraint has no candidate inside its interval"
                )

            selected.append(self._boundary_from_constraint(constraint))

        return selected

    def _boundary_from_constraint(
        self,
        constraint: PositiveBoundaryConstraint,
    ) -> LifecycleBoundary:
        boundary_id = (
            f"{constraint.slot}:{constraint.scope}:"
            f"{constraint.cpu_id or 'board'}:{constraint.candidate_time.isoformat()}"
        )
        scope_label = _scope_label(constraint.scope)
        return LifecycleBoundary(
            id=boundary_id,
            origin_scope=constraint.scope,
            origin_scope_label_zh=scope_label,
            effective_scopes=[],
            slot=constraint.slot,
            cpu_id=constraint.cpu_id,
            timestamp=constraint.candidate_time,
            support_evidence=[],
            type=constraint.type,
            type_label_zh=_BOUNDARY_TYPE_LABEL_ZH.get(constraint.type, constraint.type),
            title_zh=f"采用生命周期边界 {constraint.candidate_time.isoformat()}",
            explanation_zh=self._boundary_explanation(constraint, scope_label),
        )

    def _boundary_explanation(
        self,
        constraint: PositiveBoundaryConstraint,
        scope_label: str,
    ) -> str:
        if constraint.type == "journal_sequence_wrapped":
            return (
                f"{scope_label} journal 序号从 {constraint.old_sequence} "
                f"回绕到 {constraint.new_sequence}，说明 "
                f"{constraint.old_observed_time.isoformat()} 到 "
                f"{constraint.new_observed_time.isoformat()} 区间内至少存在一次生命周期边界。"
                "当前切点采用回绕后第一条日志时间，作为后一个 cycle 的起点。"
            )
        return (
            f"{scope_label}可靠进程 {constraint.process_name} 的 PID "
            f"从 {constraint.old_pid} 变为 {constraint.new_pid}，"
            f"说明 {constraint.old_observed_time.isoformat()} 到 "
            f"{constraint.new_observed_time.isoformat()} 区间内至少存在一次生命周期边界。"
            "当前切点采用新 PID 首次观测时间，作为后一个 cycle 的起点。"
        )

    def _support_payload(
        self,
        constraint: PositiveBoundaryConstraint,
        *,
        evidence_scope: str | None = None,
    ) -> dict[str, Any]:
        return {
            "type": constraint.type,
            "type_label_zh": _BOUNDARY_TYPE_LABEL_ZH.get(constraint.type, constraint.type),
            "support_type": "tight_support",
            "support_type_label_zh": _SUPPORT_TYPE_LABEL_ZH["tight_support"],
            "evidence_scope": evidence_scope or constraint.scope,
            "evidence_scope_label_zh": _scope_label(evidence_scope or constraint.scope),
            "process_name": constraint.process_name,
            "old_pid": constraint.old_pid,
            "new_pid": constraint.new_pid,
            "old_sequence": constraint.old_sequence,
            "new_sequence": constraint.new_sequence,
            "old_observed_time": constraint.old_observed_time,
            "new_observed_time": constraint.new_observed_time,
            "old_raw": constraint.old_raw,
            "new_raw": constraint.new_raw,
        }

    def _fill_effective_scopes(
        self,
        boundaries: list[LifecycleBoundary],
        scope_keys: list[ScopeKey],
    ) -> None:
        cpus_by_slot: dict[str, list[str]] = defaultdict(list)
        for scope, slot, cpu_id in scope_keys:
            if scope == "cpu" and cpu_id is not None:
                cpus_by_slot[slot].append(cpu_id)

        for boundary in boundaries:
            if boundary.origin_scope == "board":
                scopes = [_scope_ref("board", boundary.slot, None, inherited=False)]
                for cpu_id in sorted(set(cpus_by_slot.get(boundary.slot, []))):
                    scopes.append(_scope_ref("cpu", boundary.slot, cpu_id, inherited=True))
                boundary.effective_scopes = scopes
            else:
                boundary.effective_scopes = [
                    _scope_ref("cpu", boundary.slot, boundary.cpu_id, inherited=False)
                ]

    def _effective_boundaries_by_scope(
        self,
        scope_keys: list[ScopeKey],
        boundaries: list[LifecycleBoundary],
    ) -> dict[ScopeKey, list[tuple[LifecycleBoundary, bool]]]:
        by_scope: dict[ScopeKey, list[tuple[LifecycleBoundary, bool]]] = {
            key: [] for key in scope_keys
        }
        for key in scope_keys:
            scope, slot, cpu_id = key
            if scope == "board":
                by_scope[key].extend(
                    (boundary, False)
                    for boundary in boundaries
                    if boundary.origin_scope == "board" and boundary.slot == slot
                )
                continue

            by_scope[key].extend(
                (boundary, True)
                for boundary in boundaries
                if boundary.origin_scope == "board" and boundary.slot == slot
            )
            by_scope[key].extend(
                (boundary, False)
                for boundary in boundaries
                if (
                    boundary.origin_scope == "cpu"
                    and boundary.slot == slot
                    and boundary.cpu_id == cpu_id
                )
            )
            by_scope[key] = _unique_effective_boundaries(by_scope[key])

        for key in by_scope:
            by_scope[key].sort(key=lambda item: item[0].timestamp)
        return by_scope

    def _build_scope_results(
        self,
        scope_keys: list[ScopeKey],
        boundaries: list[LifecycleBoundary],
        effective_by_scope: dict[ScopeKey, list[tuple[LifecycleBoundary, bool]]],
    ) -> list[LifecycleScopeResult]:
        scopes: list[LifecycleScopeResult] = []
        for key in scope_keys:
            scope, slot, cpu_id = key
            origin = [
                _boundary_ref(boundary, inherited=False)
                for boundary in boundaries
                if (
                    boundary.origin_scope == scope
                    and boundary.slot == slot
                    and boundary.cpu_id == cpu_id
                )
            ]
            effective = [
                _boundary_ref(
                    boundary,
                    inherited=inherited,
                    target_scope=scope,
                    target_cpu_id=cpu_id,
                )
                for boundary, inherited in effective_by_scope.get(key, [])
            ]
            scopes.append(LifecycleScopeResult(
                scope_key=_scope_key_dict(scope, slot, cpu_id),
                scope=scope,
                scope_label_zh=_scope_label(scope),
                slot=slot,
                cpu_id=cpu_id,
                origin_boundaries=origin,
                effective_boundaries=effective,
                cycle_indices=list(range(len(effective) + 1)),
                lifecycle_reliable=True,
            ))
        return scopes

    def _build_lifecycle_cycles(
        self,
        entries: list[MechLogEntry],
        effective_by_scope: dict[ScopeKey, list[tuple[LifecycleBoundary, bool]]],
    ) -> list[LifecycleCycle]:
        cycles: list[LifecycleCycle] = []
        for key in sorted(effective_by_scope, key=lambda item: (item[1], item[0], item[2] or "")):
            scope, slot, cpu_id = key
            scope_entries = [
                entry
                for entry in entries
                if entry.slot == slot and (entry.cpu_id or None) == cpu_id
                if scope == ("cpu" if entry.cpu_id else "board")
            ]
            boundaries = [boundary.timestamp for boundary, _ in effective_by_scope[key]]
            cycle_count = len(boundaries) + 1
            for cycle_index in range(cycle_count):
                cycle_entries = [
                    entry
                    for entry in scope_entries
                    if entry.timestamp
                    and self._cycle_index(entry.timestamp, boundaries) == cycle_index
                ]
                start, end = _entry_time_bounds(cycle_entries)
                cycles.append(LifecycleCycle(
                    scope=scope,
                    scope_label_zh=_scope_label(scope),
                    slot=slot,
                    cpu_id=cpu_id,
                    cycle_index=cycle_index,
                    start_time=start,
                    end_time=end,
                    next_boundary_time=(
                        boundaries[cycle_index] if cycle_index < len(boundaries) else None
                    ),
                    lifecycle_reliable=True,
                ))
        return cycles

    def _build_evidence(
        self,
        constraints: list[PositiveBoundaryConstraint],
        boundaries: list[LifecycleBoundary],
        effective_by_scope: dict[ScopeKey, list[tuple[LifecycleBoundary, bool]]],
    ) -> list[LifecycleEvidence]:
        evidence: list[LifecycleEvidence] = []
        boundary_by_id = {boundary.id: boundary for boundary in boundaries}
        for constraint in constraints:
            key = (constraint.scope, constraint.slot, constraint.cpu_id)
            covered = [
                _boundary_ref(
                    boundary,
                    inherited=inherited,
                    target_scope=constraint.scope,
                    target_cpu_id=constraint.cpu_id,
                )
                for boundary, inherited in effective_by_scope.get(key, [])
                if _constraint_covers_time(constraint, boundary.timestamp)
            ]
            support_type = "tight_support" if len(covered) == 1 else "wide_support"
            if support_type == "tight_support" and covered:
                boundary = boundary_by_id.get(covered[0]["id"])
                if boundary is not None:
                    boundary.support_evidence.append(
                        self._support_payload(
                            constraint,
                            evidence_scope=constraint.scope,
                        )
                    )
            evidence.append(LifecycleEvidence(
                type=constraint.type,
                type_label_zh=_BOUNDARY_TYPE_LABEL_ZH.get(constraint.type, constraint.type),
                scope=constraint.scope,
                scope_label_zh=_scope_label(constraint.scope),
                slot=constraint.slot,
                cpu_id=constraint.cpu_id,
                process_name=constraint.process_name,
                old_pid=constraint.old_pid,
                new_pid=constraint.new_pid,
                old_sequence=constraint.old_sequence,
                new_sequence=constraint.new_sequence,
                old_observed_time=constraint.old_observed_time,
                new_observed_time=constraint.new_observed_time,
                old_raw=constraint.old_raw,
                new_raw=constraint.new_raw,
                support_type=support_type,
                support_type_label_zh=_SUPPORT_TYPE_LABEL_ZH[support_type],
                covered_boundaries=covered,
                title_zh=(
                    self._evidence_title(constraint)
                    if support_type == "tight_support"
                    else "宽区间证据无法定位具体边界"
                ),
                explanation_zh=self._evidence_explanation(
                    constraint,
                    support_type=support_type,
                    covered_count=len(covered),
                ),
            ))
        return evidence

    def _evidence_title(self, constraint: PositiveBoundaryConstraint) -> str:
        if constraint.type == "journal_sequence_wrapped":
            return "journal 序号回绕证据"
        return "可靠进程 PID 变化证据"

    def _evidence_explanation(
        self,
        constraint: PositiveBoundaryConstraint,
        *,
        support_type: str,
        covered_count: int,
    ) -> str:
        if constraint.type == "journal_sequence_wrapped":
            fact = (
                f"journal 序号 {constraint.old_sequence}->{constraint.new_sequence}"
            )
        else:
            fact = (
                f"{constraint.process_name} PID "
                f"{constraint.old_pid}->{constraint.new_pid}"
            )
        return (
            f"{_scope_label(constraint.scope)}证据 {fact} 覆盖 "
            f"{covered_count} 个 effective boundary，"
            f"支撑类型为 {_SUPPORT_TYPE_LABEL_ZH[support_type]}。"
        )

    def _find_reliable_multi_pid_issues(
        self,
        entries: list[MechLogEntry],
        effective_by_scope: dict[ScopeKey, list[tuple[LifecycleBoundary, bool]]],
        cycles: list[LifecycleCycle],
    ) -> list[LifecycleIssue]:
        issues: list[LifecycleIssue] = []
        cycle_lookup = {
            (cycle.scope, cycle.slot, cycle.cpu_id, cycle.cycle_index): cycle
            for cycle in cycles
        }
        for key, boundary_items in effective_by_scope.items():
            scope, slot, cpu_id = key
            boundaries = [boundary.timestamp for boundary, _ in boundary_items]
            by_process_cycle: dict[tuple[str, int], list[MechLogEntry]] = defaultdict(list)
            for entry in entries:
                entry_scope = "cpu" if entry.cpu_id else "board"
                if (
                    entry_scope != scope
                    or entry.slot != slot
                    or (entry.cpu_id or None) != cpu_id
                    or entry.process_name not in self._reliable_processes
                    or entry.process_name in self._multi_instance
                    or not entry.pid
                    or entry.timestamp is None
                ):
                    continue
                cycle_index = self._cycle_index(entry.timestamp, boundaries)
                by_process_cycle[(entry.process_name, cycle_index)].append(entry)

            for (process_name, cycle_index), cycle_entries in sorted(by_process_cycle.items()):
                pid_runs = _pid_run_payloads(cycle_entries)
                observed_pids = _ordered_unique([run["pid"] for run in pid_runs])
                if len(observed_pids) <= 1:
                    continue

                interval_payloads: list[dict[str, Any]] = []
                covered_refs: list[dict[str, Any]] = []
                for old_run, new_run in zip(pid_runs, pid_runs[1:]):
                    if old_run["pid"] == new_run["pid"]:
                        continue
                    left_time = old_run["last_seen"]
                    right_time = new_run["first_seen"]
                    covered = [
                        _boundary_ref(
                            boundary,
                            inherited=inherited,
                            target_scope=scope,
                            target_cpu_id=cpu_id,
                            include_support=True,
                        )
                        for boundary, inherited in boundary_items
                        if left_time < boundary.timestamp <= right_time
                    ]
                    covered_refs.extend(covered)
                    interval_payloads.append({
                        "old_pid": old_run["pid"],
                        "new_pid": new_run["pid"],
                        "left_open_time": left_time,
                        "right_closed_time": right_time,
                        "covered_boundaries": covered,
                    })

                cycle = cycle_lookup.get((scope, slot, cpu_id, cycle_index))
                cycle_window = {
                    "start_time": cycle.start_time if cycle else None,
                    "end_time": cycle.end_time if cycle else None,
                    "next_boundary_time": cycle.next_boundary_time if cycle else None,
                }
                pids_text = ",".join(observed_pids)
                scope_text = _format_scope_text(scope, cpu_id)
                cycle_ref = _cycle_ref(scope, slot, cpu_id, cycle_index)
                issues.append(LifecycleIssue(
                    type="reliable_process_multiple_pid_in_cycle",
                    type_label_zh=_ISSUE_TYPE_LABEL_ZH["reliable_process_multiple_pid_in_cycle"],
                    severity="error",
                    severity_label_zh=_SEVERITY_LABEL_ZH["error"],
                    scope=scope,
                    scope_label_zh=_scope_label(scope),
                    slot=slot,
                    cpu_id=cpu_id,
                    related_process=process_name,
                    related_boundaries=covered_refs,
                    affected_cycles=[cycle_ref],
                    observed_pids=observed_pids,
                    cycle_window=cycle_window,
                    pid_runs=pid_runs,
                    expected_boundary_intervals=interval_payloads,
                    covered_boundaries=covered_refs,
                    title_zh="可靠进程同一生命周期内出现多个 PID",
                    rule_zh="可靠进程不会在同一个生命周期内独立重启。",
                    facts_zh=(
                        f"{process_name} 在 slot={slot} {scope_text} cycle={cycle_index} "
                        f"内出现 PID {pids_text}。"
                    ),
                    current_result_zh=(
                        "final effective boundaries 没有把这些 PID run 切到不同生命周期。"
                    ),
                    conflict_reason_zh=(
                        "同一生命周期内可靠进程只能有一个 PID；多个 PID 表示该切分结果不可靠。"
                    ),
                    impact_zh=(
                        f"slot={slot} {scope_text} cycle={cycle_index} 标记 lifecycle_reliable=false。"
                    ),
                    action_zh="仅标记不可靠，不自动补切、删除或移动边界。",
                    explanation_zh=(
                        f"可靠进程 {process_name} 在同一个生命周期 cycle={cycle_index} "
                        f"内出现多个 PID ({pids_text})，与 v2 可靠进程生命周期绑定规则冲突。"
                    ),
                ))
        return issues

    def _find_same_pid_issues(
        self,
        entries: list[MechLogEntry],
        effective_by_scope: dict[ScopeKey, list[tuple[LifecycleBoundary, bool]]],
    ) -> list[LifecycleIssue]:
        issues: list[LifecycleIssue] = []
        for key, boundary_items in effective_by_scope.items():
            scope, slot, cpu_id = key
            boundaries = [boundary.timestamp for boundary, _ in boundary_items]
            if not boundaries:
                continue
            by_process_pid: dict[tuple[str, str], dict[int, list[MechLogEntry]]] = defaultdict(
                lambda: defaultdict(list)
            )
            for entry in entries:
                entry_scope = "cpu" if entry.cpu_id else "board"
                if (
                    entry_scope != scope
                    or entry.slot != slot
                    or (entry.cpu_id or None) != cpu_id
                    or not entry.pid
                    or entry.timestamp is None
                    or entry.process_name in self._multi_instance
                ):
                    continue
                cycle_index = self._cycle_index(entry.timestamp, boundaries)
                by_process_pid[(entry.process_name, entry.pid)][cycle_index].append(entry)

            for (process_name, pid), by_cycle in sorted(by_process_pid.items()):
                ordered = sorted(by_cycle)
                pairs = []
                for left, right in zip(ordered, ordered[1:]):
                    if right == left + 1:
                        left_entry = max(by_cycle[left], key=_entry_sort_key)
                        right_entry = min(by_cycle[right], key=_entry_sort_key)
                        boundary_ref = None
                        if left < len(boundary_items):
                            boundary, inherited = boundary_items[left]
                            boundary_ref = _boundary_ref(
                                boundary,
                                inherited=inherited,
                                target_scope=scope,
                                target_cpu_id=cpu_id,
                                include_support=True,
                            )
                        pairs.append({
                            "scope_key": _scope_key_dict(scope, slot, cpu_id),
                            "left_cycle_index": left,
                            "right_cycle_index": right,
                            "boundary_timestamp": boundaries[left],
                            "before_seen": left_entry.timestamp,
                            "after_seen": right_entry.timestamp,
                            "before_raw": left_entry.raw,
                            "after_raw": right_entry.raw,
                            "boundary": boundary_ref,
                        })
                if not pairs:
                    continue
                affected_cycles = [
                    _cycle_ref(scope, slot, cpu_id, index)
                    for pair in pairs
                    for index in (pair["left_cycle_index"], pair["right_cycle_index"])
                ]
                issues.append(LifecycleIssue(
                    type="same_pid_single_boundary_conflict",
                    type_label_zh=_ISSUE_TYPE_LABEL_ZH["same_pid_single_boundary_conflict"],
                    severity="error",
                    severity_label_zh=_SEVERITY_LABEL_ZH["error"],
                    scope=scope,
                    scope_label_zh=_scope_label(scope),
                    slot=slot,
                    cpu_id=cpu_id,
                    related_process=f"{process_name}-{pid}",
                    related_boundaries=[
                        pair["boundary"] or {"timestamp": pair["boundary_timestamp"]}
                        for pair in pairs
                    ],
                    affected_cycles=_dedupe_dicts(affected_cycles),
                    conflicting_cycle_pairs=pairs,
                    title_zh="唯一进程 same PID 跨相邻生命周期",
                    rule_zh="唯一进程同 PID 不允许跨相邻生命周期。",
                    facts_zh=(
                        f"{process_name}-{pid} 出现在 slot={slot} "
                        f"{_format_scope_text(scope, cpu_id)} 的相邻 cycle。"
                    ),
                    current_result_zh="final effective boundaries 将同一 PID 分到了相邻生命周期。",
                    conflict_reason_zh="同一次生命周期重启前后，唯一进程如果两边都被拉起，PID 必须不同。",
                    impact_zh=(
                        f"slot={slot} {_format_scope_text(scope, cpu_id)} "
                        "相关 cycle 标记 lifecycle_reliable=false。"
                    ),
                    action_zh="仅标记不可靠，不自动新增、删除或移动边界。",
                    explanation_zh=(
                        f"唯一进程 {process_name}-{pid} 出现在相邻 cycle，"
                        "与 v2 same PID 一致性规则冲突；系统只标记不可靠，不自动补切或移动边界。"
                    ),
                ))
        return issues

    def _attach_invalid_evidence_cycles(
        self,
        issues: list[LifecycleIssue],
        effective_by_scope: dict[ScopeKey, list[tuple[LifecycleBoundary, bool]]],
    ) -> None:
        for issue in issues:
            if (
                issue.type != "invalid_lifecycle_evidence"
                or issue.observed_time is None
                or issue.affected_cycles
            ):
                continue
            key = (issue.scope, issue.slot, issue.cpu_id)
            if key not in effective_by_scope:
                continue
            boundaries = [
                boundary.timestamp
                for boundary, _inherited in effective_by_scope.get(key, [])
            ]
            cycle_index = self._cycle_index(issue.observed_time, boundaries)
            issue.affected_cycles.append(
                _cycle_ref(issue.scope, issue.slot, issue.cpu_id, cycle_index)
            )

    def _invalid_evidence_issue(
        self,
        entry: MechLogEntry,
        *,
        scope: str,
        reason: str,
    ) -> LifecycleIssue:
        cpu_id = entry.cpu_id or None
        scope_text = _format_scope_text(scope, cpu_id)
        timestamp_text = entry.timestamp.isoformat() if entry.timestamp else "<空>"
        return LifecycleIssue(
            type="invalid_lifecycle_evidence",
            type_label_zh=_ISSUE_TYPE_LABEL_ZH["invalid_lifecycle_evidence"],
            severity="error",
            severity_label_zh=_SEVERITY_LABEL_ZH["error"],
            scope=scope,
            scope_label_zh=_scope_label(scope),
            slot=entry.slot,
            cpu_id=cpu_id,
            related_process=entry.process_name,
            observed_time=entry.timestamp,
            source=entry.source,
            source_file=entry.source_file,
            raw_excerpt=entry.raw,
            reason_zh=reason,
            title_zh="生命周期证据结构无效",
            rule_zh="正向边界证据必须包含可解析 timestamp、必要 PID 或 journal 序号，并形成 old_observed < boundary <= new_observed 的有效区间。",
            facts_zh=(
                f"{reason} slot={entry.slot} {scope_text} 进程={entry.process_name} "
                f"PID={entry.pid or '<空>'} sequence={entry.sequence} timestamp={timestamp_text}。"
            ),
            current_result_zh="该日志无法构造成可求解的正向生命周期边界约束。",
            conflict_reason_zh="输入或解析结果缺少生命周期切分所需字段，或边界证据的时间区间不满足正向顺序。",
            impact_zh=f"slot={entry.slot} {scope_text} 标记 lifecycle_reliable=false；若能定位到 cycle，则同步标记该 cycle 不可靠。",
            action_zh="记录 invalid_lifecycle_evidence，不进入正常边界求解，不自动补切或移动边界。",
            explanation_zh=(
                f"{reason} 进程={entry.process_name} PID={entry.pid or '<空>'}，"
                f"timestamp={timestamp_text}。"
            ),
        )

    def _propagate_reliability(self, result: LifecycleSplitResult) -> None:
        error_issues = [issue for issue in result.issues if issue.severity == "error"]
        if not error_issues:
            return

        result.lifecycle_reliable = False
        unreliable_scopes = {
            (issue.scope, issue.slot, issue.cpu_id) for issue in error_issues
        }
        cpu_slot_wide_unreliable = {
            issue.slot
            for issue in error_issues
            if issue.scope == "cpu" and issue.cpu_id is None
        }
        unreliable_cycles = {
            (
                cycle.get("scope"),
                cycle.get("slot"),
                cycle.get("cpu_id"),
                cycle.get("cycle_index"),
            )
            for issue in error_issues
            for cycle in issue.affected_cycles
        }
        for scope_result in result.scopes:
            if (
                (scope_result.scope, scope_result.slot, scope_result.cpu_id) in unreliable_scopes
                or (
                    scope_result.scope == "cpu"
                    and scope_result.slot in cpu_slot_wide_unreliable
                )
            ):
                scope_result.lifecycle_reliable = False
        for cycle in result.cycles:
            if (
                cycle.scope,
                cycle.slot,
                cycle.cpu_id,
                cycle.cycle_index,
            ) in unreliable_cycles or (
                cycle.scope == "cpu"
                and cycle.slot in cpu_slot_wide_unreliable
            ):
                cycle.lifecycle_reliable = False

    def _boundary_times_for_scope(
        self,
        result: LifecycleSplitResult,
        key: ScopeKey,
    ) -> list[datetime]:
        scope_result = next(
            (
                scope
                for scope in result.scopes
                if (
                    scope.scope == key[0]
                    and scope.slot == key[1]
                    and scope.cpu_id == key[2]
                )
            ),
            None,
        )
        if scope_result is None:
            return []
        return [item["timestamp"] for item in scope_result.effective_boundaries]

    def _split_traces_for_scope(
        self,
        result: LifecycleSplitResult,
        key: ScopeKey,
        cycle_index: int,
    ) -> list[MechCycleSplitTrace]:
        if cycle_index == 0:
            return []
        boundaries = self._boundary_times_for_scope(result, key)
        boundary_time = boundaries[cycle_index - 1] if cycle_index - 1 < len(boundaries) else None
        if boundary_time is None:
            return []
        boundary = next((item for item in result.boundaries if item.timestamp == boundary_time), None)
        return [
            MechCycleSplitTrace(
                timestamp=boundary_time,
                reason="lifecycle_split_v2",
                cpu_id=key[2] or "",
                indicator=(boundary.type if boundary else "lifecycle_split_v2"),
                detail="v2 lifecycle boundary",
            )
        ]

    @staticmethod
    def _cycle_index(timestamp: datetime | None, boundaries: list[datetime]) -> int:
        if timestamp is None:
            return 0
        return bisect_right(boundaries, timestamp)


def _norm(value: str) -> str:
    return value.casefold()


def _parse_reliable_processes(raw: Any, *, base_path: str) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(name) for name in raw]
    if isinstance(raw, dict):
        unknown_keys = sorted(str(key) for key in raw if key not in {"board", "cpu"})
        if unknown_keys:
            raise ValueError(
                f"{base_path} has unsupported legacy keys: {unknown_keys}; expected board/cpu"
            )
        merged: list[str] = []
        for key in ("board", "cpu"):
            value = raw.get(key, [])
            if value is None:
                continue
            if not isinstance(value, list):
                raise ValueError(f"{base_path}.{key} must be a list")
            merged.extend(str(name) for name in value)
        return _ordered_unique(merged)
    raise ValueError(f"{base_path} must be a list or legacy object")


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        marker = _norm(value)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result


def _format_scope_text(scope: str, cpu_id: str | None) -> str:
    if scope == "cpu" and cpu_id:
        return f"cpu_{cpu_id}"
    return scope


def _scope_label(scope: str) -> str:
    return _SCOPE_LABEL_ZH.get(scope, scope)


def _scope_key_dict(scope: str, slot: str, cpu_id: str | None) -> dict[str, Any]:
    return {"scope": scope, "slot": slot, "cpu_id": cpu_id}


def _scope_ref(
    scope: str,
    slot: str,
    cpu_id: str | None,
    *,
    inherited: bool,
) -> dict[str, Any]:
    return {
        "scope": scope,
        "slot": slot,
        "cpu_id": cpu_id,
        "inherited": inherited,
    }


def _boundary_ref(
    boundary: LifecycleBoundary,
    *,
    inherited: bool,
    target_scope: str | None = None,
    target_cpu_id: str | None = None,
    include_support: bool = False,
) -> dict[str, Any]:
    scope = target_scope if inherited else boundary.origin_scope
    cpu_id = target_cpu_id if inherited else boundary.cpu_id
    payload = {
        "id": boundary.id,
        "origin_scope": boundary.origin_scope,
        "scope": scope,
        "slot": boundary.slot,
        "cpu_id": cpu_id,
        "timestamp": boundary.timestamp,
        "inherited": inherited,
        "type": boundary.type,
    }
    if include_support:
        payload["support_evidence"] = list(boundary.support_evidence)
    return payload


def _cycle_ref(
    scope: str,
    slot: str,
    cpu_id: str | None,
    cycle_index: int,
) -> dict[str, Any]:
    return {
        "scope": scope,
        "slot": slot,
        "cpu_id": cpu_id,
        "cycle_index": cycle_index,
    }


def _entry_sort_key(entry: MechLogEntry) -> tuple[Any, ...]:
    return (
        entry.timestamp or datetime.min,
        entry.sequence,
        entry.source_file,
        entry.raw,
    )


def _entry_timestamp_key(entry: MechLogEntry) -> datetime:
    return entry.timestamp or datetime.min


def _compress_pid_runs(entries: list[MechLogEntry]) -> list[MechLogEntry]:
    runs: list[MechLogEntry] = []
    for entry in entries:
        if not runs or runs[-1].pid != entry.pid:
            runs.append(entry)
        else:
            runs[-1] = entry
    return runs


def _pid_run_payloads(entries: list[MechLogEntry]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for entry in sorted(entries, key=_entry_sort_key):
        if not runs or runs[-1]["pid"] != entry.pid:
            runs.append({
                "pid": entry.pid,
                "first_seen": entry.timestamp,
                "last_seen": entry.timestamp,
                "first_raw": entry.raw,
                "last_raw": entry.raw,
            })
        else:
            runs[-1]["last_seen"] = entry.timestamp
            runs[-1]["last_raw"] = entry.raw
    return runs


def _constraint_covers_time(
    constraint: PositiveBoundaryConstraint,
    timestamp: datetime,
) -> bool:
    return constraint.old_observed_time < timestamp <= constraint.new_observed_time


def _covering_boundary(
    boundaries: list[LifecycleBoundary],
    constraint: PositiveBoundaryConstraint,
) -> LifecycleBoundary | None:
    return next(
        (
            boundary
            for boundary in boundaries
            if _constraint_covers_time(constraint, boundary.timestamp)
        ),
        None,
    )


def _unique_effective_boundaries(
    boundaries: list[tuple[LifecycleBoundary, bool]],
) -> list[tuple[LifecycleBoundary, bool]]:
    by_time: dict[datetime, tuple[LifecycleBoundary, bool]] = {}
    for boundary, inherited in sorted(
        boundaries,
        key=lambda item: (item[0].timestamp, 0 if item[1] else 1),
    ):
        if boundary.timestamp not in by_time:
            by_time[boundary.timestamp] = (boundary, inherited)
    return list(by_time.values())


def _entry_time_bounds(entries: list[MechLogEntry]) -> tuple[datetime | None, datetime | None]:
    stamps = sorted(entry.timestamp for entry in entries if entry.timestamp)
    if not stamps:
        return None, None
    return stamps[0], stamps[-1]


def _format_cycle_dir(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return "unknown"
    return f"{start:%Y%m%d%H%M%S}-{end:%Y%m%d%H%M%S}"


def _build_process_lifecycles(entries: list[MechLogEntry]) -> list[MechProcessLifecycle]:
    by_key: dict[tuple[str, str], list[MechLogEntry]] = defaultdict(list)
    for entry in entries:
        by_key[(entry.process_name, entry.pid)].append(entry)

    processes: list[MechProcessLifecycle] = []
    for (process_name, pid), logs in sorted(by_key.items()):
        logs.sort(key=_entry_sort_key)
        processes.append(MechProcessLifecycle(
            process_name=process_name,
            pid=pid,
            logs=logs,
            total_count=len(logs),
            missing_sequences=_missing_sequences(logs),
        ))
    return processes


def _missing_sequences(logs: list[MechLogEntry]) -> list[int]:
    seqs = sorted({entry.sequence for entry in logs if entry.sequence})
    if not seqs:
        return []
    return [seq for seq in range(seqs[0], seqs[-1] + 1) if seq not in seqs]


def _dedupe_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[tuple[str, Any], ...]] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        marker = tuple(sorted(item.items()))
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(item)
    return unique
