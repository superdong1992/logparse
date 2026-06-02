"""Module 2 mechanism plugin."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from backend.models import (
    LogEntry,
    MechBoardCycle,
    MechCpuCycle,
    MechLogEntry,
    MechProcessLifecycle,
    MechResult,
    MechSlotOutput,
    ParseResult,
)
from backend.parsing.file_iter import iter_log_entry_lines
from backend.plugins.mechanisms.base import MechanismModulePlugin

logger = logging.getLogger(__name__)
_NON_OVERLAP_EPSILON = timedelta(microseconds=1)


@dataclass
class _CycleMatch:
    board_cycle: MechBoardCycle | None = None
    cpu_cycle: MechCpuCycle | None = None
    reason: str = ""
    detail: str = ""


@dataclass
class _ProjectedCycleTarget:
    board_cycle: MechBoardCycle
    cpu_cycle: MechCpuCycle | None
    entries: list[MechLogEntry]
    start_time: datetime | None
    end_time: datetime | None


@dataclass
class _BoardProjection:
    board_cycle: MechBoardCycle
    entries: list[MechLogEntry]
    board_entries: list[MechLogEntry] | None = None
    cpu_unknown_by_id: dict[str, tuple[MechCpuCycle, list[MechLogEntry]]] | None = None


@dataclass
class _KnownBucketTarget:
    board_cycle: MechBoardCycle
    cpu_cycle: MechCpuCycle | None
    entries: list[MechLogEntry]


@dataclass
class _CandidateResolution:
    target: Any | None
    target_count: int
    admissible_count: int
    distance: float | None = None
    tie: bool = False
    selected_summary: str = ""
    detail: str = ""


@dataclass
class _NearestResolutionStats:
    known_process: int = 0
    projected: int = 0

    @property
    def total(self) -> int:
        return self.known_process + self.projected


class Module2Plugin(MechanismModulePlugin):
    """Diagnostic-only module that reuses another module's board cycles."""

    @classmethod
    def validate_config(cls, module_key: str, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        for field in ("module_name", "identifying_keyword", "depends_on_module", "diag_pattern"):
            if not config.get(field):
                errors.append(f"mechanism_modules.{module_key}.{field} 不能为空")

        pattern = config.get("diag_pattern")
        if pattern:
            try:
                diag_re = re.compile(pattern)
            except re.error as e:
                errors.append(f"mechanism_modules.{module_key}.diag_pattern 正则非法: {e}")
            else:
                required = {"Slot", "CPU_Id", "ProcessName", "Context"}
                missing = required - set(diag_re.groupindex)
                if missing:
                    errors.append(
                        f"mechanism_modules.{module_key}.diag_pattern 缺少命名组: {sorted(missing)}"
                    )

        return errors

    def parse(self, result: ParseResult) -> MechResult | None:
        errors = self.validate_config(self.module_key, self.config)
        if errors:
            result.errors.extend(errors)
            logger.warning("[%s] 配置校验失败: %s", self.module_key, errors)
            return None

        upstream = self._find_dependency(result)
        if upstream is None:
            msg = (
                f"{self.module_key}: depends_on_module={self.config['depends_on_module']!r} result not found"
            )
            result.errors.append(msg)
            logger.warning("[%s] 依赖未找到: %s", self.module_key, msg)
            return None

        entries = self._scan_diagnostic_entries(result)
        if not entries:
            logger.info("[%s] 未扫描到诊断日志条目 (keyword=%r, slots=%d)",
                        self.module_key,
                        self.config["identifying_keyword"],
                        len(result.diagnostic_slots))
            return None

        self._normalize_timezones(entries, upstream)

        return self._build_result(entries, upstream)

    def _find_dependency(self, result: ParseResult) -> MechResult | None:
        depends_on = self.config["depends_on_module"]
        for mech in result.mech_results:
            if mech.module_key == depends_on:
                return mech
        return None

    def _scan_diagnostic_entries(self, result: ParseResult) -> list[MechLogEntry]:
        diag_re = re.compile(self.config["diag_pattern"])
        keyword = str(self.config["identifying_keyword"])
        entries: list[MechLogEntry] = []

        for slot in result.diagnostic_slots:
            for log_entry in slot.diagnostic_logs:
                entries.extend(self._scan_log_entry(log_entry, slot.slot_id, diag_re, keyword))

        return entries

    def _normalize_timezones(self, entries: list[MechLogEntry], upstream: MechResult) -> None:
        tz = next(
            (e.timestamp.tzinfo for e in entries if e.timestamp and e.timestamp.tzinfo),
            None,
        )
        if tz is None:
            for slot in upstream.slots:
                for cycle in slot.board_cycles:
                    for t in (cycle.start_time, cycle.end_time):
                        if t and t.tzinfo:
                            tz = t.tzinfo
                            break
                    if tz:
                        break
                if tz:
                    break
        if tz:
            for entry in entries:
                if entry.timestamp and entry.timestamp.tzinfo is None:
                    entry.timestamp = entry.timestamp.replace(tzinfo=tz)
        else:
            logger.warning("[%s] 无法检测时区，周期匹配可能失败", self.module_key)

    def _scan_log_entry(
        self,
        log_entry: LogEntry,
        source_slot_id: str,
        diag_re: re.Pattern,
        keyword: str,
    ) -> list[MechLogEntry]:
        entries: list[MechLogEntry] = []
        for line in iter_log_entry_lines(log_entry):
            if keyword not in line:
                continue
            m = diag_re.search(line)
            if not m:
                continue

            slot = _extract_slot_id(m.group("Slot"))
            cpu_id = m.group("CPU_Id")
            if cpu_id == "0":
                cpu_id = ""
            process_name, pid = _parse_bracket_process_name(m.group("ProcessName"))
            context = m.group("Context")
            timestamp = self._extract_first_ts(line)
            source_file = f"slot_{source_slot_id}/{log_entry.name}"

            entries.append(MechLogEntry(
                timestamp=timestamp,
                source="diagnostic",
                source_file=source_file,
                slot=slot,
                cpu_id=cpu_id,
                process_name=process_name,
                pid=pid,
                context=context,
                sequence=0,
                raw=line.strip()[:500],
            ))

        return entries

    def _extract_first_ts(self, line: str) -> datetime | None:
        stamps = self.ts_extractor.extract_from_text(line)
        return stamps[0] if stamps else None

    def _build_result(self, entries: list[MechLogEntry], upstream: MechResult) -> MechResult:
        by_slot: dict[str, list[MechLogEntry]] = defaultdict(list)
        for entry in entries:
            by_slot[entry.slot].append(entry)

        mech_result = MechResult(module_name=self.config["module_name"], module_key=self.module_key)
        for slot_id, slot_entries in sorted(by_slot.items()):
            slot_output = MechSlotOutput(slot_id=slot_id)
            upstream_slot = _find_upstream_slot(upstream, slot_id)
            grouped, matches = _assign_entries_to_cycles(
                slot_entries,
                upstream_slot,
                available_slots=[slot.slot_id for slot in upstream.slots],
                module_key=self.module_key,
            )
            slot_output.board_cycles = _build_cycles(
                grouped,
                upstream_slot,
                matches,
                module_key=self.module_key,
            )
            mech_result.slots.append(slot_output)

        mech_result.diag_entry_count = len(entries)
        return mech_result


def _parse_bracket_process_name(raw: str) -> tuple[str, str]:
    m = re.match(r"^(?P<name>.+?)\[(?P<pid>\d+)\]$", raw)
    if not m:
        return raw, ""
    return m.group("name"), m.group("pid")


def _extract_slot_id(raw: str) -> str:
    """从 '框号/slot' 格式中提取 slot_id，纯数字则直接返回。

    TODO 临时规避：当前只取 '/' 后面的 slot 号，丢弃了前面的框号。
    正式方案应保留完整 '框号/slot' 语义，在周期匹配时按框号+slot
    联合定位上游模块的 SlotOutput。
    """
    if "/" in raw:
        return raw.rsplit("/", 1)[-1].strip()
    return raw.strip()


def _find_upstream_slot(upstream: MechResult, slot_id: str) -> MechSlotOutput | None:
    for slot in upstream.slots:
        if slot.slot_id == slot_id:
            return slot
    return None


def _assign_entries_to_cycles(
    entries: list[MechLogEntry],
    upstream_slot: MechSlotOutput | None,
    available_slots: list[str] | None = None,
    module_key: str = "module2",
) -> tuple[
    list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]],
    dict[int, _CycleMatch],
]:
    buckets: list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]] = []
    matches: dict[int, _CycleMatch] = {}
    nearest_stats = _NearestResolutionStats()
    unknown = MechBoardCycle(dir_name="unknown")

    for entry in entries:
        match = _find_matching_cycle(entry, upstream_slot, available_slots or [])
        matches[id(entry)] = match
        _log_successful_assignment_detail(module_key, entry, match)
        board_cycle = match.board_cycle
        cpu_cycle = match.cpu_cycle
        if board_cycle is None:
            board_cycle = unknown
        if entry.cpu_id and cpu_cycle is None:
            cpu_cycle = MechCpuCycle(
                cpu_id=entry.cpu_id,
                dir_name="unknown",
                start_time=board_cycle.start_time,
                end_time=board_cycle.end_time,
            )

        for existing_board, existing_cpu, existing_entries in buckets:
            if (
                existing_board.dir_name == board_cycle.dir_name
                and (existing_cpu.dir_name if existing_cpu else "") == (cpu_cycle.dir_name if cpu_cycle else "")
                and (existing_cpu.cpu_id if existing_cpu else "") == (cpu_cycle.cpu_id if cpu_cycle else "")
            ):
                existing_entries.append(entry)
                break
        else:
            buckets.append((board_cycle, cpu_cycle, [entry]))

    _merge_unknown_entries_into_unique_known_bucket(
        buckets,
        matches,
        upstream_slot,
        module_key,
        nearest_stats,
    )
    _merge_unknown_entries_into_unique_expanded_cycle_bucket(
        buckets,
        matches,
        upstream_slot,
        module_key,
        nearest_stats,
    )
    _log_nearest_resolution_summary(module_key, entries, nearest_stats)
    _log_unknown_assignments(module_key, buckets, matches)
    return buckets, matches


