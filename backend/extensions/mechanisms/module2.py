"""当前产品 Module2 关联与归属机制扩展。"""

from __future__ import annotations

import logging
import hashlib
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from backend.contracts.plugins import MechanismContext, MechanismOutcome
from backend.contracts.runtime import Diagnostic, DiagnosticSeverity
from backend.extensions.mechanisms.base import MechanismPlugin
from backend.extensions.products.current.mechanism_input import CurrentMechanismInput
from backend.models import (
    LogEntry,
    MechBoardCycle,
    MechCpuCycle,
    MechLogEntry,
    MechProcessLifecycle,
    MechResult,
    MechSlotOutput,
)
from backend.parsing.file_iter import iter_log_entry_lines
from backend.parsing.mech_entry_dedup import dedupe_mech_entries
from backend.contracts.scopes import CycleRef
from backend.extensions.products.current.scopes import product_cycle_ref

logger = logging.getLogger("backend.plugins.mechanisms.module2")
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
    ref: str = ""


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
    ref: str = ""


@dataclass
class _KnownBucketTargetCache:
    target: _KnownBucketTarget
    lower_bound: datetime | None
    upper_bound: datetime | None
    base_start: datetime | None
    base_end: datetime | None
    raw_start: datetime | None
    raw_end: datetime | None
    range_start: datetime | None
    range_end: datetime | None


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


