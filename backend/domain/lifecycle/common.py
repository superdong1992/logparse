"""生命周期切分的稳定领域模型与公共算法。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.models import MechLogEntry, MechProcessLifecycle


class LifecycleSplitConfig(BaseModel):
    process_name_mapping: dict[str, list[str]] = Field(default_factory=dict)
    reliable_processes: list[str] = Field(default_factory=list)
    multi_instance_processes: list[str] = Field(default_factory=list)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> "LifecycleSplitConfig":
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError("lifecycle_split config must be an object")

        unsupported = [key for key in ("enabled", "algorithm") if key in raw]
        if unsupported:
            raise ValueError(
                "lifecycle_split only supports V3 fields; unsupported keys: "
                f"{sorted(unsupported)}"
            )

        mapping_raw = raw.get("process_name_mapping", {})
        if not isinstance(mapping_raw, dict):
            raise ValueError("lifecycle_split.process_name_mapping must be an object")
        mapping: dict[str, list[str]] = {}
        for canonical, aliases in mapping_raw.items():
            canonical_name = str(canonical)
            if aliases is None:
                mapping[canonical_name] = []
            elif isinstance(aliases, str):
                mapping[canonical_name] = [aliases]
            else:
                try:
                    mapping[canonical_name] = [str(alias) for alias in aliases]
                except TypeError as exc:
                    raise ValueError(
                        f"lifecycle_split.process_name_mapping.{canonical_name} must be a list"
                    ) from exc

        reliable_raw = raw.get("reliable_processes", [])
        if reliable_raw is not None and not isinstance(reliable_raw, list):
            raise ValueError("lifecycle_split.reliable_processes must be a list")
        multi_raw = raw.get("multi_instance_processes", [])
        if multi_raw is not None and not isinstance(multi_raw, list):
            raise ValueError("lifecycle_split.multi_instance_processes must be a list")

        return cls(
            process_name_mapping=mapping,
            reliable_processes=[str(name) for name in (reliable_raw or [])],
            multi_instance_processes=[str(name) for name in (multi_raw or [])],
        )


def _norm(value: str) -> str:
    return value.casefold()


def _entry_sort_key(entry: MechLogEntry) -> tuple[Any, ...]:
    return (
        entry.timestamp or datetime.min,
        entry.sequence,
        entry.source_file,
        entry.raw,
    )


def _entry_time_bounds(
    entries: list[MechLogEntry],
) -> tuple[datetime | None, datetime | None]:
    stamps = sorted(entry.timestamp for entry in entries if entry.timestamp)
    if not stamps:
        return None, None
    return stamps[0], stamps[-1]


def _format_cycle_dir(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return "unknown"
    return f"{start:%Y%m%d%H%M%S}-{end:%Y%m%d%H%M%S}"


def _build_process_lifecycles(entries: list[MechLogEntry]) -> list[MechProcessLifecycle]:
    by_key: dict[tuple[str, str, str], list[MechLogEntry]] = defaultdict(list)
    for entry in entries:
        by_key[(entry.process_name, entry.pid, entry.cpu_id or "")].append(entry)

    _merge_pidless_journal_entries(by_key)

    processes: list[MechProcessLifecycle] = []
    for (process_name, pid, _cpu_id), logs in sorted(by_key.items()):
        logs.sort(key=_entry_sort_key)
        processes.append(
            MechProcessLifecycle(
                process_name=process_name,
                pid=pid,
                logs=logs,
                total_count=len(logs),
                missing_sequences=_missing_sequences(logs),
            )
        )
    return processes


def _merge_pidless_journal_entries(
    by_key: dict[tuple[str, str, str], list[MechLogEntry]],
) -> None:
    no_pid_keys = [key for key in by_key if key[1] == ""]
    for key in no_pid_keys:
        if key not in by_key:
            continue
        process_name, _pid, cpu_id = key
        logs = by_key[key]
        pidless_journal_logs = [
            entry for entry in logs if entry.source == "journal" and not entry.pid
        ]
        if not pidless_journal_logs:
            continue

        candidate_keys = [
            candidate
            for candidate in by_key
            if (
                candidate[0] == process_name
                and candidate[1]
                and candidate[2] == cpu_id
            )
        ]
        if len(candidate_keys) != 1:
            continue

        target_key = candidate_keys[0]
        by_key[target_key].extend(pidless_journal_logs)
        moved_ids = {id(entry) for entry in pidless_journal_logs}
        remaining = [entry for entry in logs if id(entry) not in moved_ids]
        if remaining:
            by_key[key] = remaining
        else:
            del by_key[key]


def _missing_sequences(logs: list[MechLogEntry]) -> list[int]:
    seqs = sorted({entry.sequence for entry in logs if entry.sequence})
    if not seqs:
        return []
    return [seq for seq in range(seqs[0], seqs[-1] + 1) if seq not in seqs]
