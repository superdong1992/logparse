"""默认日志解析插件：提取时间戳、ActivePeriod、编排机制模块插件、兜底主控判定。"""

from __future__ import annotations

import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from backend.models import (
    LogEntry,
    MechResult,
    ParseResult,
    SlotInfo,
)
from backend.performance import resolve_worker_count
from backend.parsing.active_period_builder import ActivePeriodBuilder
from backend.parsing.file_iter import iter_log_entry_lines
from backend.parsing.output_writer import MechOutputWriter
from backend.parsing.role_identifier import RoleIdentifier
from backend.parsing.timestamp_extractor import TimestampExtractor
from backend.plugins.base import LogParserPlugin
from backend.plugins.loader import instantiate_plugin
from backend.plugins.mechanisms.base import MechanismModulePlugin

logger = logging.getLogger(__name__)


class ParserPlugin(LogParserPlugin):
    """标准日志解析：时间戳→ActivePeriod→编排机制模块插件→兜底主控判定。"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._compile_patterns()
        self._ts_extractor = TimestampExtractor(self._ts_regex)
        self._active_period_builder = ActivePeriodBuilder(self._gap_threshold)

    def _compile_patterns(self) -> None:
        self._ts_regex = re.compile(
            self.config.get("timestamp_regex",
                           r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?")
        )
        self._gap_threshold = int(self.config.get("active_period_gap_threshold", 300))
        self._mech_modules = self.config.get("mechanism_modules", {})

    # ── parse() ───────────────────────────────────────────

    def parse(self, result: ParseResult) -> ParseResult:
        # 1. 加载并编排机制模块插件
        mechanism_plugins: list[MechanismModulePlugin] = []
        for module_key, module_entry in self._mech_modules.items():
            if not module_entry.get("enabled", True):
                logger.info("[%s] 已禁用，跳过", module_key)
                continue

            plugin = instantiate_plugin(
                module_entry["plugin"],
                MechanismModulePlugin,
                module_entry.get("config", {}),
                module_key=module_key,
                ts_extractor=self._ts_extractor,
            )
            mechanism_plugins.append(plugin)
            logger.info("[%s] 已加载 %s", module_key, module_entry["plugin"])

        # 2. 单次共享扫描诊断日志，产出 timestamps 和机制模块诊断条目
        t0 = time.perf_counter()
        self._shared_diagnostic_scan(result, mechanism_plugins)
        elapsed = time.perf_counter() - t0
        diag_file_count = sum(len(slot.diagnostic_logs) for slot in result.diagnostic_slots)
        ts_total = sum(
            len(entry.content_timestamps)
            for slot in result.diagnostic_slots
            for entry in slot.diagnostic_logs
        )
        logger.info(
            "LOGPARSE_PERF parser.timestamps elapsed=%.3fs diag_files=%d timestamps=%d",
            elapsed,
            diag_file_count,
            ts_total,
        )

        # 2. 构建 ActivePeriod
        t0 = time.perf_counter()
        for slot in result.diagnostic_slots:
            for p in self._active_period_builder.build(slot):
                slot.add_active_period(p)
        elapsed = time.perf_counter() - t0
        period_total = sum(len(slot.active_periods) for slot in result.diagnostic_slots)
        logger.info(
            "LOGPARSE_PERF parser.active_periods elapsed=%.3fs slots=%d periods=%d",
            elapsed,
            len(result.diagnostic_slots),
            period_total,
        )

        for mechanism in mechanism_plugins:
            t0 = time.perf_counter()
            try:
                mech = mechanism.parse(result)
            except Exception as e:
                elapsed = time.perf_counter() - t0
                logger.info(
                    "LOGPARSE_PERF parser.module module=%s elapsed=%.3fs result=error",
                    mechanism.module_key,
                    elapsed,
                )
                logger.warning("[%s] parse 异常: %s", mechanism.module_key, e)
                result.errors.append(f"[{mechanism.module_key}] parse 异常: {e}")
                continue
            elapsed = time.perf_counter() - t0
            if mech:
                logger.info(
                    "LOGPARSE_PERF parser.module module=%s elapsed=%.3fs result=yes "
                    "diag_entries=%d journal_entries=%d slots=%d",
                    mechanism.module_key,
                    elapsed,
                    mech.diag_entry_count,
                    mech.journal_entry_count,
                    len(mech.slots),
                )
            else:
                logger.info(
                    "LOGPARSE_PERF parser.module module=%s elapsed=%.3fs result=no "
                    "diag_entries=0 journal_entries=0 slots=0",
                    mechanism.module_key,
                    elapsed,
                )
            if mech:
                result.mech_results.append(mech)
                mechanism.apply_roles(result, mech)
                logger.info(
                    "[%s] 诊断:%d journal:%d slots:%d",
                    mech.module_name or mechanism.module_key,
                    mech.diag_entry_count,
                    mech.journal_entry_count,
                    len(mech.slots),
                )
            else:
                logger.info(
                    "[%s] 未产出结果 (errors: %d)",
                    mechanism.module_key,
                    len(result.errors),
                )

        # 4. 兜底主控判定
        t0 = time.perf_counter()
        RoleIdentifier.fallback_roles(result)
        elapsed = time.perf_counter() - t0
        logger.info(
            "LOGPARSE_PERF parser.roles elapsed=%.3fs slots=%d",
            elapsed,
            len(result.diagnostic_slots),
        )

        return result

    # ── 时间戳提取 ────────────────────────────────────────

    def _shared_diagnostic_scan(
        self,
        result: ParseResult,
        mechanism_plugins: list[MechanismModulePlugin],
    ) -> None:
        slots = result.diagnostic_slots
        scanner_items: list[tuple[str, Callable[[str, LogEntry, str], Any]]] = []
        for mechanism in mechanism_plugins:
            try:
                scanner = mechanism.build_diagnostic_line_scanner()
            except Exception as exc:
                result.errors.append(
                    f"[{mechanism.module_key}] shared diagnostic scanner setup failed: {exc}"
                )
                continue
            if callable(scanner):
                scanner_items.append((mechanism.module_key, scanner))

        log_tasks = [
            (slot.slot_id, entry)
            for slot in slots
            for entry in slot.diagnostic_logs
        ]
        pipeline_cfg = self.config.get("_pipeline", {})
        worker_count = int(
            pipeline_cfg.get(
                "diagnostic_scan_workers_resolved",
                resolve_worker_count(
                    pipeline_cfg.get("diagnostic_scan_workers", "auto"),
                    default_cap=4,
                ),
            )
        )

        scan_t0 = time.perf_counter()
        if worker_count > 1 and len(log_tasks) > 1:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                scan_results = list(
                    executor.map(
                        lambda task: self._scan_diagnostic_log(task, scanner_items),
                        log_tasks,
                    )
                )
        else:
            scan_results = [
                self._scan_diagnostic_log(task, scanner_items)
                for task in log_tasks
            ]

        entries_by_module: dict[str, list[Any]] = {
            module_key: [] for module_key, _scanner in scanner_items
        }
        total_lines = 0
        total_timestamps = 0
        seen_errors: set[str] = set()
        for entry, timestamps, module_entries, line_count, scan_errors in scan_results:
            entry.content_timestamps = self._sort_timestamps(timestamps)
            total_lines += line_count
            total_timestamps += len(timestamps)
            for module_key, entries in module_entries.items():
                entries_by_module[module_key].extend(entries)
            for error in scan_errors:
                if error not in seen_errors:
                    seen_errors.add(error)
                    result.errors.append(error)

        self._normalize_timestamp_timezones(slots)
        for slot in slots:
            for entry in slot.diagnostic_logs:
                entry.content_timestamps = self._sort_timestamps(entry.content_timestamps)

        module_keys_with_scanners = set(entries_by_module)
        for mechanism in mechanism_plugins:
            if mechanism.module_key not in module_keys_with_scanners:
                continue
            mechanism.set_precomputed_diagnostic_entries(
                entries_by_module.get(mechanism.module_key, []),
                file_count=len(log_tasks),
                line_count=total_lines,
            )

        recorder = getattr(self, "performance_recorder", None)
        if recorder:
            metrics = {
                "files": len(log_tasks),
                "lines": total_lines,
                "timestamps": total_timestamps,
            }
            for module_key, entries in sorted(entries_by_module.items()):
                metrics[f"{module_key}_entries"] = len(entries)
            recorder.record_stage(
                "diagnostic_scan.shared",
                elapsed_seconds=time.perf_counter() - scan_t0,
                **metrics,
            )

    def _scan_diagnostic_log(
        self,
        task: tuple[str, LogEntry],
        scanner_items: list[tuple[str, Callable[[str, LogEntry, str], Any]]],
    ) -> tuple[LogEntry, list[Any], dict[str, list[Any]], int, list[str]]:
        slot_id, log_entry = task
        timestamps: list[Any] = []
        entries_by_module: dict[str, list[Any]] = {
            module_key: [] for module_key, _scanner in scanner_items
        }
        line_count = 0
        reported_failures: set[str] = set()
        scan_errors: list[str] = []

        for line in iter_log_entry_lines(log_entry):
            line_count += 1
            timestamps.extend(self._ts_extractor.extract_from_text(line))
            for module_key, scanner in scanner_items:
                try:
                    entry = scanner(line, log_entry, slot_id)
                except Exception as exc:
                    error = (
                        f"[{module_key}] shared diagnostic scan failed "
                        f"in slot_{slot_id}/{log_entry.name}: {exc}"
                    )
                    if error not in reported_failures:
                        reported_failures.add(error)
                        scan_errors.append(error)
                    continue
                if entry:
                    entries_by_module[module_key].append(entry)

        return log_entry, timestamps, entries_by_module, line_count, scan_errors

    @staticmethod
    def _sort_timestamps(timestamps: list[Any]) -> list[Any]:
        try:
            return sorted(timestamps)
        except TypeError:
            return list(timestamps)

    def _extract_all_timestamps(self, slots: list[SlotInfo]) -> None:
        for slot in slots:
            for entry in slot.diagnostic_logs:
                entry.content_timestamps = self._ts_extractor.extract_from_entry(entry)
        # 时区归一化：在排序/构建 ActivePeriod 前，将 naive datetime 统一时区
        tzinfo = None
        for slot in slots:
            for entry in slot.diagnostic_logs:
                for ts in entry.content_timestamps:
                    if ts.tzinfo:
                        tzinfo = ts.tzinfo
                        break
                if tzinfo:
                    break
            if tzinfo:
                break
        if tzinfo:
            for slot in slots:
                for entry in slot.diagnostic_logs:
                    entry.content_timestamps = [
                        ts.replace(tzinfo=tzinfo) if ts.tzinfo is None else ts
                        for ts in entry.content_timestamps
                    ]

    # ── 输出落盘 ──────────────────────────────────────────

    def _normalize_timestamp_timezones(self, slots: list[SlotInfo]) -> None:
        tzinfo = None
        for slot in slots:
            for entry in slot.diagnostic_logs:
                for ts in entry.content_timestamps:
                    if ts.tzinfo:
                        tzinfo = ts.tzinfo
                        break
                if tzinfo:
                    break
            if tzinfo:
                break
        if tzinfo:
            for slot in slots:
                for entry in slot.diagnostic_logs:
                    entry.content_timestamps = [
                        ts.replace(tzinfo=tzinfo) if ts.tzinfo is None else ts
                        for ts in entry.content_timestamps
                    ]

    def write_output(
        self, mech_result: MechResult, output_dir: Path,
    ) -> Path:
        return MechOutputWriter().write(mech_result, output_dir)
