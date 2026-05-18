"""Tests for backend/utils.py pure functions."""
from __future__ import annotations

import re
from datetime import datetime

import pytest

from backend.utils import (
    extract_content_timestamps,
    extract_dump_time,
    extract_journal_sequence,
    extract_private_slot_info,
    extract_slot_id,
    glob_to_regex,
    is_compressed,
)


class TestGlobToRegex:
    def test_star_matches_any(self):
        pat = glob_to_regex("slot_*")
        assert pat.match("slot_1")
        assert pat.match("slot_abc")
        assert not pat.match("slot")

    def test_question_mark_matches_one(self):
        pat = glob_to_regex("file_?.log")
        assert pat.match("file_1.log")
        assert not pat.match("file_12.log")

    def test_case_insensitive(self):
        pat = glob_to_regex("diag.zip")
        assert pat.match("DIAG.ZIP")

    def test_literal_match(self):
        pat = glob_to_regex("journal.log")
        assert pat.match("journal.log")
        assert not pat.match("journal.log.1")


class TestExtractSlotId:
    def test_simple(self):
        assert extract_slot_id("slot_1") == "1"

    def test_with_cpu(self):
        assert extract_slot_id("slot_1_cpu_2") == "1_cpu_2"

    def test_passthrough(self):
        assert extract_slot_id("other") == "other"


class TestExtractPrivateSlotInfo:
    def test_board_slot(self):
        slot_id, cpu_id = extract_private_slot_info("slot_1")
        assert slot_id == "1"
        assert cpu_id is None

    def test_cpu_subcard(self):
        slot_id, cpu_id = extract_private_slot_info("slot_1_cpu_2")
        assert slot_id == "1"
        assert cpu_id == "2"

    def test_unknown(self):
        slot_id, cpu_id = extract_private_slot_info("other")
        assert slot_id == "other"
        assert cpu_id is None


class TestExtractDumpTime:
    def test_valid_filename(self):
        regex = re.compile(r".*_(\d{14})\..*")
        dt = extract_dump_time("diaglog_1_20260103000000.log.zip", regex)
        assert dt == datetime(2026, 1, 3, 0, 0, 0)

    def test_no_match(self):
        regex = re.compile(r".*_(\d{14})\..*")
        assert extract_dump_time("diag.zip", regex) is None


class TestExtractJournalSequence:
    def test_current(self):
        regex = re.compile(r"journal\.log(?:\.(\d+))?(?:\.gz)?", re.IGNORECASE)
        assert extract_journal_sequence("journal.log", regex) == 0

    def test_history(self):
        regex = re.compile(r"journal\.log(?:\.(\d+))?(?:\.gz)?", re.IGNORECASE)
        assert extract_journal_sequence("journal.log.3", regex) == 3

    def test_gz(self):
        regex = re.compile(r"journal\.log(?:\.(\d+))?(?:\.gz)?", re.IGNORECASE)
        assert extract_journal_sequence("journal.log.1.gz", regex) == 1


class TestExtractContentTimestamps:
    def test_with_timezone(self):
        regex = re.compile(
            r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?"
        )
        stamps = extract_content_timestamps(
            "2026-01-03T00:01:00.100000+08:00 some log line", regex
        )
        assert len(stamps) == 1
        assert stamps[0].tzinfo is not None

    def test_without_timezone(self):
        regex = re.compile(
            r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?"
        )
        stamps = extract_content_timestamps(
            "2026-01-03T00:01:00.100000 some log line", regex
        )
        assert len(stamps) == 1
        assert stamps[0].tzinfo is None

    def test_multiple_timestamps(self):
        regex = re.compile(
            r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?"
        )
        stamps = extract_content_timestamps(
            "2026-01-03T00:01:00 first\n2026-01-03T00:02:00+08:00 second", regex
        )
        assert len(stamps) == 2


class TestIsCompressed:
    EXTS = [".gz", ".zip", ".tar.gz", ".tgz", ".tar"]

    def test_zip(self):
        assert is_compressed("diag.zip", self.EXTS)

    def test_gz(self):
        assert is_compressed("journal.log.1.gz", self.EXTS)

    def test_not_compressed(self):
        assert not is_compressed("debug.log", self.EXTS)

    def test_case_insensitive(self):
        assert is_compressed("FILE.ZIP", self.EXTS)
