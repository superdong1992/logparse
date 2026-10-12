"""配置校验：在解析前检测配置错误，避免静默失败。"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

from backend.application.plugin_graph import PluginGraphError, resolve_mechanism_order
from backend.performance import resolve_worker_count
from backend.plugins.base import DirectoryDiscoveryPlugin, LogParserPlugin
from backend.extensions.mechanisms.base import MechanismPlugin


# ── 顶层入口 ──────────────────────────────────────────


def validate_config(config: dict[str, Any]) -> list[str]:
    """完整配置预飞检查，返回错误列表（空表示通过）。"""
    if not isinstance(config, dict):
        return ["configuration root must be an object"]
    version = config.get("schema_version")
    if type(version) is not int or version != 2:
        return ["配置必须显式声明 schema_version: 2，旧版本配置已不再支持"]
    return _validate_v2_config(config)


def _validate_v2_config(config: dict[str, Any]) -> list[str]:
    errors = _unknown_fields("configuration", config, {"schema_version", "pipeline", "products"})
    errors.extend(_validate_pipeline_config(config.get("pipeline", {})))

    products = config.get("products")
    if not isinstance(products, dict) or not products:
        errors.append("products must be a non-empty object")
        return errors
    for product_name, product_cfg in products.items():
        errors.extend(_validate_v2_product_config(str(product_name), product_cfg))
    return errors


def _validate_v2_product_config(product_name: str, raw: Any) -> list[str]:
    path = f"products.{product_name}"
    if not isinstance(raw, dict):
        return [f"{path} must be an object"]
    if "$include" in raw:
        if set(raw) != {"$include"}:
            return [f"{path} $include cannot be combined with inline fields"]
        include_value = raw.get("$include")
        if not isinstance(include_value, str) or not include_value.strip():
            return [f"{path}.$include must be a non-empty relative path"]
        if Path(include_value).is_absolute():
            return [f"{path}.$include must be a relative path"]
        return []

    errors = _unknown_fields(path, raw, {"archive", "discovery", "parser", "mechanisms"})
    archive = raw.get("archive")
    if not isinstance(archive, dict):
        errors.append(f"{path}.archive must be an object")
    else:
        errors.extend(_validate_archive_config(f"{path}.archive", archive))

    discovery = raw.get("discovery")
    parser = raw.get("parser")
    errors.extend(_validate_plugin_section(product_name, "discovery", discovery))
    errors.extend(_validate_plugin_section(product_name, "parser", parser))

    if isinstance(discovery, dict):
        plugin_path = discovery.get("plugin")
        cfg = discovery.get("config", {})
        if isinstance(plugin_path, str) and isinstance(cfg, dict):
            errors.extend(_validate_discovery_config(product_name, plugin_path, cfg))

    if isinstance(parser, dict):
        cfg = parser.get("config", {})
        if isinstance(cfg, dict):
            errors.extend(_validate_v2_parser_config(product_name, cfg))

    mechanisms = raw.get("mechanisms")
    if not isinstance(mechanisms, dict):
        errors.append(f"{path}.mechanisms must be an object")
    else:
        for module_key, module_cfg in mechanisms.items():
            errors.extend(
                _validate_mechanism_plugin_config(
                    str(module_key),
                    module_cfg,
                    path_prefix=f"{path}.mechanisms",
                )
            )
        errors.extend(_validate_plugin_graph(mechanisms, f"{path}.mechanisms"))
    return errors


def _validate_archive_config(path: str, raw: dict[str, Any]) -> list[str]:
    errors = _unknown_fields(path, raw, {"recursive_extraction", "compressed_extensions"})
    recursive = raw.get("recursive_extraction")
    if not isinstance(recursive, bool):
        errors.append(f"{path}.recursive_extraction must be a boolean")
    extensions = raw.get("compressed_extensions")
    if not isinstance(extensions, list) or any(
        not isinstance(value, str) or not value for value in extensions
    ):
        errors.append(f"{path}.compressed_extensions must be a string list")
    return errors


def _validate_v2_parser_config(product_name: str, cfg: dict[str, Any]) -> list[str]:
    path = f"products.{product_name}.parser.config"
    errors: list[str] = []
    if "mechanism_modules" in cfg:
        errors.append(
            f"{path}.mechanism_modules is not supported in schema v2; "
            f"use products.{product_name}.mechanisms"
        )
    ts_re = cfg.get("timestamp_regex")
    if not ts_re:
        errors.append(f"{path} missing field: timestamp_regex")
    else:
        try:
            re.compile(ts_re)
        except (re.error, TypeError) as exc:
            errors.append(f"{path}.timestamp_regex: invalid regex - {exc}")
    gap = cfg.get("active_period_gap_seconds")
    if gap is not None and (isinstance(gap, bool) or not isinstance(gap, (int, float)) or gap <= 0):
        errors.append(f"{path}.active_period_gap_seconds must be positive")
    if "active_period_gap_threshold" in cfg:
        errors.append(
            f"{path}.active_period_gap_threshold was renamed to active_period_gap_seconds"
        )
    return errors


def _unknown_fields(path: str, raw: dict[str, Any], allowed: set[str]) -> list[str]:
    unknown = sorted(set(raw) - allowed)
    return [f"{path} has unknown fields: {unknown}"] if unknown else []


def _validate_pipeline_config(raw: Any) -> list[str]:
    if raw is None:
        return ["pipeline must be an object"]
    if not isinstance(raw, dict):
        return ["pipeline must be an object"]

    errors: list[str] = []
    errors.extend(
        _unknown_fields(
            "pipeline",
            raw,
            {
                "debug_expand_gz",
                "extraction_workers",
                "diagnostic_scan_workers",
                "keep_workspace",
            },
        )
    )
    if "keep_workspace" in raw and not isinstance(raw["keep_workspace"], bool):
        errors.append("pipeline.keep_workspace must be a boolean")
    if "debug_expand_gz" in raw and not isinstance(raw["debug_expand_gz"], bool):
        errors.append("pipeline.debug_expand_gz must be a boolean")

    for field in ("extraction_workers", "diagnostic_scan_workers"):
        if field not in raw:
            continue
        try:
            resolve_worker_count(raw[field], default_cap=4)
        except ValueError:
            errors.append(f"pipeline.{field} must be 'auto' or a positive integer")

    return errors


# ── 插件 section 校验 ──────────────────────────────────


def _validate_plugin_section(
    product_name: str,
    section_name: str,
    section: Any,
) -> list[str]:
    errors: list[str] = []
    path = f"products.{product_name}.{section_name}"

    if not isinstance(section, dict):
        return [f"{path} 必须是对象"]

    plugin_path = section.get("plugin")
    if not isinstance(plugin_path, str) or not plugin_path.strip():
        errors.append(f"{path}.plugin 必须是非空字符串")
        return errors

    cfg = section.get("config", {})
    if not isinstance(cfg, dict):
        errors.append(f"{path}.config 必须是对象")

    errors.extend(_validate_plugin_loadable(product_name, section_name, plugin_path))

    return errors


def _validate_plugin_loadable(
    product_name: str,
    kind: str,
    plugin_path: str,
) -> list[str]:
    """校验插件类路径可导入、类存在、继承正确基类、具备期望方法。"""
    if kind == "discovery":
        expected_methods = ["discover"]
        expected_base = DirectoryDiscoveryPlugin
    elif kind == "parser":
        expected_methods = ["parse", "write_output"]
        expected_base = LogParserPlugin
    else:
        return [f"products.{product_name}.{kind}: 未知插件类型"]

    return _validate_plugin_loadable_for_base(
        path=f"products.{product_name}.{kind}",
        plugin_path=plugin_path,
        expected_base=expected_base,
        expected_methods=expected_methods,
    )


# ── discovery config 校验 ──────────────────────────────────


def _validate_plugin_loadable_for_base(
    path: str,
    plugin_path: str,
    expected_base: type,
    expected_methods: list[str],
) -> list[str]:
    """Validate a plugin class can be imported and matches the expected base."""
    try:
        module_path, class_name = plugin_path.rsplit(".", 1)
    except ValueError:
        return [f"{path}.plugin={plugin_path!r} 格式无效（需要 module.Class）"]

    try:
        module = importlib.import_module(module_path)
    except Exception as e:
        return [
            f"{path}.plugin={plugin_path!r} 无法导入模块 {module_path}: {type(e).__name__}: {e}"
        ]

    cls = getattr(module, class_name, None)
    if cls is None:
        return [f"{path}.plugin={plugin_path!r} 模块 {module_path} 缺少类 {class_name}"]

    errors: list[str] = []

    try:
        is_subclass = issubclass(cls, expected_base)
    except TypeError:
        is_subclass = False

    if not is_subclass:
        errors.append(f"{path}.plugin={plugin_path!r} 不是 {expected_base.__name__} 的子类")

    for method in expected_methods:
        if not callable(getattr(cls, method, None)):
            errors.append(f"{path}.plugin={plugin_path!r} 缺少方法: {method}")

    return errors


def _validate_discovery_config(
    product_name: str,
    plugin_path: str,
    cfg: dict[str, Any],
) -> list[str]:
    """Delegate product fields to the selected discovery extension."""

    try:
        module_path, class_name = plugin_path.rsplit(".", 1)
        cls = getattr(importlib.import_module(module_path), class_name)
        validator = getattr(cls, "validate_config", None)
        return list(validator(product_name, cfg)) if callable(validator) else []
    except Exception as exc:
        return [
            f"products.{product_name}.discovery.plugin={plugin_path!r} "
            f"配置校验失败: {type(exc).__name__}: {exc}"
        ]


# ── 机制模块配置校验 ──────────────────────────────────


def _validate_mechanism_plugin_config(
    module_key: str,
    module_cfg: Any,
    *,
    path_prefix: str,
) -> list[str]:
    path = f"{path_prefix}.{module_key}"
    if not isinstance(module_cfg, dict):
        return [f"{path} 必须是对象"]

    errors: list[str] = []
    enabled = module_cfg.get("enabled", True)
    if not isinstance(enabled, bool):
        errors.append(f"{path}.enabled must be a boolean")
        return errors

    if enabled is False:
        return []

    dependencies = module_cfg.get("depends_on", [])
    errors.extend(
        _unknown_fields(
            path,
            module_cfg,
            {"plugin", "enabled", "depends_on", "config"},
        )
    )
    if not isinstance(dependencies, list) or any(
        not isinstance(value, str) or not value.strip() for value in dependencies
    ):
        errors.append(f"{path}.depends_on must be a list of non-empty strings")

    plugin_path = module_cfg.get("plugin")
    if not isinstance(plugin_path, str) or not plugin_path.strip():
        errors.append(f"{path}.plugin 必须是非空字符串")
        return errors

    cfg = module_cfg.get("config", {})
    if not isinstance(cfg, dict):
        errors.append(f"{path}.config 必须是对象")
        return errors
    if "depends_on_module" in cfg:
        errors.append(
            f"{path}.config.depends_on_module is not supported in schema v2; use {path}.depends_on"
        )

    load_errors = _validate_plugin_loadable_for_base(
        path=path,
        plugin_path=plugin_path,
        expected_base=MechanismPlugin,
        expected_methods=["execute"],
    )
    errors.extend(load_errors)
    if load_errors:
        return errors

    try:
        module_path, class_name = plugin_path.rsplit(".", 1)
        cls = getattr(importlib.import_module(module_path), class_name)
        validator = getattr(cls, "validate_config", None)
        if callable(validator):
            errors.extend(validator(module_key, dict(cfg)))
    except Exception as e:
        errors.append(f"{path}.plugin={plugin_path!r} 配置校验失败: {type(e).__name__}: {e}")

    return errors


def _validate_plugin_graph(modules: dict[str, Any], path: str) -> list[str]:
    try:
        resolve_mechanism_order(modules)
    except PluginGraphError as exc:
        return [f"{path}: {issue.message}" for issue in exc.issues]
    return []