class Module2Plugin(MechanismPlugin):
    """Diagnostic-only module that reuses another module's board cycles."""

    @classmethod
    def validate_config(cls, module_key: str, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        for field in ("module_name", "identifying_keyword", "diag_pattern"):
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

    def build_diagnostic_line_scanner(self):
        errors = self.validate_config(self.module_key, self.config)
        if errors:
            return None
        diag_re = re.compile(self.config["diag_pattern"])
        keyword = str(self.config["identifying_keyword"])

        def _scanner(line: str, log_entry: LogEntry, slot_id: str) -> MechLogEntry | None:
            return self._scan_line(line, log_entry, slot_id, diag_re, keyword)

        return _scanner

    def execute(self, context: MechanismContext) -> MechanismOutcome:
        errors = self.validate_config(self.module_key, self.config)
        if errors:
            logger.warning("[%s] 配置校验失败: %s", self.module_key, errors)
            return MechanismOutcome(
                diagnostics=tuple(
                    Diagnostic(
                        code="LP_MECHANISM_CONFIG_INVALID",
                        message=message,
                        severity=DiagnosticSeverity.ERROR,
                        stage=f"mechanism.{self.module_key}",
                    )
                    for message in errors
                )
            )

        inputs = context.extension_input
        if not isinstance(inputs, CurrentMechanismInput):
            raise TypeError("Module2 requires CurrentMechanismInput")
        dependency_key = self._dependency_key()
        if not dependency_key:
            return MechanismOutcome(
                diagnostics=(
                    Diagnostic(
                        code="LP_MECHANISM_DEPENDENCY_INVALID",
                        message=f"{self.module_key}: exactly one depends_on entry is required",
                        severity=DiagnosticSeverity.ERROR,
                        stage=f"mechanism.{self.module_key}",
                    ),
                )
            )

        t0 = time.perf_counter()
        upstream = self._find_dependency(
            context.dependency_results,
            dependency_key,
        )
        logger.info(
            "LOGPARSE_PERF module2.find_dependency module=%s elapsed=%.3fs result=%s",
            self.module_key,
            time.perf_counter() - t0,
            "yes" if upstream is not None else "no",
        )
        if upstream is None:
            msg = (
                f"{self.module_key}: depends_on_module={dependency_key!r} result not found"
            )
            logger.warning("[%s] 依赖未找到: %s", self.module_key, msg)
            return MechanismOutcome(
                diagnostics=(
                    Diagnostic(
                        code="LP_MECHANISM_DEPENDENCY_MISSING",
                        message=msg,
                        severity=DiagnosticSeverity.ERROR,
                        stage=f"mechanism.{self.module_key}",
                    ),
                )
            )

        t0 = time.perf_counter()
        entries = self._scan_diagnostic_entries(inputs)
        diag_file_count = sum(
            len(slot.diagnostic_logs) for slot in inputs.diagnostic_slots
        )
        logger.info(
            "LOGPARSE_PERF module2.diag_scan module=%s elapsed=%.3fs files=%d entries=%d",
            self.module_key,
            time.perf_counter() - t0,
            diag_file_count,
            len(entries),
        )
        if not entries:
            logger.info("[%s] 未扫描到诊断日志条目 (keyword=%r, slots=%d)",
                        self.module_key,
                        self.config["identifying_keyword"],
                        len(inputs.diagnostic_slots))
            return MechanismOutcome()

        t0 = time.perf_counter()
        self._normalize_timezones(entries, upstream)
        entries = dedupe_mech_entries(entries)
        logger.info(
            "LOGPARSE_PERF module2.normalize_timezones module=%s elapsed=%.3fs entries=%d",
            self.module_key,
            time.perf_counter() - t0,
            len(entries),
        )

        t0 = time.perf_counter()
        mech_result = self._build_result(entries, upstream)
        logger.info(
            "LOGPARSE_PERF module2.build_result module=%s elapsed=%.3fs entries=%d slots=%d",
            self.module_key,
            time.perf_counter() - t0,
            len(entries),
            len(mech_result.slots),
        )
        return MechanismOutcome(result=mech_result)

    def parse(self, result) -> MechResult | None:
        """Compatibility helper for focused LAN tests and pre-v1 callers."""

        depends_on = self._dependency_key()
        dependency_results = {
            mech.module_key: mech
            for mech in result.mech_results
            if depends_on and mech.module_key == depends_on
        }
        outcome = self.execute(
            MechanismContext(
                extension_input=CurrentMechanismInput.from_collections(
                    result.diagnostic_slots,
                    result.private_slots,
                ),
                dependency_results=dependency_results,
            )
        )
        result.errors.extend(item.message for item in outcome.diagnostics)
        return outcome.result

    def _dependency_key(self) -> str:
        if len(self.descriptor.dependencies) == 1:
            return self.descriptor.dependencies[0]
        return ""

    def _find_dependency(
        self,
        dependency_results,
        depends_on: str,
    ) -> MechResult | None:
        upstream = dependency_results.get(depends_on)
        return upstream if isinstance(upstream, MechResult) else None

    def _scan_diagnostic_entries(
        self,
        inputs: CurrentMechanismInput,
    ) -> list[MechLogEntry]:
        precomputed_diag_entries = getattr(self, "_precomputed_diagnostic_entries", None)
        if precomputed_diag_entries is not None:
            return list(precomputed_diag_entries)

        diag_re = re.compile(self.config["diag_pattern"])
        keyword = str(self.config["identifying_keyword"])
        entries: list[MechLogEntry] = []

        for slot in inputs.diagnostic_slots:
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
            entry = self._scan_line(line, log_entry, source_slot_id, diag_re, keyword)
            if entry:
                entries.append(entry)

        return entries

    def _scan_line(
        self,
        line: str,
        log_entry: LogEntry,
        source_slot_id: str,
        diag_re: re.Pattern,
        keyword: str,
    ) -> MechLogEntry | None:
        if keyword not in line:
            return None
        m = diag_re.search(line)
        if not m:
            return None

        slot = _extract_slot_id(m.group("Slot"))
        cpu_id = (m.group("CPU_Id") or "").strip()
        if cpu_id == "0":
            cpu_id = ""
        process_name, pid = _parse_bracket_process_name(m.group("ProcessName"))
        context = m.group("Context")
        timestamp = self._extract_first_ts(line)
        source_file = f"slot_{source_slot_id}/{log_entry.name}"

        return MechLogEntry(
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
        )

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
            assign_t0 = time.perf_counter()
            grouped, matches = _assign_entries_to_cycles(
                slot_entries,
                upstream_slot,
                available_slots=[slot.slot_id for slot in upstream.slots],
                module_key=self.module_key,
            )
            slot_output.assignment_decisions = [
                _assignment_decision(entry, matches[_entry_ref(entry)])
                for entry in sorted(slot_entries, key=_entry_ref)
            ]
            logger.info(
                "LOGPARSE_PERF module2.assign_cycles module=%s slot=%s elapsed=%.3fs "
                "entries=%d buckets=%d matches=%d unknown_entries=%d",
                self.module_key,
                slot_id,
                time.perf_counter() - assign_t0,
                len(slot_entries),
                len(grouped),
                len(matches),
                _count_unknown_bucket_entries(grouped),
            )
            build_cycles_t0 = time.perf_counter()
            slot_output.board_cycles = _build_cycles(
                grouped,
                upstream_slot,
                matches,
                module_key=self.module_key,
            )
            logger.info(
                "LOGPARSE_PERF module2.build_cycles module=%s slot=%s elapsed=%.3fs "
                "groups=%d cycles=%d entries=%d",
                self.module_key,
                slot_id,
                time.perf_counter() - build_cycles_t0,
                len(grouped),
                len(slot_output.board_cycles),
                len(slot_entries),
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
    """Normalize module2 frame/slot forms to the module1 slot identity."""
    text = raw.strip()
    if "/" not in text:
        return text
    parts = [part.strip() for part in text.split("/") if part.strip()]
    return parts[-1] if parts else text


def _entry_ref(entry: MechLogEntry) -> str:
    """Stable entry identity used by structured assignment decisions."""

    timestamp = entry.timestamp.isoformat(timespec="microseconds") if entry.timestamp else ""
    fields = (
        timestamp,
        entry.source,
        entry.source_file,
        entry.slot,
        entry.cpu_id,
        entry.process_name,
        entry.pid,
        str(entry.sequence),
        entry.context,
        entry.raw,
    )
    return hashlib.sha256("\x1f".join(fields).encode("utf-8")).hexdigest()


def _cycle_pair_ref(
    board_cycle: MechBoardCycle,
    cpu_cycle: MechCpuCycle | None,
    slot_id: str = "unknown",
) -> tuple[CycleRef, CycleRef | None]:
    board_ref = product_cycle_ref(
        slot_id or "unknown",
        board_cycle.start_time,
        board_cycle.end_time,
    )
    if cpu_cycle is None:
        return board_ref, None
    cpu_ref = product_cycle_ref(
        slot_id or "unknown",
        cpu_cycle.start_time,
        cpu_cycle.end_time,
        cpu_id=cpu_cycle.cpu_id,
    )
    return board_ref, cpu_ref


def _cycle_ref_text(ref: CycleRef) -> str:
    return f"{ref.scope.identity}/{ref.cycle_id}#{ref.ordinal}"


def _target_ref(
    board_cycle: MechBoardCycle,
    cpu_cycle: MechCpuCycle | None,
    slot_id: str,
    *,
    discriminator: str = "",
) -> str:
    board_ref, cpu_ref = _cycle_pair_ref(board_cycle, cpu_cycle, slot_id)
    scope_ref = cpu_ref or board_ref
    suffix = f"/{discriminator}" if discriminator else ""
    return f"{_cycle_ref_text(scope_ref)}{suffix}"


def _assignment_decision(entry: MechLogEntry, match: _CycleMatch) -> dict[str, Any]:
    cycle = match.cpu_cycle or match.board_cycle
    cycle_ref = (
        product_cycle_ref(
            entry.slot or "unknown",
            cycle.start_time,
            cycle.end_time,
            cpu_id=entry.cpu_id if match.cpu_cycle is not None else "",
        )
        if cycle is not None
        else None
    )
    scope_ref = (
        cycle_ref.scope.identity
        if cycle_ref is not None
        else product_cycle_ref(entry.slot or "unknown", None, None, cpu_id=entry.cpu_id).scope.identity
    )
    return {
        "entry_ref": _entry_ref(entry),
        "status": "assigned" if match.board_cycle is not None else "unknown",
        "scope_ref": scope_ref,
        "cycle_ref": _cycle_ref_text(cycle_ref) if cycle_ref is not None else None,
        "reason": match.reason or "unknown",
        "detail": match.detail,
        "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
        "source_file": entry.source_file,
        "process_name": entry.process_name,
        "pid": entry.pid,
    }


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
    dict[str, _CycleMatch],
]:
    buckets: list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]] = []
    matches: dict[str, _CycleMatch] = {}
    nearest_stats = _NearestResolutionStats()
    unknown = MechBoardCycle(dir_name="unknown")
    slot_label = _entries_slot_label(entries)

    initial_t0 = time.perf_counter()
    for entry in entries:
        match = _find_matching_cycle(entry, upstream_slot, available_slots or [])
        matches[_entry_ref(entry)] = match
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
            if _cycle_pair_ref(existing_board, existing_cpu, slot_label) == _cycle_pair_ref(
                board_cycle,
                cpu_cycle,
                slot_label,
            ):
                existing_entries.append(entry)
                break
        else:
            buckets.append((board_cycle, cpu_cycle, [entry]))

    logger.info(
        "LOGPARSE_PERF module2.assign_initial module=%s slot=%s elapsed=%.3fs "
        "entries=%d buckets=%d matches=%d unknown_entries=%d upstream_cycles=%d",
        module_key,
        slot_label,
        time.perf_counter() - initial_t0,
        len(entries),
        len(buckets),
        len(matches),
        _count_unknown_bucket_entries(buckets),
        len(upstream_slot.board_cycles) if upstream_slot else 0,
    )

    known_t0 = time.perf_counter()
    known_buckets_before = len(buckets)
    known_unknown_before = _count_unknown_bucket_entries(buckets)
    known_nearest_before = nearest_stats.known_process
    _merge_unknown_entries_into_unique_known_bucket(
        buckets,
        matches,
        upstream_slot,
        module_key,
        nearest_stats,
    )
    known_unknown_after = _count_unknown_bucket_entries(buckets)
    logger.info(
        "LOGPARSE_PERF module2.merge_known_unknown module=%s slot=%s elapsed=%.3fs "
        "before_buckets=%d after_buckets=%d unknown_before=%d unknown_after=%d "
        "resolved_entries=%d nearest_resolved=%d",
        module_key,
        slot_label,
        time.perf_counter() - known_t0,
        known_buckets_before,
        len(buckets),
        known_unknown_before,
        known_unknown_after,
        max(0, known_unknown_before - known_unknown_after),
        nearest_stats.known_process - known_nearest_before,
    )

    projected_t0 = time.perf_counter()
    projected_buckets_before = len(buckets)
    projected_unknown_before = _count_unknown_bucket_entries(buckets)
    projected_nearest_before = nearest_stats.projected
    _merge_unknown_entries_into_unique_expanded_cycle_bucket(
        buckets,
        matches,
        upstream_slot,
        module_key,
        nearest_stats,
    )
    projected_unknown_after = _count_unknown_bucket_entries(buckets)
    logger.info(
        "LOGPARSE_PERF module2.merge_projected_unknown module=%s slot=%s elapsed=%.3fs "
        "before_buckets=%d after_buckets=%d unknown_before=%d unknown_after=%d "
        "resolved_entries=%d nearest_resolved=%d",
        module_key,
        slot_label,
        time.perf_counter() - projected_t0,
        projected_buckets_before,
        len(buckets),
        projected_unknown_before,
        projected_unknown_after,
        max(0, projected_unknown_before - projected_unknown_after),
        nearest_stats.projected - projected_nearest_before,
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
                if target.board_cycle is not None and _cycle_pair_ref(
                    cycle,
                    cpu_cycle,
                    entry.slot,
                ) == _cycle_pair_ref(target.board_cycle, target.cpu_cycle, entry.slot):
                    continue
                if _cycle_has_pid(cpu_cycle.processes, entry):
                    return True
            continue

        if target.board_cycle is not None and _cycle_pair_ref(
            cycle,
            None,
            entry.slot,
        ) == _cycle_pair_ref(target.board_cycle, None, entry.slot):
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
    seen: set[tuple[CycleRef, CycleRef | None]] = set()
    for board_cycle, cpu_cycle in matches:
        key = _cycle_pair_ref(board_cycle, cpu_cycle, entry.slot)
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
    matches: dict[str, _CycleMatch] | None = None,
    upstream_slot: MechSlotOutput | None = None,
    module_key: str = "module2",
    nearest_stats: _NearestResolutionStats | None = None,
) -> None:
    for specificity in (1, 0):
        candidates = _candidate_buckets_by_process_key(
            buckets,
            min_specificity=specificity + 1,
        )
        target_caches = _known_bucket_target_caches(candidates, upstream_slot)
        for board_cycle, cpu_cycle, entries in buckets:
            if _bucket_specificity(board_cycle, cpu_cycle) != specificity:
                continue

            remaining: list[MechLogEntry] = []
            for entry in entries:
                targets = candidates.get(_entry_process_key(entry), [])
                resolution = _resolve_candidate_by_nearest_time(
                    entry,
                    targets,
                    range_getter=lambda target: _known_bucket_cached_range(target, target_caches),
                    admissible_range_getter=lambda target: _known_bucket_cached_admissible_range(
                        target,
                        target_caches,
                    ),
                    summary_formatter=_format_known_bucket_target,
                )
                if resolution.target is not None:
                    resolution.target.entries.append(entry)
                    _update_known_bucket_target_cache(
                        target_caches[_known_bucket_cache_key(resolution.target)],
                        entry,
                    )
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
                        match = matches.get(_entry_ref(entry))
                        if match is not None:
                            original_reason = match.reason or "unknown"
                            match.reason = "no_unique_known_process_target"
                            match.detail = (
                                f"original_reason={original_reason} "
                                f"{resolution.detail} {match.detail}"
                            ).strip()
                    elif matches is not None:
                        match = matches.get(_entry_ref(entry))
                        if match is not None:
                            _append_match_detail(match, resolution.detail)
                remaining.append(entry)
            entries[:] = remaining

    buckets[:] = [bucket for bucket in buckets if bucket[2]]


