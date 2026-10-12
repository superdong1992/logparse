"""Versioned plugin DTOs shared by the application and extension layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Mapping

from backend.contracts.runtime import Diagnostic
PLUGIN_API_VERSION = 1


class PluginCapability(str, Enum):
    DIAGNOSTIC_SCAN = "diagnostic_scan"
    JOURNAL_SCAN = "journal_scan"
    LIFECYCLE = "lifecycle"
    ROLE_SIGNAL = "role_signal"
    OUTPUT_PROJECTION = "output_projection"


@dataclass(frozen=True, slots=True)
class MechanismDescriptor:
    key: str
    name: str
    dependencies: tuple[str, ...] = ()
    capabilities: frozenset[PluginCapability] = frozenset()
    api_version: int = PLUGIN_API_VERSION
    config_schema: str | None = None

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("mechanism descriptor key must be non-empty")
        if self.api_version != PLUGIN_API_VERSION:
            raise ValueError(
                f"unsupported mechanism plugin API version: {self.api_version}; "
                f"expected {PLUGIN_API_VERSION}"
            )
        if self.key in self.dependencies:
            raise ValueError(f"mechanism {self.key!r} cannot depend on itself")


@dataclass(frozen=True, slots=True)
class DiagnosticScanBatch:
    timestamps_by_source: Mapping[str, tuple[Any, ...]] = field(default_factory=dict)
    entries_by_module: Mapping[str, tuple[Any, ...]] = field(default_factory=dict)
    file_count: int = 0
    line_count: int = 0
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class MechanismContext:
    # Product-owned input exposes only the sources required by mechanisms.
    extension_input: Any = None
    dependency_results: Mapping[str, Any] = field(default_factory=dict)
    scan_batch: DiagnosticScanBatch | None = None


@dataclass(frozen=True, slots=True)
class MechanismOutcome:
    result: Any | None = None
    role_signals: Mapping[str, str] = field(default_factory=dict)
    diagnostics: tuple[Diagnostic, ...] = ()
    status: Literal["success", "partial", "empty", "failed", "skipped"] = "success"
