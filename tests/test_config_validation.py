"""Tests for backend/config_validation.py."""
from __future__ import annotations

import pytest

from backend.config_validation import validate_mechanism_module_config


class TestValidateMechanismModuleConfig:
    def test_valid_config_no_errors(self):
        cfg = {
            "module_name": "EXAMPLE",
            "diag_pattern": r"Slot=(?P<Slot>\d+);CPU=(?P<CPU_Id>\d+);Proc=(?P<ProcessName>\w+);Ctx=(?P<Context>.+)",
            "journal": {"line_pattern": r"(\S+)\s+No\[(\d+)\]\s+(\d{4}-\d{2}-\d{2}\S+)\s+(.*)"},
            "sequence_pattern": r"No\[(\d+)\]",
            "board_restart_whitelist": ["PROC1"],
            "process_name_mapping": {"DHCP": "dhcpd"},
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors == []

    def test_missing_module_name(self):
        cfg = {"diag_pattern": r"Slot=(?P<Slot>\d+)"}
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "module_name" in errors[0]

    def test_diag_pattern_missing_required_groups(self):
        cfg = {
            "module_name": "EXAMPLE",
            "diag_pattern": r"Slot=(?P<Slot>\d+)",
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "CPU_Id" in errors[0]
        assert "ProcessName" in errors[0]
        assert "Context" in errors[0]

    def test_diag_pattern_invalid_regex(self):
        cfg = {
            "module_name": "EXAMPLE",
            "diag_pattern": r"Slot=(?P<Slot\d+",
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "正则非法" in errors[0]

    def test_journal_pattern_invalid_regex(self):
        cfg = {
            "module_name": "EXAMPLE",
            "journal": {"line_pattern": r"((unclosed"},
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "line_pattern" in errors[0]
        assert "正则非法" in errors[0]

    def test_sequence_pattern_invalid_regex(self):
        cfg = {
            "module_name": "EXAMPLE",
            "sequence_pattern": r"No[unclosed",
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "sequence_pattern" in errors[0]

    def test_whitelist_name_map_conflict(self):
        cfg = {
            "module_name": "EXAMPLE",
            "board_restart_whitelist": ["DHCP"],
            "process_name_mapping": {"DHCP": "dhcpd"},
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "dhcp" in errors[0].lower()

    def test_multiple_errors(self):
        cfg = {
            "diag_pattern": r"((bad",
            "journal": {"line_pattern2": r"[unclosed"},
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert len(errors) >= 2

    def test_journal_pattern_requires_four_capture_groups(self):
        cfg = {
            "module_name": "EXAMPLE",
            "journal": {"line_pattern": r"(proc) (pid) (context)"},
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "至少需要 4 个捕获组" in errors[0]

    def test_journal_pattern_with_four_groups_passes(self):
        cfg = {
            "module_name": "EXAMPLE",
            "journal": {"line_pattern": r"(proc) (pid) (seq) (context)"},
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert not any("至少需要 4 个捕获组" in e for e in errors)