def _merge_unknown_entries_into_unique_expanded_cycle_bucket(
    buckets: list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]],
    matches: dict[str, _CycleMatch],
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
            match = matches.get(_entry_ref(entry))
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
    board_projections: dict[CycleRef, _BoardProjection] = {}
    targets: list[_ProjectedCycleTarget] = []
    unknown_cpu_ids = _top_level_unknown_cpu_ids(buckets)
    needs_board_target = _has_top_level_board_unknown_entries(buckets)
    slot_id = (
        upstream_slot.slot_id
        if upstream_slot is not None
        else next(
            (
                entry.slot
                for _board, _cpu, entries in buckets
                for entry in entries
                if entry.slot
            ),
            "unknown",
        )
    )

    for board_cycle, cpu_cycle, entries in buckets:
        if board_cycle.dir_name == "unknown":
            continue

        board_ref, _ = _cycle_pair_ref(board_cycle, None, slot_id)
        projection = board_projections.get(board_ref)
        if projection is None:
            projection = _BoardProjection(
                board_cycle=board_cycle,
                entries=[],
                cpu_unknown_by_id={},
            )
            board_projections[board_ref] = projection
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
            ref=_target_ref(board_cycle, cpu_cycle, slot_id),
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
                ref=_target_ref(projection.board_cycle, None, slot_id),
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
                ref=_target_ref(projection.board_cycle, cpu_cycle, slot_id),
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
    raw_start, raw_end = _raw_projected_bounds(start_time, end_time, entries)
    return _clamp_projected_bounds(
        raw_start,
        raw_end,
        fallback_start=start_time,
        fallback_end=end_time,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    )