def _find_matching_cycle(
    entry: MechLogEntry,
    upstream_slot: MechSlotOutput | None,
    available_slots: list[str],
) -> _CycleMatch:
    if upstream_slot is None:
        return _CycleMatch(
            reason="no_upstream_slot",
            detail=f"upstream_slot_found=false available_slots={_format_list(available_slots)}",
        )
    if entry.timestamp is None:
        return _CycleMatch(
            reason="missing_timestamp",
            detail=_slot_detail(upstream_slot, available_slots, timestamp=None),
        )

    time_match = _find_time_matching_cycle(entry, upstream_slot, available_slots)
    if time_match.board_cycle is not None:
        if _has_pid_match_outside_target(entry, upstream_slot, time_match):
            _append_match_detail(time_match, "pid_fallback_blocked_by_time_cycle=true")
        return time_match

    pid_match = _find_pid_matching_cycle(entry, upstream_slot)
    if pid_match != (None, None):
        board_cycle, cpu_cycle = pid_match
        match = _CycleMatch(
            board_cycle=board_cycle,
            cpu_cycle=cpu_cycle,
            reason="matched_by_pid",
            detail=time_match.detail,
        )
        _append_match_detail(match, "pid_fallback_nearest_adjacent=true")
        return match

    return time_match


def _find_pid_matching_cycle(
    entry: MechLogEntry,
    upstream_slot: MechSlotOutput,
) -> tuple[MechBoardCycle | None, MechCpuCycle | None]:
    if not entry.pid:
        return None, None

    nearest_cycles = _nearest_cycles_for_timestamp(upstream_slot.board_cycles, entry.timestamp)
    if not nearest_cycles:
        return None, None

    exact_matches: list[tuple[MechBoardCycle, MechCpuCycle | None]] = []
    pid_matches: list[tuple[MechBoardCycle, MechCpuCycle | None]] = []

    for cycle in nearest_cycles:
        if entry.cpu_id:
            for cpu_cycle in cycle.cpu_cycles:
                if cpu_cycle.cpu_id != entry.cpu_id:
                    continue
                for process in cpu_cycle.processes:
                    if process.pid != entry.pid:
                        continue
                    match = (cycle, cpu_cycle)
                    if process.process_name == entry.process_name:
                        exact_matches.append(match)
                    else:
                        pid_matches.append(match)
        else:
            for process in cycle.processes:
                if process.pid != entry.pid:
                    continue
                match = (cycle, None)
                if process.process_name == entry.process_name:
                    exact_matches.append(match)
                else:
                    pid_matches.append(match)

    matches = exact_matches or pid_matches
    return _select_match_by_timestamp(entry, matches)


