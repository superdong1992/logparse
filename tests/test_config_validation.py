"""Tests for backend/config_validation.py."""
from __future__ import annotations

import pytest

from backend.config_validation import validate_config, validate_mechanism_module_config


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


# ── 使用实际插件路径的 fixture ──────────────────────

_DEFAULT_DISCOVERY = "backend.plugins.default.scanner.ScannerPlugin"
_DEFAULT_PARSER = "backend.plugins.default.parser.ParserPlugin"
_COMPACT_DISCOVERY = "backend.plugins.compact.scanner.CompactScannerPlugin"


def _valid_product_config():
    """最小合法产品配置。"""
    return {
        "discovery": {
            "plugin": _DEFAULT_DISCOVERY,
            "config": {
                "diagnostic_dir": "diag",
                "private_dir": "varlog",
                "slot_dir_pattern": "slot_*",
                "diag_file_patterns": ["diag.zip"],
            },
        },
        "log_parser": {
            "plugin": _DEFAULT_PARSER,
            "config": {
                "timestamp_regex": r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})([+-]\d{2}:\d{2})?",
                "mechanism_modules": {
                    "module1": {
                        "module_name": "EXAMPLE",
                        "journal": {
                            "line_pattern": r"(\S+)\s+No\[(\d+)\]\s+(\S+)\s+(.*)",
                        },
                    },
                },
            },
        },
    }


class TestValidateConfig:
    def test_valid_config_passes(self):
        errors = validate_config({"products": {"default": _valid_product_config()}})
        assert errors == []

    def test_missing_products(self):
        errors = validate_config({})
        assert errors
        assert any("products" in e for e in errors)

    def test_empty_products(self):
        errors = validate_config({"products": {}})
        assert errors

    def test_missing_discovery(self):
        cfg = _valid_product_config()
        del cfg["discovery"]
        errors = validate_config({"products": {"default": cfg}})
        assert any("discovery" in e for e in errors)

    def test_missing_log_parser(self):
        cfg = _valid_product_config()
        del cfg["log_parser"]
        errors = validate_config({"products": {"default": cfg}})
        assert any("log_parser" in e for e in errors)

    def test_discovery_plugin_empty(self):
        cfg = _valid_product_config()
        cfg["discovery"]["plugin"] = ""
        errors = validate_config({"products": {"default": cfg}})
        assert any("plugin" in e for e in errors)

    def test_unknown_plugin(self):
        cfg = _valid_product_config()
        cfg["discovery"]["plugin"] = "nonexistent.module.BadClass"
        errors = validate_config({"products": {"default": cfg}})
        assert any("无法导入模块" in e for e in errors)

    def test_plugin_class_missing(self):
        cfg = _valid_product_config()
        cfg["log_parser"]["plugin"] = "backend.plugins.default.parser.NonexistentClass"
        errors = validate_config({"products": {"default": cfg}})
        assert any("缺少类" in e for e in errors)

    def test_missing_timestamp_regex(self):
        cfg = _valid_product_config()
        del cfg["log_parser"]["config"]["timestamp_regex"]
        errors = validate_config({"products": {"default": cfg}})
        assert any("timestamp_regex" in e for e in errors)

    def test_invalid_timestamp_regex(self):
        cfg = _valid_product_config()
        cfg["log_parser"]["config"]["timestamp_regex"] = "((bad"
        errors = validate_config({"products": {"default": cfg}})
        assert any("timestamp_regex" in e and "正则" in e for e in errors)

    def test_journal_pattern_insufficient_groups(self):
        cfg = _valid_product_config()
        cfg["log_parser"]["config"]["mechanism_modules"]["module1"]["journal"] = {
            "line_pattern": r"(a) (b) (c)",
        }
        errors = validate_config({"products": {"default": cfg}})
        assert any("至少需要 4 个捕获组" in e for e in errors)

    def test_diag_pattern_missing_named_groups(self):
        cfg = _valid_product_config()
        mod = cfg["log_parser"]["config"]["mechanism_modules"]["module1"]
        mod["diag_pattern"] = r"Slot=(?P<Slot>\d+)"
        errors = validate_config({"products": {"default": cfg}})
        assert any("缺少命名组" in e for e in errors)

    def test_valid_glob_pattern_passes(self):
        cfg = _valid_product_config()
        cfg["discovery"]["config"]["diag_file_patterns"] = ["diag_*.zip"]
        errors = validate_config({"products": {"default": cfg}})
        assert not any("glob" in e for e in errors)

    def test_product_config_not_dict(self):
        errors = validate_config({"products": {"default": "not a dict"}})
        assert any("必须是对象" in e for e in errors)
