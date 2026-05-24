"""默认日志解析插件：提取时间戳、ActivePeriod、机制模块解析、主控判定。"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config_validation import validate_mechanism_module_config
from backend.models import (
    MechLogEntry,
    MechResult,
    MechSlotOutput,
    LogEntry,
    ParseResult,
    PrivateSlotInfo,
    SlotInfo,
)
from backend.parsing.active_period_builder import ActivePeriodBuilder
from backend.parsing.cycle_detector import CycleDetector
from backend.parsing.mech_diag_scanner import MechDiagScanner
from backend.parsing.mech_journal_scanner import MechJournalScanner
from backend.parsing.output_writer import MechOutputWriter
from backend.parsing.process_name_resolver import ProcessNameResolver
from backend.parsing.role_identifier import RoleIdentifier
from backend.parsing.timestamp_extractor import TimestampExtractor
from backend.plugins.base import LogParserPlugin

logger = logging.getLogger(__name__)


class ParserPlugin(LogParserPlugin):
    """标准日志解析：时间戳→ActivePeriod→机制模块→主控判定。"""

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
        # 1. 提取所有内容时间戳
        self._extract_all_timestamps(result.diagnostic_slots)

        # 2. 构建 ActivePeriod
        for slot in result.diagnostic_slots:
            for p in self._active_period_builder.build(slot):
                slot.add_active_period(p)

        # 3. 机制模块解析
        results: dict[str, MechResult] = {}
        for module_key, cfg in self._mech_modules.items():
            if not cfg.get("enabled", True):
                continue

            errors = validate_mechanism_module_config(module_key, cfg)
            if errors:
                result.errors.extend(errors)
                continue

            mech = self._parse_one_mech(result, cfg, module_key)
            if mech:
                results[module_key] = mech

        # 4. 主控判定：机制模块优先，兜底 Identifier 逻辑
        for module_key, mech in results.items():
            result.mech_results.append(mech)
            RoleIdentifier.apply_mech_roles(mech, result)

        RoleIdentifier.fallback_roles(result)

        return result

    # ── 时间戳提取 ────────────────────────────────────────

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

    # ── 机制模块解析 ──────────────────────────────────────

    def _parse_one_mech(
        self, result: ParseResult, cfg: dict[str, Any], module_key: str,
    ) -> MechResult | None:
        module_name: str = cfg["module_name"]
        mod_upper = module_name.upper()

        diag_re = re.compile(cfg["diag_pattern"]) if cfg.get("diag_pattern") else None
        if diag_re:
            required = {"Slot", "CPU_Id", "ProcessName", "Context"}
            if not required.issubset(diag_re.groupindex):
                missing = required - set(diag_re.groupindex)
                raise ValueError(f"{module_key} diag_pattern 缺少命名组: {sorted(missing)}")

        jnl_cfg: dict = cfg.get("journal", {})
        journal_re = re.compile(jnl_cfg["line_pattern"]) if jnl_cfg.get("line_pattern") else None
        journal_re2 = re.compile(jnl_cfg["line_pattern2"]) if jnl_cfg.get("line_pattern2") else None
        journal_keyword = (jnl_cfg.get("identifying_keyword", "").lower()
                           if jnl_cfg.get("identifying_keyword") else None)
        seq_re = re.compile(cfg.get("sequence_pattern", r"No\[(\d+)\]"))
        master_keyword = (re.compile(cfg["active_master_keyword"])
                          if cfg.get("active_master_keyword") else None)
        indicator = (cfg.get("board_restart_indicator", "").lower()
                     if cfg.get("board_restart_indicator") else None)
        whitelist = cfg.get("board_restart_whitelist", [])
        name_map: dict[str, str] = cfg.get("process_name_mapping", {})

        # 白名单进程不能出现在映射表中（白名单要求不重名，映射表进程存在重名）
        whitelist_set = {w.lower() for w in whitelist}
        map_keys = {k.lower() for k in name_map}
        conflict = whitelist_set & map_keys
        if conflict:
            raise ValueError(
                f"白名单进程不能同时配置在 process_name_mapping 中"
                f"（白名单要求不重名，mapping 进程存在重名）: {sorted(conflict)}"
            )

        all_entries: list[MechLogEntry] = []
        resolver = ProcessNameResolver(name_map)

        # 扫描诊断日志
        if diag_re:
            diag_scanner = MechDiagScanner(
                diag_re, seq_re, master_keyword, resolver,
                mod_upper, self._ts_extractor,
            )
            for slot in result.diagnostic_slots:
                for log_entry in slot.diagnostic_logs:
                    all_entries.extend(diag_scanner.scan(log_entry, slot.slot_id))

        # 诊断日志时区，供 journal 扫描时即时归一化
        diag_tz = None
        for e in all_entries:
            if e.timestamp and e.timestamp.tzinfo:
                diag_tz = e.timestamp.tzinfo
                break

        # 扫描 journal 日志
        if (journal_re or journal_re2) and journal_keyword:
            journal_scanner = MechJournalScanner(
                journal_re, journal_re2, journal_keyword,
                seq_re, master_keyword, resolver, indicator,
                mod_upper, self._ts_extractor,
            )
            for ps in result.private_slots:
                all_entries.extend(journal_scanner.scan(ps, diag_tz))

        if not all_entries:
            return None

        # 全局时区归一化
        tzinfo = None
        for e in all_entries:
            if e.timestamp and e.timestamp.tzinfo:
                tzinfo = e.timestamp.tzinfo
                break
        if tzinfo:
            for e in all_entries:
                if e.timestamp and e.timestamp.tzinfo is None:
                    e.timestamp = e.timestamp.replace(tzinfo=tzinfo)

        # 按 slot 分组
        by_slot: dict[str, list[MechLogEntry]] = defaultdict(list)
        for e in all_entries:
            by_slot[e.slot].append(e)

        mech_result = MechResult(module_name=module_name)
        for slot_id, entries in sorted(by_slot.items()):
            slot_output = MechSlotOutput(slot_id=slot_id)
            detector = CycleDetector(indicator=indicator, whitelist=whitelist)
            slot_output.board_cycles = detector.detect(entries)
            mech_result.slots.append(slot_output)

        active_slots: set[str] = set()
        for e in all_entries:
            if e.is_active_signal:
                active_slots.add(e.slot)
        mech_result.active_master_slots = sorted(active_slots)

        mech_result.diag_entry_count = sum(1 for e in all_entries if e.source == "diagnostic")
        mech_result.journal_entry_count = sum(1 for e in all_entries if e.source == "journal")

        return mech_result

    # ── 输出落盘 ──────────────────────────────────────────

    def write_output(
        self, mech_result: MechResult, output_dir: Path,
    ) -> Path:
        return MechOutputWriter().write(mech_result, output_dir)
