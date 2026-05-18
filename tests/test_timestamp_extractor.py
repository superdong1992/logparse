"""Tests for TimestampExtractor."""
from __future__ import annotations

import gzip
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from backend.models import LogEntry


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

    def test_extract_from_file(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("2026-01-03T00:01:00 line1\n2026-01-03T00:02:00 line2", encoding="utf-8")
        stamps = self.extractor.extract_from_file(f)
        assert len(stamps) == 2

    def test_extract_from_gz_file(self, tmp_path):
        f = tmp_path / "test.log.gz"
        with gzip.open(f, "wt", encoding="utf-8") as fh:
            fh.write("2026-01-03T00:01:00 gz line")
        stamps = self.extractor.extract_from_file(f)
        assert len(stamps) == 1

    def test_extract_from_entry_plain_file(self, tmp_path):
        f = tmp_path / "plain.log"
        f.write_text("2026-01-03T00:01:00 plain", encoding="utf-8")
        entry = LogEntry(path=str(f), name="plain.log", size_bytes=100)
        stamps = self.extractor.extract_from_entry(entry)
        assert len(stamps) == 1

    def test_extract_from_entry_compressed_dir(self, tmp_path):
        ext_dir = tmp_path / "extracted"
        ext_dir.mkdir()
        (ext_dir / "inner.log").write_text("2026-01-03T00:01:00 inner", encoding="utf-8")
        entry = LogEntry(
            path=str(tmp_path / "fake.zip"),
            name="fake.zip",
            size_bytes=100,
            compressed=True,
            extracted_path=str(ext_dir),
        )
        stamps = self.extractor.extract_from_entry(entry)
        assert len(stamps) == 1
