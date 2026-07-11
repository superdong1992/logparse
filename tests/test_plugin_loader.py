"""Tests for backend/plugins/loader.py."""
from __future__ import annotations

import pytest

from backend.contracts.plugins import DiagnosticScanBatch, MechanismContext
from backend.extensions.products.current.mechanism_input import CurrentMechanismInput
from backend.models import MechResult, ParseResult
from backend.plugins.base import DirectoryDiscoveryPlugin, LogParserPlugin
from backend.plugins.loader import (
    instantiate_mechanism_plugins,
    instantiate_plugin,
    load_plugin_class,
)


class TestInstantiatePlugin:
    def test_load_scanner_plugin(self):
        plugin = instantiate_plugin(
            "backend.plugins.default.scanner.ScannerPlugin",
            DirectoryDiscoveryPlugin,
            {"diagnostic_dir": "diag", "private_dir": "varlog"},
        )
        assert isinstance(plugin, DirectoryDiscoveryPlugin)

    def test_load_parser_plugin(self, sample_config):
        plugin = instantiate_plugin(
            "backend.plugins.default.parser.ParserPlugin",
            LogParserPlugin,
            sample_config,
        )
        assert isinstance(plugin, LogParserPlugin)

    def test_wrong_base_class(self, sample_config):
        with pytest.raises(TypeError):
            instantiate_plugin(
                "backend.plugins.default.parser.ParserPlugin",
                DirectoryDiscoveryPlugin,
                sample_config,
            )

    def test_invalid_module(self):
        with pytest.raises(ModuleNotFoundError):
            instantiate_plugin(
                "nonexistent.module.Class",
                DirectoryDiscoveryPlugin,
                {},
            )

    def test_invalid_class(self):
        with pytest.raises(AttributeError):
            instantiate_plugin(
                "backend.plugins.default.scanner.NonExistentClass",
                DirectoryDiscoveryPlugin,
                {},
            )


def test_instantiate_mechanism_module_plugin():
    from backend.plugins.loader import instantiate_plugin
    from backend.extensions.mechanisms.base import MechanismPlugin

    plugin = instantiate_plugin(
        "backend.plugins.mechanisms.module1.Module1Plugin",
        MechanismPlugin,
        {"module_name": "EXAMPLE"},
        module_key="module1",
        ts_extractor=None,
    )

    assert plugin.module_key == "module1"
    assert plugin.module_name == "EXAMPLE"


def test_mechanism_plugin_base_rejects_wrong_class():
    import pytest

    from backend.plugins.loader import instantiate_plugin
    from backend.plugins.mechanisms.base import MechanismModulePlugin

    with pytest.raises(TypeError):
        instantiate_plugin(
            "backend.plugins.default.parser.ParserPlugin",
            MechanismModulePlugin,
            {},
        )


def test_load_plugin_class_does_not_instantiate():
    cls = load_plugin_class(
        "backend.plugins.default.scanner.ScannerPlugin",
        DirectoryDiscoveryPlugin,
    )

    assert cls.__name__ == "ScannerPlugin"


def test_instantiate_mechanisms_uses_dependency_order(sample_config):
    module1 = sample_config["mechanism_modules"]["module1"]
    modules = {
        "module2": {
            "plugin": "backend.plugins.mechanisms.module2.Module2Plugin",
            "depends_on": ["module1"],
            "config": {
                "module_name": "MODULE2",
                "identifying_keyword": "module2",
                "depends_on_module": "module1",
                "diag_pattern": (
                    r"Slot=(?P<Slot>\d+),CPU-Id=(?P<CPU_Id>\d*),"
                    r"ProcessName=(?P<ProcessName>\w+),Context=(?P<Context>.*)"
                ),
            },
        },
        "module1": module1,
    }

    plugins = instantiate_mechanism_plugins(modules)

    assert [plugin.module_key for plugin in plugins] == ["module1", "module2"]
    assert plugins[1].descriptor.dependencies == ("module1",)


def test_mechanism_context_injects_dependency_result(sample_config):
    module1 = sample_config["mechanism_modules"]["module1"]
    modules = {
        "module1": module1,
        "module2": {
            "plugin": "backend.plugins.mechanisms.module2.Module2Plugin",
            "depends_on": ["module1"],
            "config": {
                "module_name": "MODULE2",
                "identifying_keyword": "module2",
                "depends_on_module": "module1",
                "diag_pattern": (
                    r"Slot=(?P<Slot>\d+),CPU-Id=(?P<CPU_Id>\d*),"
                    r"ProcessName=(?P<ProcessName>\w+),Context=(?P<Context>.*)"
                ),
            },
        },
    }
    module2 = instantiate_mechanism_plugins(modules)[1]
    state = ParseResult()

    outcome = module2.execute(
        MechanismContext(
            extension_input=CurrentMechanismInput.from_collections(
                state.diagnostic_slots,
                state.private_slots,
            ),
            dependency_results={"module1": MechResult(module_key="module1")},
            scan_batch=DiagnosticScanBatch(entries_by_module={"module2": ()}),
        )
    )

    assert outcome.result is None
    assert not any("result not found" in error for error in state.errors)