def _raw_projected_bounds(
    start_time: datetime | None,
    end_time: datetime | None,
    entries: list[MechLogEntry],
) -> tuple[datetime | None, datetime | None]:
    times = [time for time in [start_time, end_time] if time]
    times.extend(entry.timestamp for entry in entries if entry.timestamp)
    if not times:
        return None, None
    return min(times), max(times)


def _clamp_projected_bounds(
    start_time: datetime | None,
    end_time: datetime | None,
    fallback_start: datetime | None,
    fallback_end: datetime | None,
    lower_bound: datetime | None = None,
    upper_bound: datetime | None = None,
) -> tuple[datetime | None, datetime | None]:
    if start_time is None or end_time is None:
        return None, None
    start = start_time
    end = end_time
    if lower_bound is not None and start < lower_bound:
        start = lower_bound
    if upper_bound is not None and end > upper_bound:
        end = upper_bound
    if start > end:
        return fallback_start, fallback_end
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
    admissible_refs = {_candidate_ref(candidate) for candidate in admissible_candidates}
    formatted = []
    for candidate in candidates[:3]:
        start_time, end_time = range_getter(candidate)
        lower_bound, upper_bound = admissible_range_getter(candidate)
        if timestamp is None:
            distance = None
            admissible = False
        else:
            distance = _time_distance(start_time, end_time, timestamp)
            admissible = _candidate_ref(candidate) in admissible_refs
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


