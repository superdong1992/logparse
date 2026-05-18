"""板卡角色判定：机制模块优先 + 兜底逻辑。"""
from __future__ import annotations

from backend.models import BoardRole, MechResult, ParseResult


class RoleIdentifier:
    @staticmethod
    def apply_mech_roles(mech_result: MechResult, result: ParseResult) -> None:
        if not mech_result.active_master_slots:
            return
        for slot in result.diagnostic_slots:
            if slot.slot_id in mech_result.active_master_slots:
                slot.role = BoardRole.ACTIVE

    @staticmethod
    def fallback_roles(result: ParseResult) -> None:
        for slot in result.diagnostic_slots:
            if slot.role != BoardRole.UNKNOWN:
                continue
            if slot.active_periods:
                slot.role = BoardRole.ACTIVE
            elif slot.diagnostic_logs:
                slot.role = BoardRole.STANDBY
