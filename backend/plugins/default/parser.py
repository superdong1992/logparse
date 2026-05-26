"""默认日志解析插件：提取时间戳、ActivePeriod、编排机制模块插件、兜底主控判定。"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from backend.models import (
    LogEntry,
    MechResult,
    ParseResult,
    SlotInfo,
)
from backend.parsing.active_period_builder import ActivePeriodBuilder
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
        # 1. 提取所有内容时间戳
        self._extract_all_timestamps(result.diagnostic_slots)

        # 2. 构建 ActivePeriod
        for slot in result.diagnostic_slots:
            for p in self._active_period_builder.build(slot):
                slot.add_active_period(p)

        # 3. 加载并编排机制模块插件
        mechanism_plugins: list[MechanismModulePlugin] = []
        for module_key, module_entry in self._mech_modules.items():
            if not module_entry.get("enabled", True):
                continue

            plugin = instantiate_plugin(
                module_entry["plugin"],
                MechanismModulePlugin,
                module_entry.get("config", {}),
                module_key=module_key,
                ts_extractor=self._ts_extractor,
            )
            mechanism_plugins.append(plugin)

        for mechanism in mechanism_plugins:
            mech = mechanism.parse(result)
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

    # ── 输出落盘 ──────────────────────────────────────────

    def write_output(
        self, mech_result: MechResult, output_dir: Path,
    ) -> Path:
        return MechOutputWriter().write(mech_result, output_dir)
