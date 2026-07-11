"""Product-neutral mechanism planning, shared scanning, and execution."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from backend.contracts.plugins import (
    DiagnosticScanBatch,
    LegacyMechanismContext,
    MechanismContext,
    MechanismOutcome,
)
from backend.plugins.loader import instantiate_mechanism_plugins


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


@dataclass(frozen=True, slots=True)
class MechanismExecutionResult:
    batch: DiagnosticScanBatch
    outcomes: tuple[tuple[Any, MechanismOutcome], ...]


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
        for plugin in plugins:
            try:
                scanner = plugin.build_diagnostic_line_scanner()
            except Exception as exc:
                error_sink(
                    f"[{plugin.module_key}] shared diagnostic scanner setup failed: {exc}"
                )
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
            module_key: [] for module_key, _scanner in scanners
        }
        total_lines = 0
        seen_errors: set[str] = set()
        for source, timestamps, module_entries, line_count, errors in scan_results:
            source.replace_timestamps(self._sort_timestamps(timestamps))
            total_lines += line_count
            for module_key, entries in module_entries.items():
                entries_by_module[module_key].extend(entries)
            for error in errors:
                if error in seen_errors:
                    continue
                seen_errors.add(error)
                error_sink(error)

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
        )

    def execute(
        self,
        parse_state: Any,
        extension_input: Any,
        plugins: Sequence[Any],
        batch: DiagnosticScanBatch,
        *,
        error_sink: Callable[[str], None],
        outcome_sink: Callable[[Any, MechanismOutcome], None],
    ) -> tuple[tuple[Any, MechanismOutcome], ...]:
        dependency_results: dict[str, Any] = {}
        outcomes: list[tuple[Any, MechanismOutcome]] = []
        for plugin in plugins:
            started = time.perf_counter()
            try:
                context_kwargs = {
                    "extension_input": extension_input,
                    "dependency_results": {
                        key: dependency_results[key]
                        for key in plugin.descriptor.dependencies
                        if key in dependency_results
                    },
                    "scan_batch": batch,
                }
                if getattr(plugin, "requires_legacy_parse_state", False):
                    context = LegacyMechanismContext(
                        parse_state=parse_state,
                        **context_kwargs,
                    )
                else:
                    context = MechanismContext(**context_kwargs)
                outcome = plugin.execute(context)
            except Exception as exc:
                self._log_module(plugin, started, None, error=exc)
                error_sink(f"[{plugin.module_key}] parse 异常: {exc}")
                continue

            result = outcome.result
            self._log_module(plugin, started, result)
            outcomes.append((plugin, outcome))
            if result is not None:
                dependency_results[plugin.module_key] = result
                outcome_sink(plugin, outcome)
        return tuple(outcomes)

    def _scan_source(
        self,
        source: DiagnosticSourcePort,
        scanners: Sequence[tuple[str, Callable[[str, Any, str], Any]]],
    ) -> tuple[DiagnosticSourcePort, list[Any], dict[str, list[Any]], int, list[str]]:
        timestamps: list[Any] = []
        entries_by_module: dict[str, list[Any]] = {
            module_key: [] for module_key, _scanner in scanners
        }
        errors: list[str] = []
        reported: set[str] = set()
        line_count = 0
        for line in source.iter_lines():
            line_count += 1
            timestamps.extend(self._timestamp_extractor.extract_from_text(line))
            for module_key, scanner in scanners:
                try:
                    entry = scanner(line, source.payload, source.scope_value)
                except Exception as exc:
                    message = (
                        f"[{module_key}] shared diagnostic scan failed "
                        f"in {source.key}: {exc}"
                    )
                    if message not in reported:
                        reported.add(message)
                        errors.append(message)
                    continue
                if entry is not None:
                    entries_by_module[module_key].append(entry)
        return source, timestamps, entries_by_module, line_count, errors

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
