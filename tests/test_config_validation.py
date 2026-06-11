"""Tests for backend/config_validation.py."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.config_validation import validate_config, validate_mechanism_module_config


ROOT = Path(__file__).resolve().parents[1]


def _minimal_valid_config() -> dict:
    return {
        "products": {
            "default": {
                "discovery": {
                    "plugin": "backend.plugins.default.scanner.ScannerPlugin",
                    "config": {},
                },
                "log_parser": {
                    "plugin": "backend.plugins.default.parser.ParserPlugin",
                    "config": {"timestamp_regex": r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"},
                },
            },
        },
    }


class TestValidatePipelineConfig:
    def test_valid_pipeline_performance_options(self):
        cfg = _minimal_valid_config()
        cfg["pipeline"] = {
            "debug_expand_gz": False,
            "extraction_workers": "auto",
            "diagnostic_scan_workers": 4,
        }

        assert validate_config(cfg) == []

    def test_rejects_invalid_worker_count(self):
        cfg = _minimal_valid_config()
        cfg["pipeline"] = {
            "extraction_workers": 0,
            "diagnostic_scan_workers": "many",
        }

        errors = validate_config(cfg)

        assert "pipeline.extraction_workers" in errors[0]
        assert "pipeline.diagnostic_scan_workers" in errors[1]

    def test_rejects_unbounded_worker_count(self):
        cfg = _minimal_valid_config()
        cfg["pipeline"] = {"extraction_workers": 999999}

        errors = validate_config(cfg)

        assert errors == ["pipeline.extraction_workers must be 'auto' or a positive integer"]

    def test_rejects_boolean_worker_count(self):
        cfg = _minimal_valid_config()
        cfg["pipeline"] = {
            "extraction_workers": True,
            "diagnostic_scan_workers": False,
        }

        errors = validate_config(cfg)

        assert "pipeline.extraction_workers" in errors[0]
        assert "pipeline.diagnostic_scan_workers" in errors[1]

    def test_rejects_non_boolean_debug_expand_gz(self):
        cfg = _minimal_valid_config()
        cfg["pipeline"] = {"debug_expand_gz": "false"}

        errors = validate_config(cfg)

        assert errors == ["pipeline.debug_expand_gz must be a boolean"]

    def test_rejects_null_pipeline_config(self):
        cfg = _minimal_valid_config()
        cfg["pipeline"] = None

        errors = validate_config(cfg)

        assert errors == ["pipeline must be an object"]


class TestValidateMechanismModuleConfig:
    def test_valid_config_no_errors(self):
        cfg = {
            "module_name": "EXAMPLE",
            "diag_pattern": r"Slot=(?P<Slot>\d+);CPU=(?P<CPU_Id>\d+);Proc=(?P<ProcessName>\w+);Ctx=(?P<Context>.+)",
            "journal": {"line_pattern": r"(\S+)\s+No\[(\d+)\]\s+(\d{4}-\d{2}-\d{2}\S+)\s+(.*)"},
            "sequence_pattern": r"No\[(\d+)\]",
            "lifecycle_split": {
                "process_name_mapping": {"canonical": ["alias"]},
                "reliable_processes": ["canonical"],
                "multi_instance_processes": ["worker"],
            },
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

    def test_legacy_module_lifecycle_fields_are_rejected(self):
        for field, value in (
            ("board_restart_" + "indicator", "dhcp"),
            ("board_restart_" + "whitelist", ["DHCP"]),
            ("process_name_" + "mapping", {"DHCP": "dhcpd"}),
        ):
            cfg = {"module_name": "EXAMPLE", field: value}

            errors = validate_mechanism_module_config("module1", cfg)

            assert errors
            assert field in errors[0]

    def test_lifecycle_split_reliable_processes_accepts_flat_list(self):
        cfg = {
            "module_name": "EXAMPLE",
            "lifecycle_split": {
                "process_name_mapping": {"board_anchor": ["boardd"]},
                "reliable_processes": ["board_anchor"],
                "multi_instance_processes": ["multi"],
            },
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors == []

    def test_lifecycle_split_legacy_reliable_board_cpu_lists_are_rejected(self):
        cfg = {
            "module_name": "EXAMPLE",
            "lifecycle_split": {
                "reliable_processes": {
                    "board": ["anchor"],
                    "cpu": ["anchor"],
                },
            },
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "reliable_processes" in errors[0]
        assert "list" in errors[0]

    def test_lifecycle_split_reliable_processes_none_means_empty(self):
        cfg = {
            "module_name": "EXAMPLE",
            "lifecycle_split": {
                "reliable_processes": None,
            },
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors == []

    def test_lifecycle_split_conflict_is_checked_after_name_mapping(self):
        cfg = {
            "module_name": "EXAMPLE",
            "lifecycle_split": {
                "process_name_mapping": {"canonical_proc": ["alias_proc"]},
                "reliable_processes": ["alias_proc"],
                "multi_instance_processes": ["canonical_proc"],
            },
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "canonical_proc" in errors[0]

    def test_lifecycle_split_enabled_is_rejected(self):
        cfg = {
            "module_name": "EXAMPLE",
            "lifecycle_split": {
                "enabled": True,
            },
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "enabled" in errors[0]

    def test_lifecycle_split_algorithm_is_rejected(self):
        cfg = {
            "module_name": "EXAMPLE",
            "lifecycle_split": {
                "algorithm": "interval_" + "v2",
            },
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "algorithm" in errors[0]

    def test_lifecycle_split_process_name_mapping_must_be_object(self):
        cfg = {
            "module_name": "EXAMPLE",
            "lifecycle_split": {
                "process_name_mapping": [],
            },
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "process_name_mapping" in errors[0]
        assert "object" in errors[0]

    def test_lifecycle_split_reliable_processes_rejects_non_list(self):
        cfg = {
            "module_name": "EXAMPLE",
            "lifecycle_split": {
                "reliable_processes": "worker",
            },
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "reliable_processes" in errors[0]
        assert "list" in errors[0]

    def test_lifecycle_split_multi_instance_processes_must_be_list(self):
        cfg = {
            "module_name": "EXAMPLE",
            "lifecycle_split": {
                "multi_instance_processes": "worker",
            },
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "multi_instance_processes" in errors[0]
        assert "list" in errors[0]

    def test_lifecycle_split_reliable_multi_conflict_is_case_insensitive(self):
        cfg = {
            "module_name": "EXAMPLE",
            "lifecycle_split": {
                "reliable_processes": ["Proc"],
                "multi_instance_processes": ["proc"],
            },
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "proc" in errors[0].lower()

    def test_multiple_errors(self):
        cfg = {
            "diag_pattern": r"((bad",
            "journal": {"line_pattern2": r"[unclosed"},
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert len(errors) >= 2

    def test_journal_pattern_requires_at_least_three_capture_groups(self):
        cfg = {
            "module_name": "EXAMPLE",
            "journal": {"line_pattern": r"(proc) (context)"},
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "需要 3 或 4 个捕获组" in errors[0]

    def test_journal_pattern_with_four_groups_passes(self):
        cfg = {
            "module_name": "EXAMPLE",
            "journal": {"line_pattern": r"(proc) (pid) (seq) (context)"},
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert not any("捕获组" in e for e in errors)

    def test_journal_pattern_with_three_groups_passes_for_no_sequence(self):
        cfg = {
            "module_name": "EXAMPLE",
            "journal": {"line_pattern2": r"(\S+?)(?:-(\d+))?:\s+(.+)"},
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert not any("捕获组" in e for e in errors)

    def test_journal_pattern_with_two_groups_fails(self):
        cfg = {
            "module_name": "EXAMPLE",
            "journal": {"line_pattern2": r"(\S+):\s+(.+)"},
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "journal.line_pattern2" in errors[0]

    def test_journal_pattern_with_three_groups_and_sequence_fails(self):
        cfg = {
            "module_name": "EXAMPLE",
            "journal": {"line_pattern2": r"^(\S+):\s+No\[(\d+)\](.+)$"},
            "sequence_pattern": r"No\[(\d+)\]",
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "包含序号格式时需要 4 个捕获组" in errors[0]

    @pytest.mark.parametrize(
        "value",
        [
            "MODULE1",
            ["MODULE1", 123],
            ["MODULE1", ""],
        ],
    )
    def test_line_pattern2_required_substrings_must_be_non_empty_string_list(self, value):
        cfg = {
            "module_name": "EXAMPLE",
            "journal": {
                "line_pattern2": r"(\S+?)(?:-(\d+))?:\s+(.+)",
                "line_pattern2_required_substrings": value,
            },
        }

        errors = validate_mechanism_module_config("module1", cfg)

        assert any("line_pattern2_required_substrings" in error for error in errors)

    def test_line_pattern2_required_substrings_accepts_string_list(self):
        cfg = {
            "module_name": "EXAMPLE",
            "journal": {
                "line_pattern2": r"(\S+?)(?:-(\d+))?:\s+(.+)",
                "line_pattern2_required_substrings": ["MODULE1", "module1-alt"],
            },
        }

        errors = validate_mechanism_module_config("module1", cfg)

        assert not any("line_pattern2_required_substrings" in error for error in errors)


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
                        "plugin": "backend.plugins.mechanisms.module1.Module1Plugin",
                        "enabled": True,
                        "config": {
                            "module_name": "EXAMPLE",
                            "journal": {
                                "line_pattern": r"(\S+)\s+No\[(\d+)\]\s+(\S+)\s+(.*)",
                            },
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
        cfg["log_parser"]["config"]["mechanism_modules"]["module1"]["config"]["journal"] = {
            "line_pattern": r"(a) (b)",
        }
        errors = validate_config({"products": {"default": cfg}})
        assert any("捕获组" in e for e in errors)

    def test_diag_pattern_missing_named_groups(self):
        cfg = _valid_product_config()
        mod = cfg["log_parser"]["config"]["mechanism_modules"]["module1"]["config"]
        mod["diag_pattern"] = r"Slot=(?P<Slot>\d+)"
        errors = validate_config({"products": {"default": cfg}})
        assert any("缺少命名组" in e for e in errors)

    def test_mechanism_module_requires_plugin(self):
        cfg = _valid_product_config()
        del cfg["log_parser"]["config"]["mechanism_modules"]["module1"]["plugin"]

        errors = validate_config({"products": {"default": cfg}})

        assert any("mechanism_modules.module1.plugin" in e for e in errors)

    def test_mechanism_module_plugin_must_be_loadable(self):
        cfg = _valid_product_config()
        cfg["log_parser"]["config"]["mechanism_modules"]["module1"]["plugin"] = "bad.module.Plugin"

        errors = validate_config({"products": {"default": cfg}})

        assert any("bad.module.Plugin" in e for e in errors)

    def test_mechanism_module_nested_config_is_validated(self):
        cfg = _valid_product_config()
        mod = cfg["log_parser"]["config"]["mechanism_modules"]["module1"]
        mod["config"]["diag_pattern"] = r"Slot=(?P<Slot>\d+)"

        errors = validate_config({"products": {"default": cfg}})

        assert any("CPU_Id" in e and "ProcessName" in e for e in errors)

    def test_valid_glob_pattern_passes(self):
        cfg = _valid_product_config()
        cfg["discovery"]["config"]["diag_file_patterns"] = ["diag_*.zip"]
        errors = validate_config({"products": {"default": cfg}})
        assert not any("glob" in e for e in errors)

    def test_valid_loose_diagnostics_config_passes(self):
        cfg = _valid_product_config()
        cfg["discovery"]["config"]["loose_diagnostics"] = {
            "enabled": True,
            "file_patterns": ["diag_*.log", "diaglog_*.zip"],
        }

        errors = validate_config({"products": {"default": cfg}})

        assert not any("loose_diagnostics" in e for e in errors)

    def test_loose_diagnostics_enabled_must_be_boolean(self):
        cfg = _valid_product_config()
        cfg["discovery"]["config"]["loose_diagnostics"] = {
            "enabled": "yes",
            "file_patterns": ["diag_*.log"],
        }

        errors = validate_config({"products": {"default": cfg}})

        assert any("loose_diagnostics.enabled" in e for e in errors)

    def test_loose_diagnostics_file_patterns_must_be_valid_globs(self):
        cfg = _valid_product_config()
        cfg["discovery"]["config"]["loose_diagnostics"] = {
            "enabled": True,
            "file_patterns": [None],
        }

        errors = validate_config({"products": {"default": cfg}})

        assert any("loose_diagnostics.file_patterns" in e for e in errors)

    def test_product_config_not_dict(self):
        errors = validate_config({"products": {"default": "not a dict"}})
        assert any("必须是对象" in e for e in errors)


class TestPluginSubclassValidation:
    def test_plugin_not_subclass_of_discovery_base(self, monkeypatch):
        """插件类存在但未继承 DirectoryDiscoveryPlugin 时应报错。"""
        import types
        import importlib as il

        class FakeScanner:
            def discover(self, root):
                pass

        fake_module = types.ModuleType("fake_scanner")
        fake_module.FakeScanner = FakeScanner

        original_import = il.import_module

        def fake_import(name, *args, **kwargs):
            if name == "fake_scanner":
                return fake_module
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(il, "import_module", fake_import)

        cfg = _valid_product_config()
        cfg["discovery"]["plugin"] = "fake_scanner.FakeScanner"
        errors = validate_config({"products": {"default": cfg}})
        assert any("DirectoryDiscoveryPlugin" in e for e in errors)

    def test_plugin_not_subclass_of_parser_base(self, monkeypatch):
        """插件类存在但未继承 LogParserPlugin 时应报错。"""
        import types
        import importlib as il

        class FakeParser:
            def parse(self, result):
                pass
            def write_output(self, mr, d):
                pass

        fake_module = types.ModuleType("fake_parser")
        fake_module.FakeParser = FakeParser

        original_import = il.import_module

        def fake_import(name, *args, **kwargs):
            if name == "fake_parser":
                return fake_module
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(il, "import_module", fake_import)

        cfg = _valid_product_config()
        cfg["log_parser"]["plugin"] = "fake_parser.FakeParser"
        errors = validate_config({"products": {"default": cfg}})
        assert any("LogParserPlugin" in e for e in errors)

    def test_valid_plugins_pass_subclass_check(self):
        """使用实际插件类时应通过子类校验。"""
        errors = validate_config({"products": {"default": _valid_product_config()}})
        assert not any("子类" in e for e in errors)


class TestShippedConfigFiles:
    def test_config_yaml_validates_as_v3_only_current_entry(self):
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))

        assert validate_config(cfg) == []

        default_module = (
            cfg["products"]["default"]["log_parser"]["config"]["mechanism_modules"]["module1"]["config"]
        )
        compact_module = (
            cfg["products"]["compact"]["log_parser"]["config"]["mechanism_modules"]["ctrl"]["config"]
        )
        for module in (default_module, compact_module):
            assert set(module["lifecycle_split"]) == {
                "process_name_mapping",
                "reliable_processes",
                "multi_instance_processes",
            }
            assert "enabled" not in module["lifecycle_split"]
            assert "algorithm" not in module["lifecycle_split"]

        legacy_lifecycle_fields = {
            "board_restart_" + "indicator",
            "board_restart_" + "whitelist",
            "process_name_" + "mapping",
        }
        for product in cfg["products"].values():
            modules = product["log_parser"]["config"]["mechanism_modules"].values()
            for module in modules:
                assert legacy_lifecycle_fields.isdisjoint(module["config"])

    def test_lifecycle_v2_config_is_archived_not_current_entry(self):
        archived_name = "config.lifecycle-" + "v2.yaml"
        assert not (ROOT / archived_name).exists()
        assert (ROOT / "docs" / "archive" / "lifecycle-v2" / archived_name).exists()