def _candidate_ref(candidate: Any) -> str:
    ref = getattr(candidate, "ref", "")
    if ref:
        return str(ref)
    return repr(candidate)


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


def _known_bucket_target_caches(
    candidates: dict[tuple[str, str, str], list[_KnownBucketTarget]],
    upstream_slot: MechSlotOutput | None,
) -> dict[str, _KnownBucketTargetCache]:
    caches: dict[str, _KnownBucketTargetCache] = {}
    for targets in candidates.values():
        for target in targets:
            key = _known_bucket_cache_key(target)
            if key not in caches:
                caches[key] = _build_known_bucket_target_cache(target, upstream_slot)
    return caches


def _build_known_bucket_target_cache(
    target: _KnownBucketTarget,
    upstream_slot: MechSlotOutput | None,
) -> _KnownBucketTargetCache:
    lower_bound, upper_bound = _extension_limits_for_target(
        target.board_cycle,
        target.cpu_cycle,
        upstream_slot,
    )
    base_start, base_end = _target_base_range(target.board_cycle, target.cpu_cycle)
    raw_start, raw_end = _raw_projected_bounds(base_start, base_end, target.entries)
    range_start, range_end = _clamp_projected_bounds(
        raw_start,
        raw_end,
        fallback_start=base_start,
        fallback_end=base_end,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    )
    return _KnownBucketTargetCache(
        target=target,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        base_start=base_start,
        base_end=base_end,
        raw_start=raw_start,
        raw_end=raw_end,
        range_start=range_start,
        range_end=range_end,
    )


