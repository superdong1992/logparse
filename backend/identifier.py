from __future__ import annotations

from backend.models import (
    BoardRole,
    ParseResult,
)


class Identifier:
    """
    兜底主控判定：根据 ActivePeriod 和诊断日志存在性判定各 slot 的角色。
    """

    def analyze(self, result: ParseResult) -> ParseResult:
        self._determine_roles(result)
        return result

    def _determine_roles(self, result: ParseResult) -> None:
        for slot in result.diagnostic_slots:
            if slot.active_periods:
                slot.role = BoardRole.ACTIVE
            elif slot.diagnostic_logs:
                slot.role = BoardRole.STANDBY
            else:
                slot.role = BoardRole.UNKNOWN
