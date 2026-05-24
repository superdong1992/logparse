"""ActivePeriod 构建：从时间戳序列中提取连续主控时段。"""

from __future__ import annotations

from datetime import timedelta

from backend.models import ActivePeriod, SlotInfo


class ActivePeriodBuilder:
    def __init__(self, gap_threshold_seconds: int):
        self._gap = timedelta(seconds=gap_threshold_seconds)

    def build(self, slot: SlotInfo) -> list[ActivePeriod]:
        all_stamps = slot.all_content_timestamps
        if not all_stamps:
            return []

        periods: list[ActivePeriod] = []
        seg_start = all_stamps[0]
        seg_end = all_stamps[0]

        for ts in all_stamps[1:]:
            if ts - seg_end <= self._gap:
                seg_end = ts
            else:
                periods.append(ActivePeriod(start=seg_start, end=seg_end))
                seg_start = ts
                seg_end = ts

        periods.append(ActivePeriod(start=seg_start, end=seg_end))
        return periods