def _has_pid_match_outside_target(
    entry: MechLogEntry,
    upstream_slot: MechSlotOutput,
    target: _CycleMatch,
) -> bool:
    if not entry.pid:
        return False

    for cycle in upstream_slot.board_cycles:
        if entry.cpu_id:
            for cpu_cycle in cycle.cpu_cycles:
                if cpu_cycle.cpu_id != entry.cpu_id:
                    continue
                if cycle is target.board_cycle and cpu_cycle is target.cpu_cycle:
                    continue
                if _cycle_has_pid(cpu_cycle.processes, entry):
                    return True
            continue

        if cycle is target.board_cycle and target.cpu_cycle is None:
            continue
        if _cycle_has_pid(cycle.processes, entry):
            return True

    return False


def _cycle_has_pid(processes: list[MechProcessLifecycle], entry: MechLogEntry) -> bool:
    return any(process.pid == entry.pid for process in processes)


def _nearest_cycles_for_timestamp(
    cycles: list[MechBoardCycle] | list[MechCpuCycle],
    timestamp: datetime | None,
) -> list[MechBoardCycle] | list[MechCpuCycle]:
    if timestamp is None:
        return []
    scored = [
        (_time_distance(cycle.start_time, cycle.end_time, timestamp), index, cycle)
        for index, cycle in enumerate(cycles)
    ]
    scored = [item for item in scored if item[0] != float("inf")]
    if not scored:
        return []
    scored.sort(key=lambda item: (item[0], item[1]))
    best_distance = scored[0][0]
    return [cycle for distance, _index, cycle in scored if distance == best_distance]


def _find_time_matching_cycle(
    entry: MechLogEntry,
    upstream_slot: MechSlotOutput,
    available_slots: list[str],
) -> _CycleMatch:
    for cycle in upstream_slot.board_cycles:
        if not _contains_time(cycle.start_time, cycle.end_time, entry.timestamp):
            continue
        if entry.cpu_id:
            for cpu_cycle in cycle.cpu_cycles:
                if cpu_cycle.cpu_id != entry.cpu_id:
                    continue
                if _contains_time(cpu_cycle.start_time, cpu_cycle.end_time, entry.timestamp):
                    return _CycleMatch(
                        board_cycle=cycle,
                        cpu_cycle=cpu_cycle,
                        reason="matched_by_time",
                    )
            return _CycleMatch(
                board_cycle=cycle,
                reason="no_cpu_cycle_contains_timestamp",
                detail=_cpu_cycle_detail(upstream_slot, available_slots, cycle, entry),
            )
        return _CycleMatch(board_cycle=cycle, reason="matched_by_time")
    return _CycleMatch(
        reason="no_board_cycle_contains_timestamp",
        detail=_slot_detail(upstream_slot, available_slots, timestamp=entry.timestamp),
    )


def _select_match_by_timestamp(
    entry: MechLogEntry,
    matches: list[tuple[MechBoardCycle, MechCpuCycle | None]],
) -> tuple[MechBoardCycle | None, MechCpuCycle | None]:
    if not matches or entry.timestamp is None:
        return None, None

    unique_matches: list[tuple[MechBoardCycle, MechCpuCycle | None]] = []
    seen: set[tuple[int, int]] = set()
    for board_cycle, cpu_cycle in matches:
        key = (id(board_cycle), id(cpu_cycle) if cpu_cycle else 0)
        if key in seen:
            continue
        seen.add(key)
        unique_matches.append((board_cycle, cpu_cycle))

    if len(unique_matches) == 1:
        return unique_matches[0]

    containing = [
        match for match in unique_matches
        if _match_contains_timestamp(match, entry.timestamp)
    ]
    if len(containing) == 1:
        return containing[0]
    if len(containing) > 1:
        return None, None

    scored = [
        (_match_time_distance(match, entry.timestamp), index, match)
        for index, match in enumerate(unique_matches)
    ]
    scored.sort(key=lambda item: (item[0], item[1]))
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None, None
    return scored[0][2]


def _match_contains_timestamp(
    match: tuple[MechBoardCycle, MechCpuCycle | None],
    timestamp: datetime,
) -> bool:
    board_cycle, cpu_cycle = match
    if cpu_cycle is not None:
        return _contains_time(cpu_cycle.start_time, cpu_cycle.end_time, timestamp)
    return _contains_time(board_cycle.start_time, board_cycle.end_time, timestamp)


def _match_time_distance(
    match: tuple[MechBoardCycle, MechCpuCycle | None],
    timestamp: datetime,
) -> float:
    board_cycle, cpu_cycle = match
    if cpu_cycle is not None:
        return _time_distance(cpu_cycle.start_time, cpu_cycle.end_time, timestamp)
    return _time_distance(board_cycle.start_time, board_cycle.end_time, timestamp)


def _contains_time(
    start: datetime | None,
    end: datetime | None,
    timestamp: datetime,
) -> bool:
    if start is None or end is None:
        return False
    return start <= timestamp <= end


def _time_distance(
    start: datetime | None,
    end: datetime | None,
    timestamp: datetime,
) -> float:
    if start is not None and timestamp < start:
        return abs((start - timestamp).total_seconds())
    if end is not None and timestamp > end:
        return abs((timestamp - end).total_seconds())
    if start is not None and end is not None:
        return 0.0
    return float("inf")