def _known_bucket_cached_range(
    target: _KnownBucketTarget,
    caches: dict[str, _KnownBucketTargetCache],
) -> tuple[datetime | None, datetime | None]:
    cache = caches[_known_bucket_cache_key(target)]
    return cache.range_start, cache.range_end


def _known_bucket_cached_admissible_range(
    target: _KnownBucketTarget,
    caches: dict[str, _KnownBucketTargetCache],
) -> tuple[datetime | None, datetime | None]:
    cache = caches[_known_bucket_cache_key(target)]
    return cache.lower_bound, cache.upper_bound


def _update_known_bucket_target_cache(
    cache: _KnownBucketTargetCache,
    entry: MechLogEntry,
) -> None:
    if entry.timestamp is None:
        return
    if cache.raw_start is None or entry.timestamp < cache.raw_start:
        cache.raw_start = entry.timestamp
    if cache.raw_end is None or entry.timestamp > cache.raw_end:
        cache.raw_end = entry.timestamp
    cache.range_start, cache.range_end = _clamp_projected_bounds(
        cache.raw_start,
        cache.raw_end,
        fallback_start=cache.base_start,
        fallback_end=cache.base_end,
        lower_bound=cache.lower_bound,
        upper_bound=cache.upper_bound,
    )


def _known_bucket_cache_key(target: _KnownBucketTarget) -> str:
    return target.ref


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
            slot_id = entry.slot or _entries_slot_label(entries)
            candidates[key].append(
                _KnownBucketTarget(
                    board_cycle,
                    cpu_cycle,
                    entries,
                    ref=_target_ref(
                        board_cycle,
                        cpu_cycle,
                        slot_id,
                        discriminator="process:" + "|".join(key),
                    ),
                )
            )

    return candidates


def _entries_slot_label(entries: list[MechLogEntry]) -> str:
    slots = sorted({entry.slot for entry in entries if entry.slot})
    if not slots:
        return "<empty>"
    return ",".join(slots)


def _count_unknown_bucket_entries(
    buckets: list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]],
) -> int:
    return sum(
        len(entries)
        for board_cycle, cpu_cycle, entries in buckets
        if _bucket_specificity(board_cycle, cpu_cycle) < 2
    )


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
    matches: dict[str, _CycleMatch],
) -> None:
    for board_cycle, cpu_cycle, entries in buckets:
        if _bucket_specificity(board_cycle, cpu_cycle) >= 2:
            continue
        for entry in entries:
            match = matches.get(_entry_ref(entry), _CycleMatch(reason="unknown", detail=""))
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
    matches: dict[str, _CycleMatch] | None = None,
    module_key: str = "module2",
) -> list[MechBoardCycle]:
    cycles: list[MechBoardCycle] = []
    board_by_key: dict[CycleRef, MechBoardCycle] = {}
    cpu_by_key: dict[tuple[CycleRef, CycleRef], MechCpuCycle] = {}
    slot_id = (
        upstream_slot.slot_id
        if upstream_slot is not None
        else next(
            (
                entry.slot
                for _board, _cpu, entries in grouped
                for entry in entries
                if entry.slot
            ),
            "unknown",
        )
    )

    for board_template, cpu_template, entries in grouped:
        board_key, cpu_ref = _cycle_pair_ref(board_template, cpu_template, slot_id)
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

        assert cpu_ref is not None
        cpu_key = (board_key, cpu_ref)
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
    matches: dict[str, _CycleMatch] | None = None,
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
    matches: dict[str, _CycleMatch],
    proposed_start: datetime,
    proposed_end: datetime,
    clamped_start: datetime,
    clamped_end: datetime,
) -> None:
    for entry in entries:
        match = matches.get(_entry_ref(entry))
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
