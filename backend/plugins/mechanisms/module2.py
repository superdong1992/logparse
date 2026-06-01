"""Module 2 mechanism plugin."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

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
            grouped = _assign_entries_to_cycles(slot_entries, upstream_slot)
            slot_output.board_cycles = _build_cycles(grouped)
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
) -> list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]]:
    buckets: list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]] = []
    unknown = MechBoardCycle(dir_name="unknown")

    for entry in entries:
        board_cycle, cpu_cycle = _find_matching_cycle(entry, upstream_slot)
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

    _merge_unknown_entries_into_unique_known_bucket(buckets)
    return buckets


def _find_matching_cycle(
    entry: MechLogEntry,
    upstream_slot: MechSlotOutput | None,
) -> tuple[MechBoardCycle | None, MechCpuCycle | None]:
    if upstream_slot is None or entry.timestamp is None:
        return None, None

    pid_match = _find_pid_matching_cycle(entry, upstream_slot)
    if pid_match != (None, None):
        return pid_match

    return _find_time_matching_cycle(entry, upstream_slot)


def _find_pid_matching_cycle(
    entry: MechLogEntry,
    upstream_slot: MechSlotOutput,
) -> tuple[MechBoardCycle | None, MechCpuCycle | None]:
    if not entry.pid:
        return None, None

    exact_matches: list[tuple[MechBoardCycle, MechCpuCycle | None]] = []
    pid_matches: list[tuple[MechBoardCycle, MechCpuCycle | None]] = []

    for cycle in upstream_slot.board_cycles:
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


def _find_time_matching_cycle(
    entry: MechLogEntry,
    upstream_slot: MechSlotOutput,
) -> tuple[MechBoardCycle | None, MechCpuCycle | None]:
    for cycle in upstream_slot.board_cycles:
        if not _contains_time(cycle.start_time, cycle.end_time, entry.timestamp):
            continue
        if entry.cpu_id:
            for cpu_cycle in cycle.cpu_cycles:
                if cpu_cycle.cpu_id != entry.cpu_id:
                    continue
                if _contains_time(cpu_cycle.start_time, cpu_cycle.end_time, entry.timestamp):
                    return cycle, cpu_cycle
            return cycle, None
        return cycle, None
    return None, None


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
                if len(targets) == 1:
                    targets[0].append(entry)
                else:
                    remaining.append(entry)
            entries[:] = remaining

    buckets[:] = [bucket for bucket in buckets if bucket[2]]


def _candidate_buckets_by_process_key(
    buckets: list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]],
    min_specificity: int,
) -> dict[tuple[str, str, str], list[list[MechLogEntry]]]:
    candidates: dict[tuple[str, str, str], list[list[MechLogEntry]]] = defaultdict(list)
    for board_cycle, cpu_cycle, entries in buckets:
        if _bucket_specificity(board_cycle, cpu_cycle) < min_specificity:
            continue

        seen: set[tuple[str, str, str]] = set()
        for entry in entries:
            key = _entry_process_key(entry)
            if key in seen:
                continue
            seen.add(key)
            candidates[key].append(entries)

    return candidates


def _bucket_specificity(board_cycle: MechBoardCycle, cpu_cycle: MechCpuCycle | None) -> int:
    if board_cycle.dir_name == "unknown":
        return 0
    if cpu_cycle is not None and cpu_cycle.dir_name == "unknown":
        return 1
    return 2


def _entry_process_key(entry: MechLogEntry) -> tuple[str, str, str]:
    return entry.process_name, entry.pid, entry.cpu_id or ""


def _build_cycles(
    grouped: list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]],
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
        _extend_cycle_bounds(board_cycle, entries)

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
        _extend_cycle_bounds(cpu_cycle, entries)
        cpu_cycle.processes.extend(_build_processes(entries))

    return cycles


def _extend_cycle_bounds(
    cycle: MechBoardCycle | MechCpuCycle,
    entries: list[MechLogEntry],
) -> None:
    if cycle.dir_name == "unknown":
        return

    times = [entry.timestamp for entry in entries if entry.timestamp]
    candidates = [time for time in [cycle.start_time, cycle.end_time, *times] if time]
    if not candidates:
        return

    cycle.start_time = min(candidates)
    cycle.end_time = max(candidates)
    cycle.dir_name = _format_cycle_dir(cycle.start_time, cycle.end_time)


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