def _merge_unknown_entries_into_unique_known_bucket(
    buckets: list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]],
    matches: dict[int, _CycleMatch] | None = None,
    upstream_slot: MechSlotOutput | None = None,
    module_key: str = "module2",
    nearest_stats: _NearestResolutionStats | None = None,
) -> None:
    for specificity in (1, 0):
        candidates = _candidate_buckets_by_process_key(
            buckets,
            min_specificity=specificity + 1,
        )
        for board_cycle, cpu_cycle, entries in buckets:
            if _bucket_specificity(board_cycle, cpu_cycle) != specificity:
                continue

            remaining: list[MechLogEntry] = []
            for entry in entries:
                targets = candidates.get(_entry_process_key(entry), [])
                resolution = _resolve_candidate_by_nearest_time(
                    entry,
                    targets,
                    range_getter=lambda target: _known_bucket_target_range(target, upstream_slot),
                    admissible_range_getter=lambda target: _candidate_admissible_range(
                        target.board_cycle,
                        target.cpu_cycle,
                        upstream_slot,
                    ),
                    summary_formatter=_format_known_bucket_target,
                )
                if resolution.target is not None:
                    resolution.target.entries.append(entry)
                    if resolution.target_count > 1 and nearest_stats is not None:
                        nearest_stats.known_process += 1
                    _log_resolved_unknown_by_nearest_time(
                        module_key,
                        entry,
                        resolution,
                    )
                    continue

                if targets:
                    if len(targets) > 1 and matches is not None:
                        match = matches.get(id(entry))
                        if match is not None:
                            original_reason = match.reason or "unknown"
                            match.reason = "no_unique_known_process_target"
                            match.detail = (
                                f"original_reason={original_reason} "
                                f"{resolution.detail} {match.detail}"
                            ).strip()
                    elif matches is not None:
                        match = matches.get(id(entry))
                        if match is not None:
                            _append_match_detail(match, resolution.detail)
                remaining.append(entry)
            entries[:] = remaining

    buckets[:] = [bucket for bucket in buckets if bucket[2]]


def _merge_unknown_entries_into_unique_expanded_cycle_bucket(
    buckets: list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]],
    matches: dict[int, _CycleMatch],
    upstream_slot: MechSlotOutput | None = None,
    module_key: str = "module2",
    nearest_stats: _NearestResolutionStats | None = None,
) -> None:
    targets = _projected_cycle_targets(buckets, upstream_slot)

    for board_cycle, _cpu_cycle, entries in buckets:
        if board_cycle.dir_name != "unknown":
            continue

        remaining: list[MechLogEntry] = []
        for entry in entries:
            candidates = _expanded_cycle_targets_for_entry(entry, targets)
            match = matches.get(id(entry))
            resolution = _resolve_candidate_by_nearest_time(
                entry,
                candidates,
                range_getter=lambda target: (target.start_time, target.end_time),
                admissible_range_getter=lambda target: _candidate_admissible_range(
                    target.board_cycle,
                    target.cpu_cycle,
                    upstream_slot,
                ),
                summary_formatter=_format_projected_target,
            )
            if resolution.target is not None:
                resolution.target.entries.append(entry)
                if resolution.target_count > 1 and nearest_stats is not None:
                    nearest_stats.projected += 1
                _log_resolved_unknown_by_nearest_time(
                    module_key,
                    entry,
                    resolution,
                )
                continue

            if match is not None:
                if resolution.target_count > 1:
                    original_reason = match.reason or "unknown"
                    match.reason = "no_unique_projected_assignment_target"
                    match.detail = (
                        f"original_reason={original_reason} "
                        f"{resolution.detail} "
                        f"{match.detail}"
                    ).strip()
                elif resolution.target_count == 1:
                    _append_match_detail(match, resolution.detail)
                else:
                    _append_match_detail(match, "projected_target_count=0")
            remaining.append(entry)
        entries[:] = remaining

    buckets[:] = [bucket for bucket in buckets if bucket[2]]


def _projected_cycle_targets(
    buckets: list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]],
    upstream_slot: MechSlotOutput | None = None,
) -> list[_ProjectedCycleTarget]:
    board_projections: dict[int, _BoardProjection] = {}
    targets: list[_ProjectedCycleTarget] = []
    unknown_cpu_ids = _top_level_unknown_cpu_ids(buckets)
    needs_board_target = _has_top_level_board_unknown_entries(buckets)

    for board_cycle, cpu_cycle, entries in buckets:
        if board_cycle.dir_name == "unknown":
            continue

        projection = board_projections.get(id(board_cycle))
        if projection is None:
            projection = _BoardProjection(
                board_cycle=board_cycle,
                entries=[],
                cpu_unknown_by_id={},
            )
            board_projections[id(board_cycle)] = projection
        projection.entries.extend(entries)

        if cpu_cycle is None:
            projection.board_entries = entries
            continue

        if cpu_cycle.dir_name == "unknown":
            projection.cpu_unknown_by_id[cpu_cycle.cpu_id] = (cpu_cycle, entries)
            continue

        lower_bound, upper_bound = _extension_limits_for_target(
            board_cycle,
            cpu_cycle,
            upstream_slot,
        )
        start_time, end_time = _projected_bounds(
            cpu_cycle.start_time,
            cpu_cycle.end_time,
            entries,
            lower_bound,
            upper_bound,
        )
        targets.append(_ProjectedCycleTarget(
            board_cycle=board_cycle,
            cpu_cycle=cpu_cycle,
            entries=entries,
            start_time=start_time,
            end_time=end_time,
        ))

    for projection in board_projections.values():
        board_lower, board_upper = _extension_limits_for_target(
            projection.board_cycle,
            None,
            upstream_slot,
        )
        board_start, board_end = _projected_bounds(
            projection.board_cycle.start_time,
            projection.board_cycle.end_time,
            projection.entries,
            board_lower,
            board_upper,
        )

        board_entries = projection.board_entries
        if board_entries is None and needs_board_target:
            board_entries = []
            buckets.append((projection.board_cycle, None, board_entries))
        if board_entries is not None:
            targets.append(_ProjectedCycleTarget(
                board_cycle=projection.board_cycle,
                cpu_cycle=None,
                entries=board_entries,
                start_time=board_start,
                end_time=board_end,
            ))

        cpu_unknown_by_id = projection.cpu_unknown_by_id or {}
        for cpu_id in sorted(unknown_cpu_ids):
            cpu_unknown = cpu_unknown_by_id.get(cpu_id)
            if cpu_unknown is None:
                cpu_entries = []
                cpu_cycle = MechCpuCycle(
                    cpu_id=cpu_id,
                    dir_name="unknown",
                    start_time=projection.board_cycle.start_time,
                    end_time=projection.board_cycle.end_time,
                )
                buckets.append((projection.board_cycle, cpu_cycle, cpu_entries))
            else:
                cpu_cycle, cpu_entries = cpu_unknown
            targets.append(_ProjectedCycleTarget(
                board_cycle=projection.board_cycle,
                cpu_cycle=cpu_cycle,
                entries=cpu_entries,
                start_time=board_start,
                end_time=board_end,
            ))

    return targets


