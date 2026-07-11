"""Versioned contracts for parse artifacts and their manifest."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping

from backend.contracts.diagnostics import DiagnosticRecord

ARTIFACT_CONTRACT_VERSION = 1
PARSE_MANIFEST_SCHEMA_VERSION = 1


class ParseStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ManifestStageStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ManifestStageRecord:
    name: str
    status: ManifestStageStatus | str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": str(self.name),
            "status": ManifestStageStatus(self.status).value,
        }
        if self.detail:
            payload["detail"] = str(self.detail)
        return payload


@dataclass(frozen=True)
class ArtifactIntegrityRecord:
    """Integrity information for one file or one directory tree."""

    path: str
    kind: str
    size_bytes: int
    sha256: str
    file_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "size_bytes": int(self.size_bytes),
            "sha256": self.sha256,
            "file_count": int(self.file_count),
        }


@dataclass(frozen=True)
class WorkspaceRecord:
    retained: bool = False
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"retained": bool(self.retained)}
        if self.retained and self.path:
            payload["path"] = str(self.path)
        return payload


@dataclass(frozen=True)
class ParseManifest:
    """Top-level parse run manifest.

    The manifest intentionally models generic stages, counters, artifacts and
    diagnostics. Product topology is projected by metadata/result artifacts.
    """

    task_id: str
    product: str
    status: ParseStatus | str
    artifacts: Mapping[str, ArtifactIntegrityRecord] = field(default_factory=dict)
    stages: tuple[ManifestStageRecord, ...] = ()
    counters: Mapping[str, int | float] = field(default_factory=dict)
    diagnostics: tuple[DiagnosticRecord, ...] = ()
    workspace: WorkspaceRecord = field(default_factory=WorkspaceRecord)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    schema_version: int = PARSE_MANIFEST_SCHEMA_VERSION
    artifact_contract_version: int = ARTIFACT_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": int(self.schema_version),
            "artifact_contract_version": int(self.artifact_contract_version),
            "task_id": str(self.task_id),
            "product": str(self.product),
            "status": ParseStatus(self.status).value,
            "created_at": str(self.created_at),
            "stages": [item.to_dict() for item in self.stages],
            "artifacts": {
                key: self.artifacts[key].to_dict() for key in sorted(self.artifacts)
            },
            "counters": {
                str(key): value for key, value in sorted(self.counters.items())
            },
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "workspace": self.workspace.to_dict(),
        }

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        product: str,
        status: ParseStatus | str,
        artifacts: Mapping[str, ArtifactIntegrityRecord] | None = None,
        stages: Iterable[ManifestStageRecord] = (),
        counters: Mapping[str, int | float] | None = None,
        diagnostics: Iterable[DiagnosticRecord | Mapping[str, Any] | str] = (),
        workspace: WorkspaceRecord | None = None,
        created_at: str | None = None,
    ) -> "ParseManifest":
        return cls(
            task_id=task_id,
            product=product,
            status=status,
            artifacts=dict(artifacts or {}),
            stages=tuple(stages),
            counters=dict(counters or {}),
            diagnostics=tuple(
                DiagnosticRecord.from_value(item) for item in diagnostics
            ),
            workspace=workspace or WorkspaceRecord(),
            **({"created_at": created_at} if created_at is not None else {}),
        )
