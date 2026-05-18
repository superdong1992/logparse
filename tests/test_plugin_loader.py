"""Tests for backend/plugins/loader.py."""
from __future__ import annotations

import pytest

from backend.plugins.base import DirectoryDiscoveryPlugin, LogParserPlugin
from backend.plugins.loader import instantiate_plugin


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
