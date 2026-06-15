"""Content-based deduplication for parsed mechanism log entries."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from backend.models import MechLogEntry


def dedupe_mech_entries(entries: Iterable[MechLogEntry]) -> list[MechLogEntry]:
    """Return entries with duplicate parsed content removed, preserving order."""
    seen: set[tuple[Any, ...]] = set()
    deduped: list[MechLogEntry] = []
    for entry in entries:
        key = _entry_key(entry)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _entry_key(entry: MechLogEntry) -> tuple[Any, ...]:
    return (
        _timestamp_key(entry.timestamp),
        entry.slot,
        entry.cpu_id,
        entry.process_name,
        entry.pid,
        entry.context,
        entry.sequence,
        entry.is_active_signal,
        entry.raw,
    )


def _timestamp_key(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