def _top_level_unknown_cpu_ids(
    buckets: list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]],
) -> set[str]:
    return {
        entry.cpu_id
        for board_cycle, _cpu_cycle, entries in buckets
        if board_cycle.dir_name == "unknown"
        for entry in entries
        if entry.cpu_id
    }


def _has_top_level_board_unknown_entries(
    buckets: list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]],
) -> bool:
    return any(
        not entry.cpu_id
        for board_cycle, _cpu_cycle, entries in buckets
        if board_cycle.dir_name == "unknown"
        for entry in entries
    )


def _projected_bounds(
    start_time: datetime | None,
    end_time: datetime | None,
    entries: list[MechLogEntry],
    lower_bound: datetime | None = None,
    upper_bound: datetime | None = None,
) -> tuple[datetime | None, datetime | None]:
    times = [entry.timestamp for entry in entries if entry.timestamp]
    candidates = [time for time in [start_time, end_time, *times] if time]
    if not candidates:
        return None, None
    start = min(candidates)
    end = max(candidates)
    if lower_bound is not None and start < lower_bound:
        start = lower_bound
    if upper_bound is not None and end > upper_bound:
        end = upper_bound
    if start > end:
        return start_time, end_time
    return start, end


def _expanded_cycle_targets_for_entry(
    entry: MechLogEntry,
    targets: list[_ProjectedCycleTarget],
) -> list[_ProjectedCycleTarget]:
    if not entry.cpu_id:
        return [
            target for target in targets
            if target.cpu_cycle is None
        ]

    candidates = [
        target for target in targets
        if target.cpu_cycle is not None
        and target.cpu_cycle.cpu_id == entry.cpu_id
    ]
    real_cpu_candidates = [
        target for target in candidates
        if target.cpu_cycle is not None and target.cpu_cycle.dir_name != "unknown"
    ]
    return real_cpu_candidates or candidates


def _resolve_candidate_by_nearest_time(
    entry: MechLogEntry,
    candidates: list[Any],
    range_getter: Callable[[Any], tuple[datetime | None, datetime | None]],
    admissible_range_getter: Callable[[Any], tuple[datetime | None, datetime | None]],
    summary_formatter: Callable[[Any], str],
) -> _CandidateResolution:
    target_count = len(candidates)
    if target_count == 0:
        return _CandidateResolution(
            target=None,
            target_count=0,
            admissible_count=0,
            detail="target_count=0 admissible_count=0",
        )

    if entry.timestamp is None:
        detail = _candidate_resolution_detail(
            candidates,
            range_getter,
            admissible_range_getter,
            summary_formatter,
            timestamp=None,
            admissible_candidates=[],
        )
        return _CandidateResolution(
            target=None,
            target_count=target_count,
            admissible_count=0,
            detail=detail,
        )

    scored: list[tuple[float, int, Any, str]] = []
    admissible_candidates: list[Any] = []
    for index, candidate in enumerate(candidates):
        lower_bound, upper_bound = admissible_range_getter(candidate)
        if not _contains_open_time(lower_bound, upper_bound, entry.timestamp):
            continue
        start_time, end_time = range_getter(candidate)
        distance = _time_distance(start_time, end_time, entry.timestamp)
        scored.append((distance, index, candidate, summary_formatter(candidate)))
        admissible_candidates.append(candidate)

    detail = _candidate_resolution_detail(
        candidates,
        range_getter,
        admissible_range_getter,
        summary_formatter,
        timestamp=entry.timestamp,
        admissible_candidates=admissible_candidates,
    )
    if not scored:
        return _CandidateResolution(
            target=None,
            target_count=target_count,
            admissible_count=0,
            detail=detail,
        )

    if len(scored) == 1:
        distance, _index, candidate, summary = scored[0]
        return _CandidateResolution(
            target=candidate,
            target_count=target_count,
            admissible_count=1,
            distance=distance,
            selected_summary=summary,
            detail=detail,
        )

    scored.sort(key=lambda item: (item[0], item[1]))
    best_distance = scored[0][0]
    tied = [item for item in scored if item[0] == best_distance]
    if len(tied) > 1:
        return _CandidateResolution(
            target=None,
            target_count=target_count,
            admissible_count=len(scored),
            distance=best_distance,
            tie=True,
            detail=detail,
        )

    distance, _index, candidate, summary = scored[0]
    return _CandidateResolution(
        target=candidate,
        target_count=target_count,
        admissible_count=len(scored),
        distance=distance,
        selected_summary=summary,
        detail=detail,
    )


def _candidate_resolution_detail(
    candidates: list[Any],
    range_getter: Callable[[Any], tuple[datetime | None, datetime | None]],
    admissible_range_getter: Callable[[Any], tuple[datetime | None, datetime | None]],
    summary_formatter: Callable[[Any], str],
    timestamp: datetime | None,
    admissible_candidates: list[Any],
) -> str:
    admissible_ids = {id(candidate) for candidate in admissible_candidates}
    formatted = []
    for candidate in candidates[:3]:
        start_time, end_time = range_getter(candidate)
        lower_bound, upper_bound = admissible_range_getter(candidate)
        if timestamp is None:
            distance = None
            admissible = False
        else:
            distance = _time_distance(start_time, end_time, timestamp)
            admissible = id(candidate) in admissible_ids
        formatted.append(
            f"{summary_formatter(candidate)} "
            f"range_start={_format_optional_ts(start_time)} "
            f"range_end={_format_optional_ts(end_time)} "
            f"admissible_start={_format_optional_ts(lower_bound)} "
            f"admissible_end={_format_optional_ts(upper_bound)} "
            f"distance={_format_distance(distance)} "
            f"admissible={str(admissible).lower()}"
        )

    return (
        f"target_count={len(candidates)} "
        f"admissible_count={len(admissible_candidates)} "
        f"candidates=[{'; '.join(formatted)}]"
    )


