from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from backend.config import ConfigLoader
from backend.models import ActivePeriod, LogEntry, SlotInfo


class LogParser:
    """日志内容解析器：提取时间戳，构建 ActivePeriod。"""

    def __init__(self, config_loader: ConfigLoader):
        self.config = config_loader

    def extract_timestamps(self, entry: LogEntry) -> list[datetime]:
        """提取单个 LogEntry 对应内容中的所有时间戳。"""
        stamps: list[datetime] = []

        if entry.extracted_path:
            ext_dir = Path(entry.extracted_path)
            if ext_dir.is_dir():
                for f in ext_dir.rglob("*"):
                    if f.is_file():
                        stamps.extend(self._parse_file(f))
                return sorted(stamps)

        file_path = Path(entry.path)
        if file_path.is_file():
            return sorted(self._parse_file(file_path))

        return stamps

    def _parse_file(self, file_path: Path) -> list[datetime]:
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            try:
                text = file_path.read_text(encoding="gbk", errors="replace")
            except Exception:
                return []
        return self.config.extract_content_timestamps(text)

    def build_active_periods(self, slot: SlotInfo) -> list[ActivePeriod]:
        """纯时间戳 gap 切分为 ActivePeriod 列表。"""
        all_stamps = slot.all_content_timestamps
        if not all_stamps:
            return []

        gap = timedelta(seconds=self.config.gap_threshold_seconds)
        periods: list[ActivePeriod] = []
        seg_start = all_stamps[0]
        seg_end = all_stamps[0]

        for ts in all_stamps[1:]:
            if ts - seg_end <= gap:
                seg_end = ts
            else:
                periods.append(ActivePeriod(start=seg_start, end=seg_end))
                seg_start = ts
                seg_end = ts

        periods.append(ActivePeriod(start=seg_start, end=seg_end))
        return periods

    def build_all_periods(self, slots: list[SlotInfo]) -> None:
        """为所有 slot 提取时间戳并构建 ActivePeriod。"""
        for slot in slots:
            for entry in slot.diagnostic_logs:
                entry.content_timestamps = self.extract_timestamps(entry)
            for p in self.build_active_periods(slot):
                slot.add_active_period(p)
