"""Tests for versioned, product-neutral artifact contracts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.contracts.artifacts import (
    ManifestStageRecord,
    ManifestStageStatus,
    ParseStatus,
)
from backend.infrastructure.artifact_layout import ArtifactLayout
from backend.infrastructure.artifact_repository import ArtifactRepository
from backend.metadata import MetadataGenerator
from backend.models import (
    LogEntry,
    MechResult,
    MechSlotOutput,
    ParseResult,
    SlotInfo,
)
from backend.result_serializer import result_to_dict


def test_artifact_layout_rejects_task_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ArtifactLayout(tmp_path, "../escape")


def test_repository_writes_manifest_with_integrity_and_empty_evidence_dir(
    tmp_path: Path,
) -> None:
    layout = ArtifactLayout(tmp_path, "task")
    repository = ArtifactRepository(layout)
    repository.write_metadata({"schema_version": 2})
    repository.write_result({"schema_version": 2, "mech_results": []})

    path = repository.refresh_manifest(
        product="default",
        status=ParseStatus.SUCCESS,
        stages=[ManifestStageRecord("parse", ManifestStageStatus.SUCCESS)],
        counters={"files": 2, "lines": 10},
        workspace_retained=False,
        created_at="2026-01-03T00:00:00+00:00",
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "success"
    assert data["artifacts"]["metadata"]["sha256"]
    assert data["artifacts"]["result"]["sha256"]
    assert data["artifacts"]["mech_modules"] == {
        "path": "mech_modules",
        "kind": "directory",
        "size_bytes": 0,
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "file_count": 0,
    }
    assert data["workspace"] == {"retained": False}
    assert not list(layout.task_dir.glob(".parse_manifest.json.*.tmp"))

    repository.write_result({"schema_version": 2, "mech_results": ["tampered"]})
    integrity = repository.verify_manifest(data)
    assert integrity["ok"] is False
    assert any(
        item["artifact"] == "result" and item["code"] == "size_bytes_mismatch"
        for item in integrity["issues"]
    )


def test_repository_atomic_failure_preserves_previous_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = ArtifactLayout(tmp_path, "task")
    repository = ArtifactRepository(layout)
    repository.write_result({"version": "old"})

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(
        "backend.infrastructure.artifact_repository.os.replace",
        fail_replace,
    )
    with pytest.raises(OSError, match="simulated replace failure"):
        repository.write_result({"version": "new"})

    assert json.loads(layout.result.read_text(encoding="utf-8")) == {"version": "old"}
    assert not list(layout.task_dir.glob(".result.json.*.tmp"))


def test_metadata_is_scan_only_and_uses_logical_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    log_path = workspace / "diag" / "slot_1" / "diag.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("line\n", encoding="utf-8")
    timestamp = datetime(2026, 1, 3, tzinfo=timezone.utc)
    result = ParseResult(
        task_id="task",
        package_name="package.zip",
        extracted_root=str(workspace),
        diagnostic_slots=[
            SlotInfo(
                slot_id="1",
                name="slot_1",
                path=str(log_path.parent),
                diagnostic_logs=[
                    LogEntry(
                        path=str(log_path),
                        name="diag.log",
                        extracted_path=str(log_path.parent / "expanded"),
                        content_timestamps=[timestamp],
                    )
                ],
            )
        ],
        mech_results=[MechResult(module_name="EXAMPLE")],
    )

    data = MetadataGenerator().build(result, product="default")
    payload = json.dumps(data, ensure_ascii=False)

    assert data["schema_version"] == 2
    assert data["product"] == "default"
    assert data["coverage"]["content_timestamp_count"] == 1
    assert data["diagnostic_slots"][0]["path"] == "diag/slot_1"
    assert data["diagnostic_slots"][0]["diagnostic_logs"][0]["path"] == (
        "diag/slot_1/diag.log"
    )
    assert "mech_results" not in data
    assert "extracted_root" not in data
    assert "extracted_path" not in payload


def test_result_is_query_only_and_full_mode_is_removed() -> None:
    result = ParseResult(
        task_id="task",
        package_name="package.zip",
        extracted_root="/temporary/workspace",
        errors=["example"],
    )

    data = result_to_dict(result)

    assert data["schema_version"] == 2
    assert "diagnostic_slots" not in data
    assert "private_slots" not in data
    assert "extracted_root" not in data
    with pytest.raises(ValueError, match="compact mode only"):
        result_to_dict(result, "full")


def test_result_recursively_drops_raw_context_and_payload_fields() -> None:
    result = ParseResult(
        mech_results=[
            MechResult(
                module_name="EXAMPLE",
                slots=[
                    MechSlotOutput(
                        slot_id="1",
                        lifecycle_split_result={
                            "algorithm": "interval_v3",
                            "rawLog": "secret raw",
                            "context_before": "secret context",
                            "payload": "secret payload",
                            "issues": [{"reason": "safe structured reason"}],
                        },
                    )
                ],
            )
        ]
    )

    payload = json.dumps(result_to_dict(result), ensure_ascii=False)

    assert "secret raw" not in payload
    assert "secret context" not in payload
    assert "secret payload" not in payload
    assert "safe structured reason" in payload
