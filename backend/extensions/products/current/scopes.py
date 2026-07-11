"""当前产品拓扑到通用 ScopeRef/CycleRef 的 projection。"""

from __future__ import annotations

from datetime import datetime

from backend.contracts.scopes import CycleRef, ScopeRef, ScopeSegment
from backend.domain.correlation.identities import cycle_ref_for_interval


def board_scope(slot_id: str) -> ScopeRef:
    return ScopeRef((ScopeSegment("slot", slot_id or "unknown"),))


def cpu_scope(slot_id: str, cpu_id: str) -> ScopeRef:
    normalized = (cpu_id or "").strip()
    if not normalized or normalized == "0":
        return board_scope(slot_id)
    return board_scope(slot_id).child("cpu", normalized)


def product_cycle_ref(
    slot_id: str,
    start: datetime | None,
    end: datetime | None,
    *,
    cpu_id: str = "",
    ordinal: int = 0,
) -> CycleRef:
    scope = cpu_scope(slot_id, cpu_id) if cpu_id else board_scope(slot_id)
    return cycle_ref_for_interval(scope, start, end, ordinal=ordinal)
