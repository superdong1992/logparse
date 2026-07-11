"""Adapter from the application artifact port to ArtifactRepository."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from backend.contracts.artifacts import (
    ArtifactIntegrityRecord,
    ManifestStageRecord,
    ManifestStageStatus,
)
from backend.contracts.runtime import (
    ArtifactRecord,
    Diagnostic,
    StageResult,
    StageStatus,
)
from backend.infrastructure.artifact_layout import ArtifactLayout
from backend.infrastructure.artifact_repository import ArtifactRepository


class RepositoryArtifactSession:
    def __init__(self, output_root: Path, task_id: str):
        self.repository = ArtifactRepository(ArtifactLayout(output_root, task_id))

    def write_result(self, payload: Mapping[str, object]) -> ArtifactRecord:
        self.repository.write_result(payload)
        record = self.repository.collect_parse_artifacts()["result"]
        return _runtime_record("result", record, schema_version=_schema(payload))

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
        manifest_stages = [
            ManifestStageRecord(
                stage.name,
                _manifest_stage_status(stage.status),
            )
            for stage in stages
        ]
        self.repository.refresh_manifest(
            product=product,
            status=status,
            stages=manifest_stages,
            counters=counters,
            diagnostics=[
                {
                    "code": item.code,
                    "message": item.message,
                    "severity": item.severity.value,
                    "stage": item.stage,
                    "detail": dict(item.details),
                }
                for item in diagnostics
            ],
            workspace_retained=workspace is not None,
            workspace_path=workspace,
            created_at=created_at,
        )

        records = self.repository.collect_parse_artifacts()
        records["parse_manifest"] = self.repository.inspect_artifact(
            self.repository.layout.parse_manifest
        )
        return tuple(
            _runtime_record(name, record)
            for name, record in sorted(records.items())
        )


def _manifest_stage_status(status: StageStatus) -> ManifestStageStatus:
    if status == StageStatus.SUCCEEDED:
        return ManifestStageStatus.SUCCESS
    if status == StageStatus.FAILED:
        return ManifestStageStatus.FAILED
    return ManifestStageStatus.SKIPPED


def _runtime_record(
    name: str,
    record: ArtifactIntegrityRecord,
    *,
    schema_version: int | None = None,
) -> ArtifactRecord:
    return ArtifactRecord(
        name=name,
        relative_path=record.path,
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        schema_version=schema_version,
    )


def _schema(payload: Mapping[str, object]) -> int | None:
    value = payload.get("schema_version")
    return int(value) if isinstance(value, int) else None
