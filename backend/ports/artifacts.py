"""Minimal artifact persistence ports.

Concrete layout and atomic-write behavior live in infrastructure; the
application layer depends only on these interfaces.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from backend.contracts.runtime import ArtifactRecord, Diagnostic, StageResult


@runtime_checkable
class ArtifactWriterPort(Protocol):
    def write_json(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        schema_version: int | None = None,
    ) -> ArtifactRecord:
        ...


@runtime_checkable
class ArtifactReaderPort(Protocol):
    @property
    def root(self) -> Path:
        ...

    def read_json(self, name: str) -> Mapping[str, Any]:
        ...


@runtime_checkable
class ParseArtifactSessionPort(Protocol):
    """One task's formal artifact transaction."""

    def write_result(self, payload: Mapping[str, Any]) -> ArtifactRecord:
        ...

    def finalize(
        self,
        *,
        product: str,
        status: str,
        stages: Sequence[StageResult],
        counters: Mapping[str, int | float],
        diagnostics: Sequence[Diagnostic],
        workspace: str | None,
        created_at: str | None = None,
    ) -> tuple[ArtifactRecord, ...]:
        ...
