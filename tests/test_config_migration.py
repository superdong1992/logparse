from __future__ import annotations

from copy import deepcopy

import pytest

from backend.config_migration import (
    ConfigMigrationError,
    migrate_config,
    normalize_config_for_runtime,
    v2_product_runtime_config,
)


def _v1_config():
    return {
        "pipeline": {
            "recursive_extraction": True,
            "inner_extraction": True,
            "generate_metadata": True,
            "output_base_dir": "./output",
            "result_json_mode": "compact",
            "cleanup_extracted": False,
            "debug_expand_gz": False,
        },
        "products": {
            "default": {
                "discovery": {
                    "plugin": "example.Discovery",
                    "config": {"compressed_extensions": [".zip"]},
                },
                "log_parser": {
                    "plugin": "example.Parser",
                    "config": {
                        "timestamp_regex": "timestamp",
                        "active_period_gap_threshold": 300,
                        "mechanism_modules": {
                            "module2": {
                                "plugin": "example.Module2",
                                "config": {
                                    "module_name": "MODULE2",
                                    "depends_on_module": "module1",
                                },
                            },
                            "module1": {
                                "plugin": "example.Module1",
                                "config": {"module_name": "MODULE1"},
                            },
                        },
                    },
                },
            }
        },
    }


def test_migration_is_non_mutating_and_deterministic():
    source = _v1_config()
    original = deepcopy(source)

    first = migrate_config(source)
    second = migrate_config(source)

    assert source == original
    assert first.config == second.config
    assert first.notices == second.notices
    assert first.config["schema_version"] == 2


def test_migration_separates_parser_mechanisms_and_archive():
    migrated = migrate_config(_v1_config()).config
    product = migrated["products"]["default"]

    assert product["archive"] == {
        "recursive_extraction": True,
        "compressed_extensions": [".zip"],
    }
    assert "mechanism_modules" not in product["parser"]["config"]
    assert product["parser"]["config"]["active_period_gap_seconds"] == 300
    assert product["mechanisms"]["module2"]["depends_on"] == ["module1"]
    assert "depends_on_module" not in product["mechanisms"]["module2"]["config"]


def test_runtime_projection_preserves_legacy_plugin_shape():
    migrated = migrate_config(_v1_config()).config

    runtime = normalize_config_for_runtime(migrated)
    product = v2_product_runtime_config(migrated, "default")

    assert runtime["pipeline"]["recursive_extraction"] is True
    assert runtime["pipeline"]["cleanup_extracted"] is False
    assert runtime["compressed_extensions"] == [".zip"]
    parser_config = product["log_parser"]["config"]
    assert parser_config["active_period_gap_threshold"] == 300
    assert parser_config["mechanism_modules"]["module2"]["config"][
        "depends_on_module"
    ] == "module1"
    assert parser_config["mechanism_modules"]["module2"]["depends_on"] == [
        "module1"
    ]


def test_v2_migration_is_noop_copy():
    source = {"schema_version": 2, "pipeline": {}, "products": {}}

    migrated = migrate_config(source)

    assert migrated.config == source
    assert migrated.config is not source
    assert migrated.notices == ()


def test_unsupported_schema_is_rejected():
    with pytest.raises(ConfigMigrationError):
        migrate_config({"schema_version": 99})
