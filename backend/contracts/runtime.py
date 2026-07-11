"""Product-neutral runtime DTOs.

These contracts intentionally do not contain concrete product topology terms.
Product extensions may project their topology onto ``ScopeRef`` from
:mod:`backend.contracts.scopes`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class DiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A stable, machine-readable diagnostic emitted by a parse stage."""

    code: str
    message: str
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
    stage: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParseRuntimeOptions:
    """Execution controls that are independent from product semantics."""

    extraction_workers: int | str = "auto"
    diagnostic_scan_workers: int | str = "auto"
    debug_expand_gz: bool = False
    keep_workspace: bool = False
    profile: bool = False
    verbose: bool = False


@dataclass(frozen=True, slots=True)
class ParseRequest:
    source: Path
    output_root: Path
    product: str = "default"
    task_id: str | None = None
    options: ParseRuntimeOptions = field(default_factory=ParseRuntimeOptions)


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class StageResult:
    name: str
    status: StageStatus
    diagnostics: tuple[Diagnostic, ...] = ()
    metrics: Mapping[str, int | float | str | bool] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    name: str
    relative_path: str
    size_bytes: int = 0
    sha256: str = ""
    schema_version: int | None = None


@dataclass(frozen=True, slots=True)
class ParseRun:
    """Complete application-layer response for one parse request."""

    # Opaque product state.  The application contract deliberately does not
    # import a product model hierarchy.
    result: Any
    status: str = "success"
    stages: tuple[StageResult, ...] = ()
    artifacts: tuple[ArtifactRecord, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    workspace: Path | None = None


@dataclass(frozen=True, slots=True)
class ParseEngineOutcome:
    """Product-neutral response returned by an injected parse engine."""

    result: Any
    status: str = "success"
    stages: tuple[StageResult, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    counters: Mapping[str, int | float] = field(default_factory=dict)
    workspace: Path | None = None
    created_at: str | None = None


class ParseEngineError(RuntimeError):
    """Structured fatal failure raised by an injected product engine."""

    def __init__(
        self,
        message: str,
        *,
        stages: tuple[StageResult, ...] = (),
        diagnostics: tuple[Diagnostic, ...] = (),
    ) -> None:
        self.stages = stages
        self.diagnostics = diagnostics
        super().__init__(message)
