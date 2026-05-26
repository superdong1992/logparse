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
