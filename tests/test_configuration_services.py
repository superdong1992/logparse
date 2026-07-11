from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.application.configuration import (
    ConfigurationError,
    explain_config,
    load_config_file,
    load_raw_config_file,
    render_migrated_config,
    runtime_config,
)
from backend.application.doctor import run_doctor


ROOT = Path(__file__).resolve().parents[1]


def _shipped_config():
    return load_config_file(ROOT / "config.yaml")


def test_explain_config_reports_stable_dependency_order():
    explanation = explain_config(_shipped_config(), product="default")

    assert explanation["schema_version"] == 2
    assert explanation["products"]["default"]["execution_order"] == [
        "module1",
        "module2",
    ]
    assert explanation["products"]["default"]["dependencies"]["module2"] == [
        "module1"
    ]


def test_render_migrated_config_is_valid_v2_yaml():
    rendered = render_migrated_config(_shipped_config())
    result = yaml.safe_load(rendered)

    assert result["schema_version"] == 2
    assert "parser" in result["products"]["default"]
    assert "log_parser" not in result["products"]["default"]


def test_runtime_config_keeps_current_pipeline_shape():
    runtime = runtime_config(_shipped_config())

    assert "log_parser" in runtime["products"]["default"]
    assert "mechanism_modules" in runtime["products"]["default"]["log_parser"]["config"]


def test_load_config_file_rejects_non_object(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("- item\n", encoding="utf-8")

    with pytest.raises(ConfigurationError):
        load_config_file(path)


def test_load_config_file_resolves_relative_product_include(tmp_path):
    product_dir = tmp_path / "configs" / "products"
    product_dir.mkdir(parents=True)
    (product_dir / "demo.yaml").write_text(
        "archive: {}\ndiscovery: {}\nparser: {}\nmechanisms: {}\n",
        encoding="utf-8",
    )
    path = tmp_path / "config.yaml"
    path.write_text(
        "schema_version: 2\nproducts:\n  demo:\n"
        "    $include: configs/products/demo.yaml\n",
        encoding="utf-8",
    )

    loaded = load_config_file(path)

    assert loaded["products"]["demo"]["mechanisms"] == {}
    assert load_raw_config_file(path)["products"]["demo"] == {
        "$include": "configs/products/demo.yaml"
    }


@pytest.mark.parametrize("include", ["../outside.yaml", "/tmp/outside.yaml"])
def test_load_config_file_rejects_unsafe_product_include(tmp_path, include):
    path = tmp_path / "config.yaml"
    path.write_text(
        "schema_version: 2\nproducts:\n  demo:\n"
        f"    $include: {include}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError):
        load_config_file(path)


def test_load_config_file_rejects_mixed_include_fields(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "schema_version: 2\nproducts:\n  demo:\n"
        "    $include: configs/products/demo.yaml\n"
        "    parser: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="cannot be combined"):
        load_config_file(path)


def test_load_config_file_rejects_missing_include(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "schema_version: 2\nproducts:\n  demo:\n"
        "    $include: configs/products/missing.yaml\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="cannot load product config"):
        load_config_file(path)


@pytest.mark.parametrize("content", ["- item\n", ": invalid\n"])
def test_load_config_file_rejects_invalid_included_document(tmp_path, content):
    product_dir = tmp_path / "configs" / "products"
    product_dir.mkdir(parents=True)
    (product_dir / "demo.yaml").write_text(content, encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(
        "schema_version: 2\nproducts:\n  demo:\n"
        "    $include: configs/products/demo.yaml\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError):
        load_config_file(path)


def test_doctor_returns_structured_config_failure():
    report = run_doctor({"schema_version": 2, "products": {}})

    config_check = next(check for check in report.checks if check.name == "config")
    assert config_check.ok is False
    assert report.ok is False
