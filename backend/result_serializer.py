from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.models import (
    JournalLogFile,
    MechBoardCycle,
    MechCpuCycle,
    MechProcessLifecycle,
    MechResult,
    ParseResult,
    PrivateSlotInfo,
    SlotInfo,
)


def result_to_dict(result: ParseResult, mode: str = "compact") -> dict[str, Any]:
    """Serialize ParseResult for result.json.

    compact mode keeps query-friendly summaries and omits raw per-line logs.
    full mode preserves the historical complete ParseResult dump.
    """
    normalized = (mode or "compact").lower()
    if normalized == "full":
        return result.model_dump(mode="json")
    if normalized != "compact":
        raise ValueError("pipeline.result_json_mode must be 'compact' or 'full'")
    return compact_result_dict(result)


def compact_result_dict(result: ParseResult) -> dict[str, Any]:
    return {
        "task_id": result.task_id,
        "package_name": result.package_name,
        "extracted_root": result.extracted_root,
        "created_at": _dt(result.created_at),
        "diagnostic_slots": [_slot_to_dict(slot) for slot in result.diagnostic_slots],
        "private_slots": [_private_slot_to_dict(slot) for slot in result.private_slots],
        "mech_results": [_mech_to_dict(mech) for mech in result.mech_results],
        "errors": result.errors,
    }


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _slot_to_dict(slot: SlotInfo) -> dict[str, Any]:
    return {
        "slot_id": slot.slot_id,
        "name": slot.name,
        "type": slot.board_type.value,
        "role": slot.role.value,
        "path": slot.path,
        "content_timestamp_count": sum(
            len(entry.content_timestamps) for entry in slot.diagnostic_logs
        ),
        "active_periods": [
            {
                "start": _dt(period.start),
                "end": _dt(period.end),
                "duration_seconds": period.duration.total_seconds(),
            }
            for period in slot.active_periods
        ],
        "diagnostic_logs": [
            {
                "path": entry.path,
                "name": entry.name,
                "size_bytes": entry.size_bytes,
                "compressed": entry.compressed,
                "original_format": entry.original_format,
                "extracted_path": entry.extracted_path,
                "dump_time": _dt(entry.dump_time),
                "content_timestamp_count": len(entry.content_timestamps),
            }
            for entry in slot.diagnostic_logs
        ],
    }


def _private_slot_to_dict(slot: PrivateSlotInfo) -> dict[str, Any]:
    return {
        "dir_name": slot.dir_name,
        "slot_id": slot.slot_id,
        "cpu_id": slot.cpu_id,
        "path": slot.path,
        "journal_logs": [_journal_to_dict(log) for log in slot.journal_logs],
    }


def _journal_to_dict(log: JournalLogFile) -> dict[str, Any]:
    return {
        "path": log.path,
        "name": log.name,
        "size_bytes": log.size_bytes,
        "compressed": log.compressed,
        "sequence": log.sequence,
    }


def _mech_to_dict(mech: MechResult) -> dict[str, Any]:
    return {
        "module_name": mech.module_name,
        "module_key": mech.module_key,
        "active_master_slots": mech.active_master_slots,
        "diag_entry_count": mech.diag_entry_count,
        "journal_entry_count": mech.journal_entry_count,
        "slots": [
            {
                "slot_id": slot.slot_id,
                "lifecycle_reliable": slot.lifecycle_reliable,
                "boundary_issues": [
                    _omit_raw_fields(issue.model_dump(mode="json"))
                    for issue in slot.boundary_issues
                ],
                "lifecycle_split_result": _model_to_json(slot.lifecycle_split_result),
                "board_cycles": [
                    _board_cycle_to_dict(cycle) for cycle in slot.board_cycles
                ],
            }
            for slot in mech.slots
        ],
    }


def _model_to_json(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return _omit_raw_fields(value.model_dump(mode="json"))
    return _omit_raw_fields(value)


def _omit_raw_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _omit_raw_fields(item)
            for key, item in value.items()
            if key not in {"raw", "raw_excerpt", "old_raw", "new_raw", "first_raw", "last_raw"}
        }
    if isinstance(value, list):
        return [_omit_raw_fields(item) for item in value]
    return value


def _board_cycle_to_dict(cycle: MechBoardCycle) -> dict[str, Any]:
    return {
        "dir_name": cycle.dir_name,
        "start_time": _dt(cycle.start_time),
        "end_time": _dt(cycle.end_time),
        "split_traces": [
            trace.model_dump(mode="json") for trace in cycle.split_traces
        ],
        "processes": [_process_to_dict(process) for process in cycle.processes],
        "cpu_cycles": [
            _cpu_cycle_to_dict(cpu_cycle) for cpu_cycle in cycle.cpu_cycles
        ],
    }


def _cpu_cycle_to_dict(cycle: MechCpuCycle) -> dict[str, Any]:
    return {
        "cpu_id": cycle.cpu_id,
        "dir_name": cycle.dir_name,
        "start_time": _dt(cycle.start_time),
        "end_time": _dt(cycle.end_time),
        "split_traces": [
            trace.model_dump(mode="json") for trace in cycle.split_traces
        ],
        "processes": [_process_to_dict(process) for process in cycle.processes],
    }


def _process_to_dict(process: MechProcessLifecycle) -> dict[str, Any]:
    return {
        "process_name": process.process_name,
        "pid": process.pid,
        "total_count": process.total_count,
        "missing_sequences": process.missing_sequences,
        "missing_count": len(process.missing_sequences),
    }
