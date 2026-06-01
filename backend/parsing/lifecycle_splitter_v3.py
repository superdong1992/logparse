"""Lifecycle split interval v3.

V3 treats a 30 second silent gap as a candidate split only.  Adjacent
candidate segments are merged again when the reliable-process PID evidence
still looks like a single lifecycle.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.models import (
    MechBoardCycle,
    MechCpuCycle,
    MechCycleSplitTrace,
    MechLogEntry,
)
from backend.parsing.lifecycle_splitter import (
    LifecycleSplitConfig,
    _build_process_lifecycles,
    _entry_sort_key,
    _entry_time_bounds,
    _format_cycle_dir,
    _norm,
)


SILENT_GAP_SECONDS = 30
logger = logging.getLogger(__name__)


class LifecycleV3CandidateSegment(BaseModel):
    id: str
    scope: str
    scope_label_zh: str
    slot: str
    cpu_id: str | None = None
    parent_lifecycle_id: str = ""
    candidate_index: int
    start_time: datetime
    end_time: datetime
    log_count: int = 0


class LifecycleV3Lifecycle(BaseModel):
    id: str
    scope: str
    scope_label_zh: str
    slot: str
    cpu_id: str | None = None
    parent_lifecycle_id: str = ""
    lifecycle_index: int
    candidate_indices: list[int] = Field(default_factory=list)
    start_time: datetime
    end_time: datetime
    log_count: int = 0
    lifecycle_reliable: bool = True


class LifecycleV3JournalEvidence(BaseModel):
    support_type: str
    support_type_label_zh: str
    scope: str
    scope_label_zh: str
    slot: str
    cpu_id: str | None = None
    old_sequence: int
    new_sequence: int
    old_observed_time: datetime
    new_observed_time: datetime
    old_raw: str = ""
    new_raw: str = ""
    explanation_zh: str


class LifecycleV3MergeDecision(BaseModel):
    scope: str
    scope_label_zh: str
    slot: str
    cpu_id: str | None = None
    left_candidate_indices: list[int] = Field(default_factory=list)
    right_candidate_indices: list[int] = Field(default_factory=list)
    left_end_time: datetime
    right_start_time: datetime
    silent_gap_seconds: float
    decision: str
    decision_label_zh: str
    blocking_reason: str = ""
    whitelist_pid_counts: list[dict[str, Any]] = Field(default_factory=list)
    journal_evidence: list[dict[str, Any]] = Field(default_factory=list)
    reason_zh: str


class LifecycleV3Issue(BaseModel):
    type: str
    type_label_zh: str
    severity: str
    severity_label_zh: str
    scope: str
    scope_label_zh: str
    slot: str
    cpu_id: str | None = None
    related_process: str = ""
    observed_pids: list[str] = Field(default_factory=list)
    affected_lifecycles: list[dict[str, Any]] = Field(default_factory=list)
    title_zh: str
    explanation_zh: str
    reason_zh: str = ""


class LifecycleV3Result(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    algorithm: str = "interval_v3"
    candidate_segments: list[LifecycleV3CandidateSegment] = Field(default_factory=list)
    merge_decisions: list[LifecycleV3MergeDecision] = Field(default_factory=list)
    lifecycles: list[LifecycleV3Lifecycle] = Field(default_factory=list)
    journal_evidence: list[LifecycleV3JournalEvidence] = Field(default_factory=list)
    issues: list[LifecycleV3Issue] = Field(default_factory=list)
    lifecycle_reliable: bool = True
    canonical_entries: list[MechLogEntry] = Field(default_factory=list, exclude=True)


@dataclass
class _WorkingSegment:
    scope: str
    slot: str
    cpu_id: str | None
    parent_lifecycle_id: str
    candidate_indices: list[int]
    entries: list[MechLogEntry]

    @property
    def start_time(self) -> datetime:
        return self.entries[0].timestamp  # type: ignore[return-value]

    @property
    def end_time(self) -> datetime:
        return self.entries[-1].timestamp  # type: ignore[return-value]


class LifecycleSplitterV3:
    """Split mechanism logs with interval_v3 lifecycle aggregation."""

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

    def split(self, entries: list[MechLogEntry]) -> LifecycleV3Result:
        canonical_entries = self._canonicalize_entries(entries)
        timestamped = [entry for entry in canonical_entries if entry.timestamp is not None]
        if not timestamped:
            return LifecycleV3Result(canonical_entries=canonical_entries)

        candidate_segments: list[LifecycleV3CandidateSegment] = []
        merge_decisions: list[LifecycleV3MergeDecision] = []
        journal_evidence: list[LifecycleV3JournalEvidence] = []
        lifecycles: list[LifecycleV3Lifecycle] = []

        for slot in sorted({entry.slot for entry in timestamped}):
            slot_entries = [entry for entry in timestamped if entry.slot == slot]
            board_candidates = self._candidate_segments(
                slot_entries,
                scope="board",
                slot=slot,
                cpu_id=None,
            )
            candidate_segments.extend(self._candidate_payloads(board_candidates))
            board_segments = self._merge_candidates(
                board_candidates,
                merge_decisions=merge_decisions,
                journal_evidence=journal_evidence,
            )
            board_lifecycles = self._lifecycle_payloads(board_segments)
            lifecycles.extend(board_lifecycles)

            for board_segment, board_lifecycle in zip(board_segments, board_lifecycles):
                cpu_ids = sorted({
                    entry.cpu_id for entry in board_segment.entries if entry.cpu_id
                })
                for cpu_id in cpu_ids:
                    cpu_entries = [
                        entry
                        for entry in board_segment.entries
                        if entry.cpu_id == cpu_id
                    ]
                    cpu_candidates = self._candidate_segments(
                        cpu_entries,
                        scope="cpu",
                        slot=slot,
                        cpu_id=cpu_id,
                        parent_lifecycle_id=board_lifecycle.id,
                    )
                    candidate_segments.extend(self._candidate_payloads(cpu_candidates))
                    cpu_segments = self._merge_candidates(
                        cpu_candidates,
                        merge_decisions=merge_decisions,
                        journal_evidence=journal_evidence,
                    )
                    lifecycles.extend(self._lifecycle_payloads(cpu_segments))

        issues = self._find_reliability_issues(lifecycles, canonical_entries)
        unreliable_lifecycle_ids = {
            item.get("id")
            for issue in issues
            if issue.severity == "error"
            for item in issue.affected_lifecycles
        }
        for lifecycle in lifecycles:
            if lifecycle.id in unreliable_lifecycle_ids:
                lifecycle.lifecycle_reliable = False

        return LifecycleV3Result(
            candidate_segments=candidate_segments,
            merge_decisions=merge_decisions,
            lifecycles=lifecycles,
            journal_evidence=journal_evidence,
            issues=issues,
            lifecycle_reliable=not any(issue.severity == "error" for issue in issues),
            canonical_entries=canonical_entries,
        )

    def build_board_cycles(self, result: LifecycleV3Result) -> list[MechBoardCycle]:
        entries = [entry for entry in result.canonical_entries if entry.timestamp is not None]
        board_cycles: list[MechBoardCycle] = []
        board_cycles_by_id: dict[str, MechBoardCycle] = {}

        board_lifecycles = [
            lifecycle for lifecycle in result.lifecycles if lifecycle.scope == "board"
        ]
        for lifecycle in board_lifecycles:
            cycle_entries = self._entries_for_lifecycle(entries, lifecycle)
            board_entries = [entry for entry in cycle_entries if not entry.cpu_id]
            start, end = _entry_time_bounds(cycle_entries)
            board_cycle = MechBoardCycle(
                dir_name=_format_cycle_dir(start, end),
                start_time=start,
                end_time=end,
                split_traces=self._split_traces_for_lifecycle(lifecycle),
                processes=_build_process_lifecycles(board_entries),
            )
            board_cycles.append(board_cycle)
            board_cycles_by_id[lifecycle.id] = board_cycle

        for lifecycle in [
            lifecycle for lifecycle in result.lifecycles if lifecycle.scope == "cpu"
        ]:
            parent = self._parent_board_lifecycle(board_lifecycles, lifecycle)
            if parent is None:
                continue
            board_cycle = board_cycles_by_id.get(parent.id)
            if board_cycle is None:
                continue
            cpu_entries = self._entries_for_lifecycle(entries, lifecycle)
            start, end = _entry_time_bounds(cpu_entries)
            board_cycle.cpu_cycles.append(
                MechCpuCycle(
                    cpu_id=lifecycle.cpu_id or "",
                    dir_name=_format_cycle_dir(start, end),
                    start_time=start,
                    end_time=end,
                    split_traces=self._split_traces_for_lifecycle(lifecycle),
                    processes=_build_process_lifecycles(cpu_entries),
                )
            )

        self._log_cycle_summary(board_cycles)
        return board_cycles

    def _log_cycle_summary(self, board_cycles: list[MechBoardCycle]) -> None:
        logger.info("最终切分结果: %d 个周期", len(board_cycles))
        for index, cycle in enumerate(board_cycles):
            logger.info(
                "  周期[%d]: %s, %d 个进程组",
                index,
                cycle.dir_name,
                len(cycle.processes),
            )
            for cpu_index, cpu_cycle in enumerate(cycle.cpu_cycles):
                logger.info(
                    "    CPU周期[%s/%d]: %s, %d 个进程组",
                    cpu_cycle.cpu_id,
                    cpu_index,
                    cpu_cycle.dir_name,
                    len(cpu_cycle.processes),
                )

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
        conflicts = reliable & multi
        if conflicts:
            raise ValueError(
                "invalid lifecycle_split config: each canonical process may appear in only one of "
                "reliable_processes, multi_instance_processes; "
                f"conflicts={sorted(conflicts)}"
            )

    def _candidate_segments(
        self,
        entries: list[MechLogEntry],
        *,
        scope: str,
        slot: str,
        cpu_id: str | None,
        parent_lifecycle_id: str = "",
    ) -> list[_WorkingSegment]:
        ordered = sorted(entries, key=_entry_sort_key)
        candidates: list[_WorkingSegment] = []
        current: list[MechLogEntry] = []
        for entry in ordered:
            if current:
                previous = current[-1]
                gap = (entry.timestamp - previous.timestamp).total_seconds()  # type: ignore[operator]
                if gap >= SILENT_GAP_SECONDS:
                    candidates.append(_WorkingSegment(
                        scope=scope,
                        slot=slot,
                        cpu_id=cpu_id,
                        parent_lifecycle_id=parent_lifecycle_id,
                        candidate_indices=[len(candidates)],
                        entries=current,
                    ))
                    current = []
            current.append(entry)
        if current:
            candidates.append(_WorkingSegment(
                scope=scope,
                slot=slot,
                cpu_id=cpu_id,
                parent_lifecycle_id=parent_lifecycle_id,
                candidate_indices=[len(candidates)],
                entries=current,
            ))
        return candidates

    def _merge_candidates(
        self,
        candidates: list[_WorkingSegment],
        *,
        merge_decisions: list[LifecycleV3MergeDecision],
        journal_evidence: list[LifecycleV3JournalEvidence],
    ) -> list[_WorkingSegment]:
        segments = list(candidates)
        index = 0
        while index < len(segments) - 1:
            left = segments[index]
            right = segments[index + 1]
            decision, evidence = self._merge_decision(left, right)
            merge_decisions.append(decision)
            journal_evidence.extend(evidence)
            if decision.decision == "merged":
                segments[index] = _WorkingSegment(
                    scope=left.scope,
                    slot=left.slot,
                    cpu_id=left.cpu_id,
                    parent_lifecycle_id=left.parent_lifecycle_id,
                    candidate_indices=left.candidate_indices + right.candidate_indices,
                    entries=sorted(left.entries + right.entries, key=_entry_sort_key),
                )
                del segments[index + 1]
                if index:
                    index -= 1
                continue
            index += 1
        return segments

    def _merge_decision(
        self,
        left: _WorkingSegment,
        right: _WorkingSegment,
    ) -> tuple[LifecycleV3MergeDecision, list[LifecycleV3JournalEvidence]]:
        gap = (right.start_time - left.end_time).total_seconds()
        left_evidence_entries = self._decision_evidence_entries(left)
        right_evidence_entries = self._decision_evidence_entries(right)
        pid_counts = self._reliable_pid_counts(left_evidence_entries + right_evidence_entries)
        journal_evidence = self._journal_wrap_evidence(
            left,
            right,
            left_entries=left_evidence_entries,
            right_entries=right_evidence_entries,
        )
        if journal_evidence:
            return (
                LifecycleV3MergeDecision(
                    scope=left.scope,
                    scope_label_zh=_scope_label(left.scope),
                    slot=left.slot,
                    cpu_id=left.cpu_id,
                    left_candidate_indices=left.candidate_indices,
                    right_candidate_indices=right.candidate_indices,
                    left_end_time=left.end_time,
                    right_start_time=right.start_time,
                    silent_gap_seconds=gap,
                    decision="kept_split",
                    decision_label_zh="保留切分",
                    blocking_reason="journal_wrap",
                    whitelist_pid_counts=pid_counts,
                    journal_evidence=[item.model_dump(mode="json") for item in journal_evidence],
                    reason_zh=(
                        "两个候选生命周期之间存在 journal 序号回绕，且回绕前日志在前段、"
                        "回绕后日志在后段，因此保留这条生命周期边界。"
                    ),
                ),
                journal_evidence,
            )

        conflicts = [item for item in pid_counts if len(item["pids"]) >= 2]
        if conflicts:
            conflict_text = "；".join(
                f"{item['process_name']} PID={','.join(item['pids'])}"
                for item in conflicts
            )
            return (
                LifecycleV3MergeDecision(
                    scope=left.scope,
                    scope_label_zh=_scope_label(left.scope),
                    slot=left.slot,
                    cpu_id=left.cpu_id,
                    left_candidate_indices=left.candidate_indices,
                    right_candidate_indices=right.candidate_indices,
                    left_end_time=left.end_time,
                    right_start_time=right.start_time,
                    silent_gap_seconds=gap,
                    decision="kept_split",
                    decision_label_zh="保留切分",
                    blocking_reason="reliable_pid_conflict",
                    whitelist_pid_counts=pid_counts,
                    reason_zh=(
                        f"合并后白名单进程出现多个 PID：{conflict_text}。"
                        "这更像跨重启边界，保留候选切分。"
                    ),
                ),
                [],
            )

        if pid_counts:
            reason = (
                "所有白名单进程 PID 数均不超过 1，没有白名单进程 PID 冲突，"
                "判断为同一生命周期内日志分段打印。"
            )
        else:
            reason = (
                "两个候选段都没有白名单进程 PID 观测，允许聚合，"
                "避免普通日志分段打印导致误切。"
            )
        return (
            LifecycleV3MergeDecision(
                scope=left.scope,
                scope_label_zh=_scope_label(left.scope),
                slot=left.slot,
                cpu_id=left.cpu_id,
                left_candidate_indices=left.candidate_indices,
                right_candidate_indices=right.candidate_indices,
                left_end_time=left.end_time,
                right_start_time=right.start_time,
                silent_gap_seconds=gap,
                decision="merged",
                decision_label_zh="聚合",
                whitelist_pid_counts=pid_counts,
                reason_zh=reason,
            ),
            [],
        )

    def _reliable_pid_counts(self, entries: list[MechLogEntry]) -> list[dict[str, Any]]:
        counts: list[dict[str, Any]] = []
        for process_name in sorted(self._reliable_processes):
            if process_name in self._multi_instance:
                continue
            pids = sorted({
                entry.pid
                for entry in entries
                if entry.process_name == process_name and entry.pid
            })
            if pids:
                counts.append({
                    "process_name": process_name,
                    "pids": pids,
                    "count": len(pids),
                })
        return counts

    def _journal_wrap_evidence(
        self,
        left: _WorkingSegment,
        right: _WorkingSegment,
        *,
        left_entries: list[MechLogEntry],
        right_entries: list[MechLogEntry],
    ) -> list[LifecycleV3JournalEvidence]:
        left_ids = {id(entry) for entry in left_entries}
        right_ids = {id(entry) for entry in right_entries}
        journal_entries = sorted(
            [
                entry
                for entry in left_entries + right_entries
                if entry.source == "journal" and entry.sequence > 0 and entry.timestamp
            ],
            key=_entry_sort_key,
        )
        evidence: list[LifecycleV3JournalEvidence] = []
        for old_entry, new_entry in zip(journal_entries, journal_entries[1:]):
            if id(old_entry) not in left_ids or id(new_entry) not in right_ids:
                continue
            if new_entry.sequence >= old_entry.sequence:
                continue
            evidence.append(
                LifecycleV3JournalEvidence(
                    support_type="boundary_support",
                    support_type_label_zh="边界证据",
                    scope=left.scope,
                    scope_label_zh=_scope_label(left.scope),
                    slot=left.slot,
                    cpu_id=left.cpu_id,
                    old_sequence=old_entry.sequence,
                    new_sequence=new_entry.sequence,
                    old_observed_time=old_entry.timestamp,
                    new_observed_time=new_entry.timestamp,
                    old_raw=old_entry.raw,
                    new_raw=new_entry.raw,
                    explanation_zh=(
                        "journal 回绕跨越相邻候选生命周期：回绕前日志位于前段，"
                        "回绕后日志位于后段，因此这条候选边界有可靠证据支撑。"
                    ),
                )
            )
        return evidence

    def _decision_evidence_entries(self, segment: _WorkingSegment) -> list[MechLogEntry]:
        if segment.scope == "board":
            return [entry for entry in segment.entries if not entry.cpu_id]
        return list(segment.entries)

    def _candidate_payloads(
        self,
        segments: list[_WorkingSegment],
    ) -> list[LifecycleV3CandidateSegment]:
        return [
            LifecycleV3CandidateSegment(
                id=_segment_id(segment, segment.candidate_indices),
                scope=segment.scope,
                scope_label_zh=_scope_label(segment.scope),
                slot=segment.slot,
                cpu_id=segment.cpu_id,
                parent_lifecycle_id=segment.parent_lifecycle_id,
                candidate_index=segment.candidate_indices[0],
                start_time=segment.start_time,
                end_time=segment.end_time,
                log_count=len(segment.entries),
            )
            for segment in segments
        ]

    def _lifecycle_payloads(
        self,
        segments: list[_WorkingSegment],
    ) -> list[LifecycleV3Lifecycle]:
        lifecycles: list[LifecycleV3Lifecycle] = []
        for lifecycle_index, segment in enumerate(segments):
            lifecycles.append(
                LifecycleV3Lifecycle(
                    id=_lifecycle_id(segment, lifecycle_index),
                    scope=segment.scope,
                    scope_label_zh=_scope_label(segment.scope),
                    slot=segment.slot,
                    cpu_id=segment.cpu_id,
                    parent_lifecycle_id=segment.parent_lifecycle_id,
                    lifecycle_index=lifecycle_index,
                    candidate_indices=segment.candidate_indices,
                    start_time=segment.start_time,
                    end_time=segment.end_time,
                    log_count=len(segment.entries),
                )
            )
        return lifecycles

    def _find_reliability_issues(
        self,
        lifecycles: list[LifecycleV3Lifecycle],
        entries: list[MechLogEntry],
    ) -> list[LifecycleV3Issue]:
        issues: list[LifecycleV3Issue] = []
        for lifecycle in lifecycles:
            lifecycle_entries = self._entries_for_lifecycle(entries, lifecycle)
            for pid_count in self._reliable_pid_counts(lifecycle_entries):
                if len(pid_count["pids"]) <= 1:
                    continue
                issues.append(LifecycleV3Issue(
                    type="reliable_process_multiple_pid_in_lifecycle",
                    type_label_zh="白名单进程同一生命周期内出现多个 PID",
                    severity="error",
                    severity_label_zh="错误",
                    scope=lifecycle.scope,
                    scope_label_zh=lifecycle.scope_label_zh,
                    slot=lifecycle.slot,
                    cpu_id=lifecycle.cpu_id,
                    related_process=pid_count["process_name"],
                    observed_pids=pid_count["pids"],
                    affected_lifecycles=[_lifecycle_ref(lifecycle)],
                    title_zh="最终生命周期内仍存在白名单 PID 冲突",
                    explanation_zh=(
                        "V3 不会在最终结果阶段自动补切生命周期；该问题表示初始候选段内部"
                        "已经包含多个白名单 PID，需要检查日志是否缺失关键边界。"
                    ),
                    reason_zh="同一最终生命周期内白名单进程出现多个不同 PID。",
                ))

        lifecycles_by_scope: dict[tuple[str, str, str | None, str], list[LifecycleV3Lifecycle]] = defaultdict(list)
        for lifecycle in lifecycles:
            lifecycles_by_scope[
                (
                    lifecycle.scope,
                    lifecycle.slot,
                    lifecycle.cpu_id,
                    lifecycle.parent_lifecycle_id,
                )
            ].append(lifecycle)
        for scope_lifecycles in lifecycles_by_scope.values():
            ordered = sorted(scope_lifecycles, key=lambda item: item.lifecycle_index)
            for left, right in zip(ordered, ordered[1:]):
                same_pid = self._same_process_pid_across_boundary(entries, left, right)
                for process_name, pid in same_pid:
                    issues.append(LifecycleV3Issue(
                        type="pid_reuse_assumption_violation",
                        type_label_zh="PID 复用假设被破坏",
                        severity="warning",
                        severity_label_zh="警告",
                        scope=left.scope,
                        scope_label_zh=left.scope_label_zh,
                        slot=left.slot,
                        cpu_id=left.cpu_id,
                        related_process=process_name,
                        observed_pids=[pid],
                        affected_lifecycles=[
                            _lifecycle_ref(left),
                            _lifecycle_ref(right),
                        ],
                        title_zh="保留边界两侧出现同名同 PID",
                        explanation_zh=(
                            "最终生命周期边界两侧仍观察到同名同 PID。V3 保留边界，"
                            "但提示 PID 复用假设可能被破坏。"
                        ),
                        reason_zh="相邻最终生命周期边界两侧存在相同 process_name + PID。",
                    ))
        return issues

    def _same_process_pid_across_boundary(
        self,
        entries: list[MechLogEntry],
        left: LifecycleV3Lifecycle,
        right: LifecycleV3Lifecycle,
    ) -> list[tuple[str, str]]:
        left_pairs = self._process_pid_pairs(self._entries_for_lifecycle(entries, left))
        right_pairs = self._process_pid_pairs(self._entries_for_lifecycle(entries, right))
        return sorted(left_pairs & right_pairs)

    def _process_pid_pairs(self, entries: list[MechLogEntry]) -> set[tuple[str, str]]:
        return {
            (entry.process_name, entry.pid)
            for entry in entries
            if entry.pid and entry.process_name not in self._multi_instance
        }

    def _entries_for_lifecycle(
        self,
        entries: list[MechLogEntry],
        lifecycle: LifecycleV3Lifecycle,
    ) -> list[MechLogEntry]:
        result: list[MechLogEntry] = []
        for entry in entries:
            if entry.timestamp is None or entry.slot != lifecycle.slot:
                continue
            if not (lifecycle.start_time <= entry.timestamp <= lifecycle.end_time):
                continue
            if lifecycle.scope == "cpu":
                if entry.cpu_id != lifecycle.cpu_id:
                    continue
            result.append(entry)
        return sorted(result, key=_entry_sort_key)

    def _parent_board_lifecycle(
        self,
        board_lifecycles: list[LifecycleV3Lifecycle],
        lifecycle: LifecycleV3Lifecycle,
    ) -> LifecycleV3Lifecycle | None:
        return next(
            (
                board
                for board in board_lifecycles
                if (
                    board.slot == lifecycle.slot
                    and board.start_time <= lifecycle.start_time
                    and lifecycle.end_time <= board.end_time
                )
            ),
            None,
        )

    def _split_traces_for_lifecycle(
        self,
        lifecycle: LifecycleV3Lifecycle,
    ) -> list[MechCycleSplitTrace]:
        if lifecycle.lifecycle_index == 0:
            return []
        return [
            MechCycleSplitTrace(
                timestamp=lifecycle.start_time,
                reason="lifecycle_split_v3",
                cpu_id=lifecycle.cpu_id or "",
                indicator="interval_v3",
                detail="v3 candidate split kept after whitelist/journal merge check",
            )
        ]


def _segment_id(segment: _WorkingSegment, indices: list[int]) -> str:
    joined = "-".join(str(index + 1) for index in indices)
    parent = segment.parent_lifecycle_id or "root"
    return f"{segment.scope}:{segment.slot}:{segment.cpu_id or 'board'}:{parent}:candidate:{joined}"


def _lifecycle_id(segment: _WorkingSegment, lifecycle_index: int) -> str:
    parent = segment.parent_lifecycle_id or "root"
    return (
        f"{segment.scope}:{segment.slot}:{segment.cpu_id or 'board'}:{parent}:"
        f"lifecycle:{lifecycle_index + 1}"
    )


def _lifecycle_ref(lifecycle: LifecycleV3Lifecycle) -> dict[str, Any]:
    return {
        "id": lifecycle.id,
        "scope": lifecycle.scope,
        "slot": lifecycle.slot,
        "cpu_id": lifecycle.cpu_id,
        "parent_lifecycle_id": lifecycle.parent_lifecycle_id,
        "lifecycle_index": lifecycle.lifecycle_index,
        "start_time": lifecycle.start_time,
        "end_time": lifecycle.end_time,
    }


def _scope_label(scope: str) -> str:
    if scope == "cpu":
        return "CPU"
    return "板卡"
