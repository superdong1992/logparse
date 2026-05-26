"""板卡角色判定：机制模块优先 + 兜底逻辑。"""
from __future__ import annotations

import logging

from backend.models import BoardRole, MechResult, ParseResult

logger = logging.getLogger(__name__)


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
        # 已经由机制模块确定的 active 不要覆盖
        known_active = [s for s in result.diagnostic_slots if s.role == BoardRole.ACTIVE]
        if known_active:
            for slot in result.diagnostic_slots:
                if slot.role == BoardRole.UNKNOWN and slot.diagnostic_logs:
                    slot.role = BoardRole.STANDBY
            return

        # 无机制模块判定时，按 ActivePeriod 兜底
        candidates = [s for s in result.diagnostic_slots if s.active_periods]

        if len(candidates) == 1:
            candidates[0].role = BoardRole.ACTIVE
            for slot in result.diagnostic_slots:
                if slot is not candidates[0] and slot.diagnostic_logs:
                    slot.role = BoardRole.STANDBY
        elif len(candidates) > 1:
            # 多个候选时不武断判 active，保持 UNKNOWN
            logger.warning(
                "多个 slot 有 ActivePeriod，无法确定主控: %s",
                [s.slot_id for s in candidates],
            )
            return
        else:
            # 无候选，仅判 standby
            for slot in result.diagnostic_slots:
                if slot.diagnostic_logs and slot.role == BoardRole.UNKNOWN:
                    slot.role = BoardRole.STANDBY
