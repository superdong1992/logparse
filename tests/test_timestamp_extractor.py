"""Tests for TimestampExtractor."""
from __future__ import annotations

import pytest


class TestTimestampExtractor:
    @pytest.fixture(autouse=True)
    def setup(self):
        from backend.parsing.timestamp_extractor import TimestampExtractor
        import re
        self.extractor = TimestampExtractor(
            ts_regex=re.compile(
                r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?"
            )
        )

    def test_extract_from_text_with_tz(self):
        stamps = self.extractor.extract_from_text("2026-01-03T00:01:00+08:00 msg")
        assert len(stamps) == 1
        assert stamps[0].tzinfo is not None

    def test_extract_from_text_without_tz(self):
        stamps = self.extractor.extract_from_text("2026-01-03T00:01:00 msg")
        assert len(stamps) == 1
        assert stamps[0].tzinfo is None
