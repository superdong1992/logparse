"""Tests for backend/utils.py pure functions."""
from __future__ import annotations

import re
from datetime import datetime


from backend.utils import (
    extract_dump_time,
    extract_journal_sequence,
    extract_private_slot_info,
    extract_slot_id,
    glob_to_regex,
    is_compressed,
    safe_log_filename,
    safe_path_segment,
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


class TestSafePathSegment:
    def test_preserves_simple_segment(self):
        assert safe_path_segment("2") == "2"
        assert safe_path_segment("1_2") == "1_2"
        assert safe_path_segment("EXAMPLE") == "EXAMPLE"
        assert safe_path_segment("MODULE2") == "MODULE2"
        assert safe_path_segment("20260103T000100-20260103T000200") == "20260103T000100-20260103T000200"

    def test_replaces_path_separators(self):
        assert safe_path_segment("1/2").startswith("1~U0000002f2~H")
        assert safe_path_segment(r"1\2").startswith("1~U0000005c2~H")

    def test_replaces_windows_forbidden_characters(self):
        segment = safe_path_segment('a:b*?c"d<e>f|g')
        assert segment.startswith(
            "a~U0000003ab~U0000002a~U0000003fc~U00000022d"
            "~U0000003ce~U0000003ef~U0000007cg~H"
        )

    def test_avoids_escape_collisions(self):
        assert safe_path_segment("a") != safe_path_segment("a.")
        assert safe_path_segment("a") != safe_path_segment(" a")
        assert safe_path_segment("a") != safe_path_segment("a ")
        assert safe_path_segment("1/2") != safe_path_segment("1_2")
        assert safe_path_segment("1/2") != safe_path_segment("1~U0000002f2")
        assert safe_path_segment(chr(0x1000) + "0") != safe_path_segment(chr(0x10000))

    def test_encodes_windows_reserved_names(self):
        assert safe_path_segment("CON").startswith("CON~H")
        assert safe_path_segment("CON.txt").startswith("CON.txt~H")

    def test_safe_log_filename_escapes_process_fields(self):
        filename = safe_log_filename(r"..\..\escape", "10/20")
        expected_name = safe_path_segment(r"..\..\escape")
        expected_pid = safe_path_segment("10/20")
        assert filename == f"{expected_name}~P{expected_pid}.log"
        assert "/" not in filename
        assert "\\" not in filename

    def test_safe_log_filename_preserves_legacy_safe_names(self):
        assert safe_log_filename("svc", "") == "svc.log"
        assert safe_log_filename("svc", "100") == "svc-100.log"
        assert safe_log_filename("SERVICE", "123") == "SERVICE-123.log"


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
