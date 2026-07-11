"""为关联算法生成跨进程稳定的周期身份。"""

from __future__ import annotations

from datetime import datetime

from backend.contracts.scopes import CycleRef, ScopeRef


def _timestamp_identity(value: datetime | None) -> str:
    return value.isoformat(timespec="microseconds") if value is not None else "open"


def cycle_ref_for_interval(
    scope: ScopeRef,
    start: datetime | None,
    end: datetime | None,
    *,
    ordinal: int = 0,
) -> CycleRef:
    """Return a stable identity independent of object ids and output paths."""

    cycle_id = f"interval:{_timestamp_identity(start)}..{_timestamp_identity(end)}"
    return CycleRef(scope=scope, cycle_id=cycle_id, ordinal=ordinal)
