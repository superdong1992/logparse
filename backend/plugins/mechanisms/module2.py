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

    return buckets


def _find_matching_cycle(
    entry: MechLogEntry,
    upstream_slot: MechSlotOutput | None,
) -> tuple[MechBoardCycle | None, MechCpuCycle | None]:
    if upstream_slot is None or entry.timestamp is None:
        return None, None
    for cycle in upstream_slot.board_cycles:
        if cycle.start_time is None or cycle.end_time is None:
            continue
        if cycle.start_time <= entry.timestamp <= cycle.end_time:
            if entry.cpu_id:
                for cpu_cycle in cycle.cpu_cycles:
                    if cpu_cycle.cpu_id != entry.cpu_id:
                        continue
                    if cpu_cycle.start_time is None or cpu_cycle.end_time is None:
                        continue
                    if cpu_cycle.start_time <= entry.timestamp <= cpu_cycle.end_time:
                        return cycle, cpu_cycle
            return cycle, None
    return None, None


def _build_cycles(
    grouped: list[tuple[MechBoardCycle, MechCpuCycle | None, list[MechLogEntry]]],
) -> list[MechBoardCycle]:
    cycles: list[MechBoardCycle] = []

    for board_template, cpu_template, entries in grouped:
        board_cycle = next((c for c in cycles if c.dir_name == board_template.dir_name), None)
        if board_cycle is None:
            board_cycle = MechBoardCycle(
                dir_name=board_template.dir_name,
                start_time=board_template.start_time,
                end_time=board_template.end_time,
            )
            cycles.append(board_cycle)

        if cpu_template is None:
            board_cycle.processes.extend(_build_processes(entries))
            continue

        cpu_cycle = next(
            (
                c for c in board_cycle.cpu_cycles
                if c.cpu_id == cpu_template.cpu_id and c.dir_name == cpu_template.dir_name
            ),
            None,
        )
        if cpu_cycle is None:
            cpu_cycle = MechCpuCycle(
                cpu_id=cpu_template.cpu_id,
                dir_name=cpu_template.dir_name,
                start_time=cpu_template.start_time,
                end_time=cpu_template.end_time,
            )
            board_cycle.cpu_cycles.append(cpu_cycle)
        cpu_cycle.processes.extend(_build_processes(entries))

    return cycles


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
