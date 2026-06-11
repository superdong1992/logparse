"""Tests for ScannerPlugin directory discovery."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from backend.plugins.default.scanner import ScannerPlugin


@pytest.fixture
def scanner():
    return ScannerPlugin(
        config={
            "diagnostic_dir": "diag",
            "private_dir": "varlog",
            "slot_dir_pattern": "slot_*",
            "diag_file_patterns": ["diag.zip", "diaglog_*.log.zip"],
            "filename_timestamp_regex": r".*_(\d{14})\..*",
            "private_dir_patterns": ["slot_*", "slot_*_cpu_*"],
            "archive_name": "varlog.zip",
            "journal_file_patterns": ["journal.log", "journal.log.*.gz"],
            "journal_sequence_regex": r"journal\.log(?:\.(\d+))?(?:\.gz)?",
            "compressed_extensions": [".gz", ".zip"],
        },
    )


def _create_mock_package(root: Path) -> Path:
    """Create a mock diagnostic package structure on disk."""
    diag_dir = root / "diag"
    diag_slot1 = diag_dir / "slot_1"
    diag_slot1.mkdir(parents=True)

    diag_zip = diag_slot1 / "diag.zip"
    with zipfile.ZipFile(diag_zip, "w") as zf:
        zf.writestr("diag_content.log", "2026-01-03T00:00:00 EXAMPLE msg")

    diaglog_zip = diag_slot1 / "diaglog_1_20260103000000.log.zip"
    with zipfile.ZipFile(diaglog_zip, "w") as zf:
        zf.writestr("diaglog_content.log", "2026-01-03T00:01:00 EXAMPLE msg")

    varlog_dir = root / "varlog" / "slot_1"
    varlog_dir.mkdir(parents=True)

    varlog_zip = varlog_dir / "varlog.zip"
    with zipfile.ZipFile(varlog_zip, "w") as zf:
        zf.writestr("varlog/journal.log", "Jan  3 00:00:00 dhcp-100: No[1] EXAMPLE msg")

    return root


def _create_pre_extracted_mock_package(root: Path) -> Path:
    """Create the structure produced by the unified decompression stage."""
    pkg_root = _create_mock_package(root)

    diag_extracted = pkg_root / "diag" / "slot_1" / "diag.zip_extracted"
    diag_extracted.mkdir()
    (diag_extracted / "diag_content.log").write_text(
        "2026-01-03T00:00:00 EXAMPLE msg",
        encoding="utf-8",
    )

    varlog_extracted = pkg_root / "varlog" / "slot_1" / "varlog.zip_extracted" / "varlog"
    varlog_extracted.mkdir(parents=True)
    (varlog_extracted / "journal.log").write_text(
        "Jan  3 00:00:00 dhcp-100: No[1] EXAMPLE msg",
        encoding="utf-8",
    )

    return pkg_root


def _scanner_with_loose(patterns: list[str]) -> ScannerPlugin:
    return ScannerPlugin(
        config={
            "diagnostic_dir": "diag",
            "private_dir": "varlog",
            "slot_dir_pattern": "slot_*",
            "diag_file_patterns": ["diag.zip", "diaglog_*.log.zip"],
            "filename_timestamp_regex": r".*_(\d{14})\..*",
            "private_dir_patterns": ["slot_*", "slot_*_cpu_*"],
            "archive_name": "varlog.zip",
            "journal_file_patterns": ["journal.log", "journal.log.*.gz"],
            "journal_sequence_regex": r"journal\.log(?:\.(\d+))?(?:\.gz)?",
            "compressed_extensions": [".gz", ".zip"],
            "loose_diagnostics": {
                "enabled": True,
                "file_patterns": patterns,
            },
        },
    )


class TestScannerPlugin:
    def test_discover_finds_slots(self, scanner, tmp_path):
        pkg_root = _create_mock_package(tmp_path)
        diag_slots, private_slots = scanner.discover(pkg_root)

        assert len(diag_slots) == 1
        assert diag_slots[0].slot_id == "1"
        assert len(diag_slots[0].diagnostic_logs) == 2

    def test_discover_finds_journal(self, scanner, tmp_path):
        pkg_root = _create_pre_extracted_mock_package(tmp_path)
        diag_slots, private_slots = scanner.discover(pkg_root)

        assert len(private_slots) == 1
        assert private_slots[0].slot_id == "1"
        assert len(private_slots[0].journal_logs) >= 1

    def test_discover_does_not_extract_varlog_archive(self, scanner, tmp_path):
        pkg_root = _create_mock_package(tmp_path)
        _diag_slots, private_slots = scanner.discover(pkg_root)

        assert len(private_slots) == 1
        assert private_slots[0].journal_logs == []
        assert not (pkg_root / "varlog" / "slot_1" / "varlog.zip_extracted").exists()

    def test_compressed_diag_entry_points_to_unified_extracted_dir(self, scanner, tmp_path):
        pkg_root = _create_pre_extracted_mock_package(tmp_path)
        diag_slots, _private_slots = scanner.discover(pkg_root)

        diag_zip = next(e for e in diag_slots[0].diagnostic_logs if e.name == "diag.zip")
        assert diag_zip.extracted_path.endswith("diag.zip_extracted")

    def test_empty_directory(self, scanner, tmp_path):
        diag_slots, private_slots = scanner.discover(tmp_path)
        assert diag_slots == []
        assert private_slots == []

    def test_missing_diag_dir(self, scanner, tmp_path):
        (tmp_path / "other").mkdir()
        diag_slots, private_slots = scanner.discover(tmp_path)
        assert diag_slots == []

    def test_discover_merges_loose_diagnostic_logs(self, tmp_path):
        pkg_root = _create_pre_extracted_mock_package(tmp_path)
        loose_file = pkg_root / "attachments" / "logs" / "loose_diag_20260103.log"
        loose_file.parent.mkdir(parents=True)
        loose_file.write_text(
            "2026-01-03T00:02:00 EXAMPLE loose diagnostic\n",
            encoding="utf-8",
        )

        diag_slots, _private_slots = _scanner_with_loose(["loose_diag_*.log"]).discover(pkg_root)

        loose_slot = next(slot for slot in diag_slots if slot.slot_id == "loose")
        assert loose_slot.name == "slot_loose"
        assert [entry.name for entry in loose_slot.diagnostic_logs] == ["loose_diag_20260103.log"]

    def test_loose_diagnostic_logs_are_deduplicated_by_expanded_content(self, tmp_path):
        pkg_root = _create_pre_extracted_mock_package(tmp_path)
        duplicate = pkg_root / "attachments" / "copy_diag.log"
        duplicate.parent.mkdir(parents=True)
        duplicate.write_text(
            "2026-01-03T00:00:00 EXAMPLE msg",
            encoding="utf-8",
        )

        diag_slots, _private_slots = _scanner_with_loose(["copy_diag.log"]).discover(pkg_root)

        assert [slot.slot_id for slot in diag_slots] == ["1"]
        assert [entry.name for entry in diag_slots[0].diagnostic_logs] == [
            "diag.zip",
            "diaglog_1_20260103000000.log.zip",
        ]

    def test_journal_scan_includes_sibling_varlog_prefixed_dirs(self, scanner, tmp_path):
        slot_dir = tmp_path / "varlog" / "slot_1" / "varlog_bundle.zip_extracted"
        (slot_dir / "varlog").mkdir(parents=True)
        (slot_dir / "varlog_other").mkdir()
        (slot_dir / "varlog" / "journal.log").write_text(
            "Jan  3 00:00:00 dhcp-100: No[1] EXAMPLE msg\n",
            encoding="utf-8",
        )
        (slot_dir / "varlog_other" / "journal.log.1.gz").write_bytes(b"compressed placeholder")

        _diag_slots, private_slots = scanner.discover(tmp_path)

        assert [log.name for log in private_slots[0].journal_logs] == [
            "journal.log",
            "journal.log.1.gz",
        ]

    def test_journal_scan_preserves_cpu_scope_for_varlog_prefixed_dirs(self, scanner, tmp_path):
        journal_dir = (
            tmp_path
            / "varlog"
            / "slot_1_cpu_2"
            / "varlog_cpu.zip_extracted"
            / "varlog_cpu"
        )
        journal_dir.mkdir(parents=True)
        (journal_dir / "journal.log").write_text(
            "Jan  3 00:00:00 dhcp-100: No[1] EXAMPLE msg\n",
            encoding="utf-8",
        )

        _diag_slots, private_slots = scanner.discover(tmp_path)

        assert private_slots[0].slot_id == "1"
        assert private_slots[0].cpu_id == "2"
        assert [log.name for log in private_slots[0].journal_logs] == ["journal.log"]
