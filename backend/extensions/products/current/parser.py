"""当前产品解析扩展：时间戳、ActivePeriod、机制模块和角色投影。"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from backend.application.mechanism_execution import MechanismExecutionService
from backend.extensions.products.current.mechanism_input import CurrentMechanismInput
from backend.models import (
    BoardRole,
    LogEntry,
    MechResult,
    ParseResult,
)
from backend.performance import resolve_worker_count
from backend.parsing.active_period_builder import ActivePeriodBuilder
from backend.parsing.file_iter import iter_log_entry_lines
from backend.parsing.mech_entry_dedup import dedupe_mech_entries
from backend.parsing.output_writer import MechOutputWriter
from backend.parsing.role_identifier import RoleIdentifier
from backend.parsing.timestamp_extractor import TimestampExtractor
from backend.plugins.base import LogParserPlugin

# 保持旧 logger 名称，避免迁移期间破坏现有运维过滤器。
logger = logging.getLogger("backend.plugins.default.parser")


@dataclass(slots=True)
class CurrentDiagnosticSource:
    """Green-zone adapter from current product logs to the scan port."""

    scope_value: str
    entry: LogEntry

    @property
    def key(self) -> str:
        return f"slot_{self.scope_value}/{self.entry.name}"

    @property
    def payload(self) -> LogEntry:
        return self.entry

    def iter_lines(self) -> Iterable[str]:
        return iter_log_entry_lines(self.entry)

    def timestamps(self) -> Sequence[Any]:
        return self.entry.content_timestamps

    def replace_timestamps(self, values: Sequence[Any]) -> None:
        self.entry.content_timestamps = list(values)


class ParserPlugin(LogParserPlugin):
    """标准日志解析：时间戳→ActivePeriod→编排机制模块插件→兜底主控判定。"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._compile_patterns()
        self._ts_extractor = TimestampExtractor(self._ts_regex)
        self._active_period_builder = ActivePeriodBuilder(self._gap_threshold)
        self.last_mechanism_diagnostics = ()

    def _compile_patterns(self) -> None:
        self._ts_regex = re.compile(
            self.config.get("timestamp_regex",
                           r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?")
        )
        self._gap_threshold = int(self.config.get("active_period_gap_threshold", 300))
        self._mech_modules = self.config.get("mechanism_modules", {})

    # ── parse() ───────────────────────────────────────────

    def parse(self, result: ParseResult) -> ParseResult:
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
        runtime = MechanismExecutionService(
            self._mech_modules,
            timestamp_extractor=self._ts_extractor,
            workers=worker_count,
            performance_recorder=getattr(self, "performance_recorder", None),
            logger=logger,
            entry_deduplicator=dedupe_mech_entries,
        )
        mechanism_plugins = runtime.load_plugins()
        for mechanism in mechanism_plugins:
            logger.info(
                "[%s] 已加载 %s dependencies=%s",
                mechanism.module_key,
                mechanism.__class__.__module__ + "." + mechanism.__class__.__name__,
                list(mechanism.descriptor.dependencies),
            )

        sources = [
            CurrentDiagnosticSource(slot.slot_id, entry)
            for slot in result.diagnostic_slots
            for entry in slot.diagnostic_logs
        ]
        t0 = time.perf_counter()
        scan_batch = runtime.scan(
            sources,
            mechanism_plugins,
            error_sink=result.errors.append,
        )
        self.last_scan_batch = scan_batch
        elapsed = time.perf_counter() - t0
        logger.info(
            "LOGPARSE_PERF parser.timestamps elapsed=%.3fs diag_files=%d timestamps=%d",
            elapsed,
            scan_batch.file_count,
            sum(len(values) for values in scan_batch.timestamps_by_source.values()),
        )

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

        def _accept_outcome(mechanism: Any, outcome) -> None:
            mech: MechResult = outcome.result
            result.mech_results.append(mech)
            if outcome.role_signals:
                for slot in result.diagnostic_slots:
                    signal = outcome.role_signals.get(slot.slot_id)
                    if signal in {item.value for item in BoardRole}:
                        slot.role = BoardRole(signal)
            elif getattr(mechanism, "requires_legacy_parse_state", False):
                mechanism.apply_roles(result, mech)
            logger.info(
                "[%s] 诊断:%d journal:%d scopes:%d",
                mech.module_name or mechanism.module_key,
                mech.diag_entry_count,
                mech.journal_entry_count,
                len(mech.slots),
            )

        execution_outcomes = runtime.execute(
            result,
            CurrentMechanismInput.from_collections(
                result.diagnostic_slots,
                result.private_slots,
            ),
            mechanism_plugins,
            scan_batch,
            error_sink=result.errors.append,
            outcome_sink=_accept_outcome,
        )
        self.last_mechanism_diagnostics = tuple(
            diagnostic
            for _mechanism, outcome in execution_outcomes
            for diagnostic in outcome.diagnostics
        )

        t0 = time.perf_counter()
        RoleIdentifier.fallback_roles(result)
        elapsed = time.perf_counter() - t0
        logger.info(
            "LOGPARSE_PERF parser.roles elapsed=%.3fs slots=%d",
            elapsed,
            len(result.diagnostic_slots),
        )

        return result

    def write_output(
        self, mech_result: MechResult, output_dir: Path,
    ) -> Path:
        return MechOutputWriter().write(mech_result, output_dir)