def _contains_open_time(
    lower_bound: datetime | None,
    upper_bound: datetime | None,
    timestamp: datetime,
) -> bool:
    if lower_bound is not None and timestamp < lower_bound:
        return False
    if upper_bound is not None and timestamp > upper_bound:
        return False
    return True


def _format_distance(distance: float | None) -> str:
    if distance is None:
        return "<none>"
    if distance == float("inf"):
        return "<inf>"
    return f"{distance:.6f}"


def _known_bucket_target_range(
    target: _KnownBucketTarget,
    upstream_slot: MechSlotOutput | None,
) -> tuple[datetime | None, datetime | None]:
    lower_bound, upper_bound = _extension_limits_for_target(
        target.board_cycle,
        target.cpu_cycle,
        upstream_slot,
    )
    start_time, end_time = _target_base_range(target.board_cycle, target.cpu_cycle)
    return _projected_bounds(
        start_time,
        end_time,
        target.entries,
        lower_bound,
        upper_bound,
    )


def _candidate_admissible_range(
    board_cycle: MechBoardCycle,
    cpu_cycle: MechCpuCycle | None,
    upstream_slot: MechSlotOutput | None,
) -> tuple[datetime | None, datetime | None]:
    return _extension_limits_for_target(board_cycle, cpu_cycle, upstream_slot)


def _target_base_range(
    board_cycle: MechBoardCycle,
    cpu_cycle: MechCpuCycle | None,
) -> tuple[datetime | None, datetime | None]:
    if cpu_cycle is not None and cpu_cycle.dir_name != "unknown":
        return cpu_cycle.start_time, cpu_cycle.end_time
    return board_cycle.start_time, board_cycle.end_time


def _extension_limits_for_target(
    board_cycle: MechBoardCycle,
    cpu_cycle: MechCpuCycle | None,
    upstream_slot: MechSlotOutput | None,
) -> tuple[datetime | None, datetime | None]:
    board_lower, board_upper = _extension_limits_for_cycle(
        board_cycle,
        upstream_slot.board_cycles if upstream_slot is not None else [],
    )
    if cpu_cycle is None or cpu_cycle.dir_name == "unknown":
        return board_lower, board_upper

    peer_cpu_cycles = [
        peer for peer in board_cycle.cpu_cycles
        if peer.cpu_id == cpu_cycle.cpu_id
    ]
    cpu_lower, cpu_upper = _extension_limits_for_cycle(cpu_cycle, peer_cpu_cycles)
    return _max_optional_datetime(board_lower, cpu_lower), _min_optional_datetime(board_upper, cpu_upper)


def _extension_limits_for_cycle(
    cycle: MechBoardCycle | MechCpuCycle,
    peers: list[MechBoardCycle] | list[MechCpuCycle],
) -> tuple[datetime | None, datetime | None]:
    if cycle.dir_name == "unknown":
        return None, None

    ordered = [peer for peer in peers if peer.start_time is not None or peer.end_time is not None]
    index = next((idx for idx, peer in enumerate(ordered) if peer is cycle), None)
    if index is None:
        return None, None

    lower: datetime | None = None
    upper: datetime | None = None
    # TODO: 评估 gap 中点作为 module2 扩展临界点是否符合业务语义；
    # 可能需要改为相邻 module1 lifecycle 边界或配置化归属规则。
    if index > 0:
        lower = _gap_midpoint(ordered[index - 1].end_time, cycle.start_time)
        if lower is not None:
            lower = lower + _NON_OVERLAP_EPSILON
    if index + 1 < len(ordered):
        upper = _gap_midpoint(cycle.end_time, ordered[index + 1].start_time)
    return lower, upper


def _gap_midpoint(
    left: datetime | None,
    right: datetime | None,
) -> datetime | None:
    if left is None or right is None or right <= left:
        return None
    return left + (right - left) / 2


def _max_optional_datetime(
    left: datetime | None,
    right: datetime | None,
) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _min_optional_datetime(
    left: datetime | None,
    right: datetime | None,
) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _append_match_detail(match: _CycleMatch, addition: str) -> None:
    if addition in match.detail:
        return
    match.detail = f"{match.detail} {addition}".strip()


def _format_projected_target(target: _ProjectedCycleTarget) -> str:
    if target.cpu_cycle is None:
        scope = "board"
    else:
        scope = f"cpu_{target.cpu_cycle.cpu_id}/{target.cpu_cycle.dir_name}"
    return (
        f"{target.board_cycle.dir_name}/{scope} "
        f"projected_start={_format_optional_ts(target.start_time)} "
        f"projected_end={_format_optional_ts(target.end_time)}"
    )


def _format_known_bucket_target(target: _KnownBucketTarget) -> str:
    if target.cpu_cycle is None:
        scope = "board"
    else:
        scope = f"cpu_{target.cpu_cycle.cpu_id}/{target.cpu_cycle.dir_name}"
    return f"{target.board_cycle.dir_name}/{scope}"


def _candidate_buckets_by_process_key(
    buckets: list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]],
    min_specificity: int,
) -> dict[tuple[str, str, str], list[_KnownBucketTarget]]:
    candidates: dict[tuple[str, str, str], list[_KnownBucketTarget]] = defaultdict(list)
    for board_cycle, cpu_cycle, entries in buckets:
        if _bucket_specificity(board_cycle, cpu_cycle) < min_specificity:
            continue

        seen: set[tuple[str, str, str]] = set()
        for entry in entries:
            key = _entry_process_key(entry)
            if key in seen:
                continue
            seen.add(key)
            candidates[key].append(_KnownBucketTarget(board_cycle, cpu_cycle, entries))

    return candidates


