"""LAN-owned metadata projection for the current product.

`metadata.json` is a scan overview.  It intentionally excludes mechanism
results and temporary extracted paths; query indexes belong in `result.json`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.contracts.artifacts import ARTIFACT_CONTRACT_VERSION
from backend.infrastructure.artifact_repository import ArtifactRepository
from backend.models import ParseResult, PrivateSlotInfo, SlotInfo

METADATA_SCHEMA_VERSION = 2


class MetadataGenerator:
    """Generate the current product's metadata projection."""

    def generate(
        self,
        result: ParseResult,
        output_dir: Path,
        *,
        product: str | None = None,
    ) -> Path:
        repository = ArtifactRepository.for_task_dir(output_dir)
        return repository.write_metadata(self.build(result, product=product))

    def build(
        self,
        result: ParseResult,
        *,
        product: str | None = None,
    ) -> dict[str, Any]:
        workspace = Path(result.extracted_root) if result.extracted_root else None
        data: dict[str, Any] = {
            "schema_version": METADATA_SCHEMA_VERSION,
            "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
            "task_id": result.task_id,
            "package_name": result.package_name,
            "created_at": result.created_at.isoformat(),
            "coverage": self._coverage(result),
            # Compatibility projection consumed by existing query/CLI callers.
            # These names are product concepts, not generic artifact contracts.
            "diagnostic_slots": [
                self._slot_to_dict(slot, workspace) for slot in result.diagnostic_slots
            ],
            "private_slots": [
                self._private_slot_to_dict(slot, workspace)
                for slot in result.private_slots
            ],
            "errors": list(result.errors),
        }
        if product:
            data["product"] = str(product)
        return data

    @staticmethod
    def _coverage(result: ParseResult) -> dict[str, Any]:
        timestamps = [
            timestamp
            for slot in result.diagnostic_slots
            for entry in slot.diagnostic_logs
            for timestamp in entry.content_timestamps
        ]
        start, end = _time_range(timestamps)
        return {
            "diagnostic_scope_count": len(result.diagnostic_slots),
            "private_scope_count": len(result.private_slots),
            "diagnostic_file_count": sum(
                len(slot.diagnostic_logs) for slot in result.diagnostic_slots
            ),
            "journal_file_count": sum(
                len(slot.journal_logs) for slot in result.private_slots
            ),
            "content_timestamp_count": len(timestamps),
            "time_range": {
                "start": start,
                "end": end,
            },
        }

    @staticmethod
    def _slot_to_dict(slot: SlotInfo, workspace: Path | None) -> dict[str, Any]:
        return {
            "slot_id": slot.slot_id,
            "name": slot.name,
            "type": slot.board_type.value,
            "role": slot.role.value,
            "path": _logical_path(slot.path, workspace),
            "content_timestamp_count": sum(
                len(entry.content_timestamps) for entry in slot.diagnostic_logs
            ),
            "active_periods": [
                {
                    "start": period.start.isoformat(),
                    "end": period.end.isoformat(),
                    "duration_seconds": period.duration.total_seconds(),
                }
                for period in slot.active_periods
            ],
            "diagnostic_logs": [
                {
                    "path": _logical_path(entry.path, workspace),
                    "name": entry.name,
                    "size_bytes": entry.size_bytes,
                    "compressed": entry.compressed,
                    "original_format": entry.original_format,
                    "dump_time": entry.dump_time.isoformat()
                    if entry.dump_time
                    else None,
                    "content_timestamp_count": len(entry.content_timestamps),
                }
                for entry in slot.diagnostic_logs
            ],
        }

    @staticmethod
    def _private_slot_to_dict(
        slot: PrivateSlotInfo,
        workspace: Path | None,
    ) -> dict[str, Any]:
        return {
            "dir_name": slot.dir_name,
            "slot_id": slot.slot_id,
            "cpu_id": slot.cpu_id,
            "path": _logical_path(slot.path, workspace),
            "journal_logs": [
                {
                    "path": _logical_path(log.path, workspace),
                    "name": log.name,
                    "size_bytes": log.size_bytes,
                    "compressed": log.compressed,
                    "sequence": log.sequence,
                }
                for log in slot.journal_logs
            ],
        }


def _logical_path(value: str, workspace: Path | None) -> str:
    """Persist a stable package-relative path instead of a temporary path."""
    if not value:
        return ""
    candidate = Path(value)
    if workspace is not None:
        try:
            return (
                candidate.resolve(strict=False)
                .relative_to(workspace.resolve(strict=False))
                .as_posix()
            )
        except ValueError:
            pass
    if not candidate.is_absolute():
        return candidate.as_posix()
    return candidate.name


def _time_range(timestamps: list[datetime]) -> tuple[str | None, str | None]:
    if not timestamps:
        return None, None

    def sort_key(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    start = min(timestamps, key=sort_key)
    end = max(timestamps, key=sort_key)
    return start.isoformat(), end.isoformat()
