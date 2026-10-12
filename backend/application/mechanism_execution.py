"""Product-neutral mechanism planning, shared scanning, and execution."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from backend.contracts.plugins import (
    DiagnosticScanBatch,
    MechanismContext,
    MechanismOutcome,
)
from backend.plugins.loader import instantiate_mechanism_plugins
from backend.contracts.runtime import Diagnostic, DiagnosticSeverity


class DiagnosticSourcePort(Protocol):
    """A product adapter over one scannable diagnostic source."""

    @property
    def key(self) -> str: ...

    @property
    def scope_value(self) -> str: ...

    @property
    def payload(self) -> Any: ...

    def iter_lines(self) -> Iterable[str]: ...

    def timestamps(self) -> Sequence[Any]: ...

    def replace_timestamps(self, values: Sequence[Any]) -> None: ...


class MechanismExecutionService:
    """Own the frozen orchestration while extensions own source semantics."""

    def __init__(
        self,
        entries: Mapping[str, Any],
        *,
        timestamp_extractor: Any,
        workers: int = 1,
        performance_recorder: Any = None,
        logger: Any = None,
        entry_deduplicator: Callable[[list[Any]], list[Any]] | None = None,
    ) -> None:
        self._entries = entries
        self._timestamp_extractor = timestamp_extractor
        self._workers = max(1, int(workers))
        self._performance = performance_recorder
        self._logger = logger
        self._entry_deduplicator = entry_deduplicator or list

    def load_plugins(self) -> tuple[Any, ...]:
        """Validate the complete graph before importing any plugin class."""

        return instantiate_mechanism_plugins(
            self._entries,
            ts_extractor=self._timestamp_extractor,
        )

    def scan(
        self,
        sources: Sequence[DiagnosticSourcePort],
        plugins: Sequence[Any],
        *,
        error_sink: Callable[[str], None],
    ) -> DiagnosticScanBatch:
        scanners: list[tuple[str, Callable[[str, Any, str], Any]]] = []
        diagnostics: list[Diagnostic] = []
        for plugin in plugins:
            try:
                scanner = plugin.build_diagnostic_line_scanner()
            except Exception as exc:
                diagnostic = Diagnostic(
                    code="LP_DIAGNOSTIC_SCANNER_SETUP_FAILED",
                    message=f"[{plugin.module_key}] 诊断日志扫描器初始化失败: {exc}",
                    stage="diagnostic_scan.shared",
                    details={"module_key": plugin.module_key},
                )
                diagnostics.append(diagnostic)
                error_sink(diagnostic.message)
                continue
            if callable(scanner):
                scanners.append((plugin.module_key, scanner))

        started = time.perf_counter()
        if self._workers > 1 and len(sources) > 1:
            with ThreadPoolExecutor(max_workers=self._workers) as executor:
                scan_results = list(
                    executor.map(
                        lambda source: self._scan_source(source, scanners),
                        sources,
                    )
                )
        else:
            scan_results = [self._scan_source(source, scanners) for source in sources]

        entries_by_module: dict[str, list[Any]] = {
            plugin.module_key: [] for plugin in plugins
        }
        total_lines = 0
        seen_errors: set[str] = set()
        for source, timestamps, module_entries, line_count, source_diagnostics in scan_results:
            source.replace_timestamps(self._sort_timestamps(timestamps))
            total_lines += line_count
            for module_key, entries in module_entries.items():
                entries_by_module[module_key].extend(entries)
            for diagnostic in source_diagnostics:
                if diagnostic.message in seen_errors:
                    continue
                seen_errors.add(diagnostic.message)
                diagnostics.append(diagnostic)
                error_sink(diagnostic.message)

        entries_by_module = {
            module_key: self._entry_deduplicator(entries)
            for module_key, entries in entries_by_module.items()
        }
        self._normalize_timezones(sources)
        total_timestamps = sum(len(source.timestamps()) for source in sources)

        metrics: dict[str, int] = {
            "files": len(sources),
            "lines": total_lines,
            "timestamps": total_timestamps,
        }
        for module_key, entries in sorted(entries_by_module.items()):
            metrics[f"{module_key}_entries"] = len(entries)
        if self._performance is not None:
            self._performance.record_stage(
                "diagnostic_scan.shared",
                elapsed_seconds=time.perf_counter() - started,
                **metrics,
            )

        return DiagnosticScanBatch(
            timestamps_by_source={
                source.key: tuple(source.timestamps()) for source in sources
            },
            entries_by_module={
                module_key: tuple(entries)
                for module_key, entries in entries_by_module.items()
            },
            file_count=len(sources),
            line_count=total_lines,
            diagnostics=tuple(diagnostics),
        )

    def execute(
        self,
        extension_input: Any,
        plugins: Sequence[Any],
        batch: DiagnosticScanBatch,
        *,
        error_sink: Callable[[str], None],
        outcome_sink: Callable[[Any, MechanismOutcome], None],
    ) -> tuple[tuple[Any, MechanismOutcome], ...]:
        dependency_results: dict[str, Any] = {}
        states: dict[str, str] = {}
        outcomes: list[tuple[Any, MechanismOutcome]] = []
        for plugin in plugins:
            started = time.perf_counter()
            execution_error: Exception | None = None
            unavailable = {
                key: states.get(key, "missing")
                for key in plugin.descriptor.dependencies
                if key not in dependency_results
            }
            if unavailable:
                outcome = MechanismOutcome(
                    status="skipped",
                    diagnostics=(Diagnostic(
                        code="LP_MECHANISM_DEPENDENCY_SKIPPED",
                        message=f"[{plugin.module_key}] 依赖没有可用结果，已跳过该机制",
                        stage=f"mechanism.{plugin.module_key}",
                        details={"dependencies": unavailable},
                    ),),
                )
            else:
                try:
                    context = MechanismContext(
                        extension_input=extension_input,
                        dependency_results={
                            key: dependency_results[key]
                            for key in plugin.descriptor.dependencies
                        },
                        scan_batch=batch,
                    )
                    outcome = plugin.execute(context)
                    if not isinstance(outcome, MechanismOutcome):
                        raise TypeError("机制插件必须返回 MechanismOutcome")
                    if outcome.status not in {"success", "partial", "empty", "failed", "skipped"}:
                        raise ValueError(f"机制插件返回未知状态: {outcome.status!r}")
                    if any(item.severity == DiagnosticSeverity.ERROR for item in outcome.diagnostics) and (
                        outcome.status != "partial" or outcome.result is None
                    ):
                        outcome = replace(outcome, status="failed")
                    elif outcome.status in {"success", "partial"} and outcome.result is None:
                        outcome = replace(outcome, status="empty")
                    if outcome.status in {"failed", "partial", "skipped"} and not any(
                        item.severity == DiagnosticSeverity.ERROR for item in outcome.diagnostics
                    ):
                        code, message = {
                            "failed": ("LP_MECHANISM_FAILED", "插件报告执行失败，未提供具体原因"),
                            "partial": ("LP_MECHANISM_PARTIAL", "插件仅返回部分结果，未提供具体原因"),
                            "skipped": (
                                "LP_MECHANISM_DEPENDENCY_SKIPPED",
                                "插件主动跳过该机制，未提供具体原因",
                            ),
                        }[outcome.status]
                        outcome = replace(outcome, diagnostics=(*outcome.diagnostics, Diagnostic(
                            code=code,
                            message=f"[{plugin.module_key}] {message}",
                            stage=f"mechanism.{plugin.module_key}",
                            details={"reported_status": outcome.status},
                        )))
                except Exception as exc:
                    execution_error = exc
                    outcome = MechanismOutcome(
                        status="failed",
                        diagnostics=(Diagnostic(
                            code="LP_MECHANISM_FAILED",
                            message=f"[{plugin.module_key}] 机制执行失败: {exc}",
                            stage=f"mechanism.{plugin.module_key}",
                        ),),
                    )
            states[plugin.module_key] = outcome.status
            outcomes.append((plugin, outcome))
            for diagnostic in outcome.diagnostics:
                if diagnostic.severity == DiagnosticSeverity.ERROR:
                    error_sink(diagnostic.message)
            self._log_module(plugin, started, outcome.result, error=execution_error)
            if outcome.status in {"success", "partial"} and outcome.result is not None:
                dependency_results[plugin.module_key] = outcome.result
                outcome_sink(plugin, outcome)
        return tuple(outcomes)

    def _scan_source(
        self,
        source: DiagnosticSourcePort,
        scanners: Sequence[tuple[str, Callable[[str, Any, str], Any]]],
    ) -> tuple[DiagnosticSourcePort, list[Any], dict[str, list[Any]], int, list[Diagnostic]]:
        timestamps: list[Any] = []
        entries_by_module: dict[str, list[Any]] = {
            module_key: [] for module_key, _scanner in scanners
        }
        diagnostics: list[Diagnostic] = []
        reported: set[str] = set()
        line_count = 0
        try:
            for line in source.iter_lines():
                line_count += 1
                timestamps.extend(self._timestamp_extractor.extract_from_text(line))
                for module_key, scanner in scanners:
                    try:
                        entry = scanner(line, source.payload, source.scope_value)
                    except Exception as exc:
                        message = f"[{module_key}] 诊断日志扫描失败: {source.key} ({exc})"
                        if message not in reported:
                            reported.add(message)
                            diagnostics.append(Diagnostic(
                                code="LP_DIAGNOSTIC_SCAN_FAILED",
                                message=message,
                                stage="diagnostic_scan.shared",
                                details={"module_key": module_key, "source": source.key},
                            ))
                        continue
                    if entry is not None:
                        entries_by_module[module_key].append(entry)
        except Exception as exc:
            diagnostics.append(Diagnostic(
                code="LP_DIAGNOSTIC_READ_FAILED",
                message=f"读取诊断日志失败: {source.key} ({exc})",
                stage="diagnostic_scan.shared",
                details={"source": source.key, "lines_read": line_count},
            ))
        return source, timestamps, entries_by_module, line_count, diagnostics

    @staticmethod
    def _sort_timestamps(values: Sequence[Any]) -> list[Any]:
        try:
            return sorted(values)
        except TypeError:
            return list(values)

    def _normalize_timezones(self, sources: Sequence[DiagnosticSourcePort]) -> None:
        tzinfo = next(
            (
                timestamp.tzinfo
                for source in sources
                for timestamp in source.timestamps()
                if getattr(timestamp, "tzinfo", None) is not None
            ),
            None,
        )
        if tzinfo is None:
            return
        for source in sources:
            source.replace_timestamps(
                [
                    timestamp.replace(tzinfo=tzinfo)
                    if getattr(timestamp, "tzinfo", None) is None
                    else timestamp
                    for timestamp in source.timestamps()
                ]
            )

    def _log_module(
        self,
        plugin: Any,
        started: float,
        result: Any,
        *,
        error: Exception | None = None,
    ) -> None:
        if self._logger is None:
            return
        elapsed = time.perf_counter() - started
        if error is not None:
            self._logger.info(
                "LOGPARSE_PERF parser.module module=%s elapsed=%.3fs result=error",
                plugin.module_key,
                elapsed,
            )
            self._logger.warning("[%s] parse 异常: %s", plugin.module_key, error)
            return
        if result is None:
            self._logger.info(
                "LOGPARSE_PERF parser.module module=%s elapsed=%.3fs result=no "
                "diag_entries=0 journal_entries=0",
                plugin.module_key,
                elapsed,
            )
            return
        self._logger.info(
            "LOGPARSE_PERF parser.module module=%s elapsed=%.3fs result=yes "
            "diag_entries=%d journal_entries=%d",
            plugin.module_key,
            elapsed,
            int(getattr(result, "diag_entry_count", 0)),
            int(getattr(result, "journal_entry_count", 0)),
        )
