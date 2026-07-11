"""Current product compact query-index projection."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from backend.contracts.artifacts import ARTIFACT_CONTRACT_VERSION
from backend.models import (
    MechBoardCycle,
    MechCpuCycle,
    MechProcessLifecycle,
    MechResult,
    ParseResult,
)

RESULT_SCHEMA_VERSION = 2
_PROHIBITED_RESULT_KEYS = {
    "context",
    "first_raw",
    "last_raw",
    "logs",
    "new_raw",
    "old_raw",
    "raw",
    "raw_excerpt",
}


def result_to_dict(result: ParseResult, mode: str = "compact") -> dict[str, Any]:
    """Serialize a ParseResult into the only supported result contract.

    The `mode` argument remains in the façade while callers migrate, but full
    raw serialization has intentionally been removed.
    """
    if (mode or "compact").lower() != "compact":
        raise ValueError("result.json supports compact mode only")
    return compact_result_dict(result)


def compact_result_dict(result: ParseResult) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "task_id": result.task_id,
        "package_name": result.package_name,
        "created_at": _dt(result.created_at),
        "mech_results": [_mech_to_dict(mech) for mech in result.mech_results],
        "errors": list(result.errors),
    }


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


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
                "lifecycle_split_result": _model_to_json(slot.lifecycle_split_result),
                "assignment_decisions": _omit_raw_fields(slot.assignment_decisions),
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
            if not _is_prohibited_result_key(key)
        }
    if isinstance(value, list):
        return [_omit_raw_fields(item) for item in value]
    return value


def _is_prohibited_result_key(key: Any) -> bool:
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).lower().replace("-", "_")
    if normalized in _PROHIBITED_RESULT_KEYS:
        return True
    tokens = set(normalized.split("_"))
    return bool(tokens & {"raw", "context", "payload"})


def _board_cycle_to_dict(cycle: MechBoardCycle) -> dict[str, Any]:
    return {
        "dir_name": cycle.dir_name,
        "start_time": _dt(cycle.start_time),
        "end_time": _dt(cycle.end_time),
        "processes": [_process_to_dict(process) for process in cycle.processes],
        "cpu_cycles": [_cpu_cycle_to_dict(cpu_cycle) for cpu_cycle in cycle.cpu_cycles],
    }


def _cpu_cycle_to_dict(cycle: MechCpuCycle) -> dict[str, Any]:
    return {
        "cpu_id": cycle.cpu_id,
        "dir_name": cycle.dir_name,
        "start_time": _dt(cycle.start_time),
        "end_time": _dt(cycle.end_time),
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