def _bucket_specificity(board_cycle: MechBoardCycle, cpu_cycle: MechCpuCycle | None) -> int:
    if board_cycle.dir_name == "unknown":
        return 0
    if cpu_cycle is not None and cpu_cycle.dir_name == "unknown":
        return 1
    return 2


def _entry_process_key(entry: MechLogEntry) -> tuple[str, str, str]:
    return entry.process_name, entry.pid, entry.cpu_id or ""


def _log_resolved_unknown_by_nearest_time(
    module_key: str,
    entry: MechLogEntry,
    resolution: _CandidateResolution,
) -> None:
    if resolution.target_count <= 1:
        return

    logger.debug(
        "[%s] module2归属诊断: slot=%s cpu=%s process=%s pid=%s "
        "timestamp=%s source=%s detail=\"resolved_unknown_by_nearest_time=true "
        "target_count=%d admissible_count=%d selected=%s distance=%s %s\" raw=\"%s\"",
        module_key,
        entry.slot,
        entry.cpu_id or "<board>",
        entry.process_name,
        entry.pid or "<empty>",
        _format_optional_ts(entry.timestamp),
        entry.source_file,
        resolution.target_count,
        resolution.admissible_count,
        resolution.selected_summary,
        _format_distance(resolution.distance),
        resolution.detail,
        _format_raw(entry.raw),
    )


def _log_nearest_resolution_summary(
    module_key: str,
    entries: list[MechLogEntry],
    stats: _NearestResolutionStats,
) -> None:
    if stats.total <= 0:
        return
    slot = entries[0].slot if entries else "<unknown>"
    logger.info(
        "[%s] module2 unknown归属摘要: slot=%s resolved_by_nearest_time=%d "
        "known_process=%d projected=%d",
        module_key,
        slot,
        stats.total,
        stats.known_process,
        stats.projected,
    )


def _log_unknown_assignments(
    module_key: str,
    buckets: list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]],
    matches: dict[int, _CycleMatch],
) -> None:
    for board_cycle, cpu_cycle, entries in buckets:
        if _bucket_specificity(board_cycle, cpu_cycle) >= 2:
            continue
        for entry in entries:
            match = matches.get(id(entry), _CycleMatch(reason="unknown", detail=""))
            logger.info(
                "[%s] 归属到unknown: slot=%s cpu=%s process=%s pid=%s "
                "timestamp=%s source=%s reason=%s detail=\"%s\" raw=\"%s\"",
                module_key,
                entry.slot,
                entry.cpu_id or "<board>",
                entry.process_name,
                entry.pid or "<empty>",
                _format_optional_ts(entry.timestamp),
                entry.source_file,
                match.reason or "unknown",
                match.detail,
                _format_raw(entry.raw),
            )


def _log_successful_assignment_detail(
    module_key: str,
    entry: MechLogEntry,
    match: _CycleMatch,
) -> None:
    if match.board_cycle is None:
        return
    if "pid_fallback_blocked_by_time_cycle=true" not in match.detail:
        return

    logger.debug(
        "[%s] module2归属诊断: slot=%s cpu=%s process=%s pid=%s "
        "timestamp=%s source=%s reason=%s detail=\"%s\" raw=\"%s\"",
        module_key,
        entry.slot,
        entry.cpu_id or "<board>",
        entry.process_name,
        entry.pid or "<empty>",
        _format_optional_ts(entry.timestamp),
        entry.source_file,
        match.reason or "unknown",
        match.detail,
        _format_raw(entry.raw),
    )


def _slot_detail(
    upstream_slot: MechSlotOutput,
    available_slots: list[str],
    timestamp: datetime | None,
) -> str:
    return (
        "upstream_slot_found=true "
        f"available_slots={_format_list(available_slots)} "
        f"board_cycles={len(upstream_slot.board_cycles)} "
        f"nearest=[{_format_nearest_board_cycles(upstream_slot, timestamp)}]"
    )


def _cpu_cycle_detail(
    upstream_slot: MechSlotOutput,
    available_slots: list[str],
    board_cycle: MechBoardCycle,
    entry: MechLogEntry,
) -> str:
    cpu_cycles = [cycle for cycle in board_cycle.cpu_cycles if cycle.cpu_id == entry.cpu_id]
    if not cpu_cycles:
        nearest = "<none>"
    else:
        nearest = _format_nearest_cycles(cpu_cycles, entry.timestamp)
    return (
        "upstream_slot_found=true "
        f"available_slots={_format_list(available_slots)} "
        f"board_cycles={len(upstream_slot.board_cycles)} "
        f"board_cycle={_format_cycle_summary(board_cycle)} "
        f"cpu_id={entry.cpu_id} cpu_cycles={len(cpu_cycles)} nearest_cpu=[{nearest}]"
    )


def _format_nearest_board_cycles(
    upstream_slot: MechSlotOutput,
    timestamp: datetime | None,
) -> str:
    return _format_nearest_cycles(upstream_slot.board_cycles, timestamp)


def _format_nearest_cycles(
    cycles: list[MechBoardCycle] | list[MechCpuCycle],
    timestamp: datetime | None,
) -> str:
    if not cycles:
        return "<none>"
    if timestamp is None:
        ordered = list(enumerate(cycles))
    else:
        ordered = sorted(
            enumerate(cycles),
            key=lambda item: (
                _time_distance(item[1].start_time, item[1].end_time, timestamp),
                item[0],
            ),
        )
    return "; ".join(_format_cycle_summary(cycle) for _index, cycle in ordered[:3])


def _format_cycle_summary(cycle: MechBoardCycle | MechCpuCycle) -> str:
    return (
        f"{cycle.dir_name} "
        f"start={_format_optional_ts(cycle.start_time)} "
        f"end={_format_optional_ts(cycle.end_time)}"
    )


def _format_optional_ts(timestamp: datetime | None) -> str:
    return timestamp.isoformat() if timestamp else "<none>"


def _format_list(values: list[str]) -> str:
    return "[" + ",".join(str(value) for value in values) + "]"


def _format_raw(raw: str) -> str:
    compact = raw.replace("\r", "\\r").replace("\n", "\\n").replace('"', "'")
    if len(compact) > 160:
        return compact[:157] + "..."
    return compact


