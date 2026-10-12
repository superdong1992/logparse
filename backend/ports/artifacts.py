"""Minimal artifact persistence ports.

Concrete layout and atomic-write behavior live in infrastructure; the
application layer depends only on these interfaces.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from backend.contracts.runtime import ArtifactRecord, Diagnostic, StageResult


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
