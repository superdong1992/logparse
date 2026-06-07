"""Timestamp extraction for log text."""
from __future__ import annotations

import re
from datetime import datetime


class TimestampExtractor:
    def __init__(self, ts_regex: re.Pattern):
        self._ts_regex = ts_regex

    def extract_from_text(self, text: str) -> list[datetime]:
        stamps: list[datetime] = []
        for m in self._ts_regex.finditer(text):
            ts_str = m.group(1)
            tz_str = m.group(2)
            if tz_str:
                ts_str = ts_str + tz_str
            try:
                stamps.append(datetime.fromisoformat(ts_str))
            except ValueError:
                continue
        return stamps
