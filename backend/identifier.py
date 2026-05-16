from __future__ import annotations

from backend.models import (
    ActivePeriod,
    BoardRole,
    ParseResult,
    SlotInfo,
    SwitchoverEvent,
)


class Identifier:
    """
    兜底主控判定：
    - 有诊断日志的 slot → 曾在某个时段做主控
    - 多 slot 有诊断日志且 ActivePeriod 时间不重叠 → 倒换事件
    """

    def analyze(self, result: ParseResult) -> ParseResult:
        self._determine_roles(result)
        self._detect_switchover(result)
        return result

    def _determine_roles(self, result: ParseResult) -> None:
        for slot in result.diagnostic_slots:
            if slot.active_periods:
                slot.role = BoardRole.ACTIVE
            elif slot.diagnostic_logs:
                slot.role = BoardRole.STANDBY
            else:
                slot.role = BoardRole.UNKNOWN

    def _detect_switchover(self, result: ParseResult) -> None:
        all_periods: list[tuple[SlotInfo, ActivePeriod]] = []
        for slot in result.diagnostic_slots:
            for period in slot.active_periods:
                all_periods.append((slot, period))

        if len(all_periods) <= 1:
            return

        all_periods.sort(key=lambda x: x[1].start)
        events: list[SwitchoverEvent] = []

        for i in range(len(all_periods) - 1):
            slot_a, period_a = all_periods[i]
            slot_b, period_b = all_periods[i + 1]

            if slot_a.slot_id == slot_b.slot_id:
                continue

            if period_a.end and period_b.start and period_a.end > period_b.start:
                continue

            events.append(SwitchoverEvent(
                time=period_b.start,
                from_slot=slot_a.slot_id,
                to_slot=slot_b.slot_id,
                evidence=(
                    f"{slot_a.name}: {period_a.start.isoformat()} ~ {period_a.end.isoformat()}, "
                    f"{slot_b.name}: {period_b.start.isoformat()} ~ {period_b.end.isoformat()}"
                ),
            ))

        result.switchover_timeline = events