def _build_cycles(
    grouped: list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]],
    upstream_slot: MechSlotOutput | None = None,
    matches: dict[int, _CycleMatch] | None = None,
    module_key: str = "module2",
) -> list[MechBoardCycle]:
    cycles: list[MechBoardCycle] = []
    board_by_key: dict[int, MechBoardCycle] = {}
    cpu_by_key: dict[tuple[int, int], MechCpuCycle] = {}

    for board_template, cpu_template, entries in grouped:
        board_key = id(board_template)
        board_cycle = board_by_key.get(board_key)
        if board_cycle is None:
            board_cycle = MechBoardCycle(
                dir_name=board_template.dir_name,
                start_time=board_template.start_time,
                end_time=board_template.end_time,
            )
            cycles.append(board_cycle)
            board_by_key[board_key] = board_cycle
        lower_bound, upper_bound = _extension_limits_for_target(
            board_template,
            None,
            upstream_slot,
        )
        _extend_cycle_bounds(
            board_cycle,
            entries,
            lower_bound,
            upper_bound,
            matches=matches,
            module_key=module_key,
            scope="board",
        )

        if cpu_template is None:
            board_cycle.processes.extend(_build_processes(entries))
            continue

        cpu_key = (board_key, id(cpu_template))
        cpu_cycle = cpu_by_key.get(cpu_key)
        if cpu_cycle is None:
            cpu_cycle = MechCpuCycle(
                cpu_id=cpu_template.cpu_id,
                dir_name=cpu_template.dir_name,
                start_time=cpu_template.start_time,
                end_time=cpu_template.end_time,
            )
            board_cycle.cpu_cycles.append(cpu_cycle)
            cpu_by_key[cpu_key] = cpu_cycle
        lower_bound, upper_bound = _extension_limits_for_target(
            board_template,
            cpu_template,
            upstream_slot,
        )
        _extend_cycle_bounds(
            cpu_cycle,
            entries,
            lower_bound,
            upper_bound,
            matches=matches,
            module_key=module_key,
            scope=f"cpu_{cpu_template.cpu_id}",
        )
        cpu_cycle.processes.extend(_build_processes(entries))

    return cycles


def _extend_cycle_bounds(
    cycle: MechBoardCycle | MechCpuCycle,
    entries: list[MechLogEntry],
    lower_bound: datetime | None = None,
    upper_bound: datetime | None = None,
    matches: dict[int, _CycleMatch] | None = None,
    module_key: str = "module2",
    scope: str = "board",
) -> None:
    if cycle.dir_name == "unknown":
        return

    times = [entry.timestamp for entry in entries if entry.timestamp]
    candidates = [time for time in [cycle.start_time, cycle.end_time, *times] if time]
    if not candidates:
        return

    proposed_start = min(candidates)
    proposed_end = max(candidates)
    start_time = proposed_start
    end_time = proposed_end
    if lower_bound is not None and start_time < lower_bound:
        start_time = lower_bound
    if upper_bound is not None and end_time > upper_bound:
        end_time = upper_bound
    if start_time > end_time:
        return

    if proposed_start != start_time or proposed_end != end_time:
        _log_pid_fallback_clamp(
            module_key,
            scope,
            cycle,
            entries,
            matches or {},
            proposed_start,
            proposed_end,
            start_time,
            end_time,
        )

    cycle.start_time = start_time
    cycle.end_time = end_time
    cycle.dir_name = _format_cycle_dir(cycle.start_time, cycle.end_time)


def _log_pid_fallback_clamp(
    module_key: str,
    scope: str,
    cycle: MechBoardCycle | MechCpuCycle,
    entries: list[MechLogEntry],
    matches: dict[int, _CycleMatch],
    proposed_start: datetime,
    proposed_end: datetime,
    clamped_start: datetime,
    clamped_end: datetime,
) -> None:
    for entry in entries:
        match = matches.get(id(entry))
        if match is None or "pid_fallback_nearest_adjacent=true" not in match.detail:
            continue

        logger.debug(
            "[%s] module2归属诊断: slot=%s cpu=%s process=%s pid=%s "
            "timestamp=%s source=%s reason=%s "
            "detail=\"pid_fallback_clamped=true scope=%s "
            "original_cycle=%s proposed_start=%s proposed_end=%s "
            "clamped_start=%s clamped_end=%s target_cycle=%s %s\" raw=\"%s\"",
            module_key,
            entry.slot,
            entry.cpu_id or "<board>",
            entry.process_name,
            entry.pid or "<empty>",
            _format_optional_ts(entry.timestamp),
            entry.source_file,
            match.reason or "unknown",
            scope,
            _format_cycle_summary(match.cpu_cycle or match.board_cycle or cycle),
            _format_optional_ts(proposed_start),
            _format_optional_ts(proposed_end),
            _format_optional_ts(clamped_start),
            _format_optional_ts(clamped_end),
            _format_cycle_summary(cycle),
            match.detail,
            _format_raw(entry.raw),
        )


def _format_cycle_dir(start: datetime | None, end: datetime | None) -> str:
    if start and end:
        return f"{start.strftime('%Y%m%dT%H%M%S')}-{end.strftime('%Y%m%dT%H%M%S')}"
    if start:
        return start.strftime("%Y%m%dT%H%M%S")
    return "unknown"


def _build_processes(entries: list[MechLogEntry]) -> list[MechProcessLifecycle]:
    by_key: dict[tuple[str, str, str], list[MechLogEntry]] = defaultdict(list)
    for entry in entries:
        by_key[(entry.process_name, entry.pid, entry.cpu_id or "")].append(entry)

    processes: list[MechProcessLifecycle] = []
    for (process_name, pid, _cpu_id), logs in sorted(by_key.items()):
        logs.sort(key=lambda e: (
            0 if e.timestamp else 1,
            e.timestamp.timestamp() if e.timestamp else 0,
            e.source_file,
            e.raw,
        ))
        processes.append(MechProcessLifecycle(
            process_name=process_name,
            pid=pid,
            logs=logs,
            total_count=len(logs),
            missing_sequences=[],
        ))
    return processes
