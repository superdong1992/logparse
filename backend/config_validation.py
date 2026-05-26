"""配置校验：在解析前检测配置错误，避免静默失败。"""

from __future__ import annotations

import importlib
import re
from typing import Any

from backend.plugins.base import DirectoryDiscoveryPlugin, LogParserPlugin


class ConfigValidationError(ValueError):
    pass


# ── 顶层入口 ──────────────────────────────────────────


def validate_config(config: dict[str, Any]) -> list[str]:
    """完整配置预飞检查，返回错误列表（空表示通过）。"""
    errors: list[str] = []

    products = config.get("products")
    if not isinstance(products, dict) or not products:
        errors.append("products 必须是非空对象")
        return errors

    for product_name, product_cfg in products.items():
        errors.extend(_validate_product_config(product_name, product_cfg))

    return errors


# ── 产品级校验 ──────────────────────────────────────────


def _validate_product_config(product_name: str, product_cfg: Any) -> list[str]:
    errors: list[str] = []

    if not isinstance(product_cfg, dict):
        return [f"products.{product_name} 必须是对象"]

    discovery = product_cfg.get("discovery")
    log_parser = product_cfg.get("log_parser")

    errors.extend(
        _validate_plugin_section(product_name, "discovery", discovery)
    )
    errors.extend(
        _validate_plugin_section(product_name, "log_parser", log_parser)
    )

    if isinstance(discovery, dict):
        plugin_path = discovery.get("plugin")
        cfg = discovery.get("config", {})
        if isinstance(plugin_path, str) and isinstance(cfg, dict):
            errors.extend(
                _validate_discovery_config(product_name, plugin_path, cfg)
            )

    if isinstance(log_parser, dict):
        plugin_path = log_parser.get("plugin")
        cfg = log_parser.get("config", {})
        if isinstance(plugin_path, str) and isinstance(cfg, dict):
            errors.extend(
                _validate_log_parser_config(product_name, cfg)
            )

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

    errors.extend(
        _validate_plugin_loadable(product_name, section_name, plugin_path)
    )

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
    elif kind == "log_parser":
        expected_methods = ["parse", "write_output"]
        expected_base = LogParserPlugin
    else:
        return [f"products.{product_name}.{kind}: 未知插件类型"]

    try:
        module_path, class_name = plugin_path.rsplit(".", 1)
    except ValueError:
        return [f"products.{product_name}.{kind}.plugin={plugin_path!r} 格式无效（需要 module.Class）"]

    try:
        module = importlib.import_module(module_path)
    except Exception as e:
        return [
            f"products.{product_name}.{kind}.plugin={plugin_path!r} "
            f"无法导入模块 {module_path}: {type(e).__name__}: {e}"
        ]

    cls = getattr(module, class_name, None)
    if cls is None:
        return [
            f"products.{product_name}.{kind}.plugin={plugin_path!r} "
            f"模块 {module_path} 缺少类 {class_name}"
        ]

    errors: list[str] = []

    try:
        is_subclass = issubclass(cls, expected_base)
    except TypeError:
        is_subclass = False

    if not is_subclass:
        errors.append(
            f"products.{product_name}.{kind}.plugin={plugin_path!r} "
            f"不是 {expected_base.__name__} 的子类"
        )

    for method in expected_methods:
        if not callable(getattr(cls, method, None)):
            errors.append(
                f"products.{product_name}.{kind}.plugin={plugin_path!r} "
                f"缺少方法: {method}"
            )

    return errors


# ── discovery config 校验 ──────────────────────────────────


def _validate_discovery_config(
    product_name: str,
    plugin_path: str,
    cfg: dict[str, Any],
) -> list[str]:
    """根据插件类型校验 discovery config 必需字段。"""
    errors: list[str] = []

    # glob 类字段校验
    for field in ("slot_dir_pattern",):
        val = cfg.get(field)
        if val:
            try:
                from backend.utils import glob_to_regex
                glob_to_regex(val)
            except Exception:
                errors.append(
                    f"products.{product_name}.discovery.config.{field}: glob 无效 - {val}"
                )

    for p in cfg.get("diag_file_patterns", []):
        try:
            from backend.utils import glob_to_regex
            glob_to_regex(p)
        except Exception:
            errors.append(
                f"products.{product_name}.discovery.config.diag_file_patterns: glob 无效 - {p}"
            )

    # timestamp_regex 校验
    ts_re = cfg.get("filename_timestamp_regex")
    if ts_re:
        try:
            re.compile(ts_re)
        except re.error as e:
            errors.append(
                f"products.{product_name}.discovery.config.filename_timestamp_regex: 正则无效 - {e}"
            )

    return errors


# ── log_parser config 校验 ──────────────────────────────────


def _validate_log_parser_config(
    product_name: str,
    cfg: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    path = f"products.{product_name}.log_parser.config"

    ts_re = cfg.get("timestamp_regex")
    if not ts_re:
        errors.append(f"{path} 缺少字段: timestamp_regex")
    else:
        try:
            re.compile(ts_re)
        except re.error as e:
            errors.append(f"{path}.timestamp_regex: 正则无效 - {e}")

    modules = cfg.get("mechanism_modules")
    if modules is not None:
        if not isinstance(modules, dict):
            errors.append(f"{path}.mechanism_modules 必须是对象")
        else:
            for module_key, module_cfg in modules.items():
                errors.extend(
                    validate_mechanism_module_config(module_key, module_cfg)
                )

    return errors


# ── 机制模块配置校验 ──────────────────────────────────


def validate_mechanism_module_config(module_key: str, cfg: dict[str, Any]) -> list[str]:
    """校验单个机制模块配置，返回错误列表（空表示通过）。"""
    errors: list[str] = []

    module_name = cfg.get("module_name")
    if not module_name:
        errors.append(f"mechanism_modules.{module_key}.module_name 不能为空")

    diag_pattern = cfg.get("diag_pattern")
    if diag_pattern:
        try:
            diag_re = re.compile(diag_pattern)
        except re.error as e:
            errors.append(f"mechanism_modules.{module_key}.diag_pattern 正则非法: {e}")
        else:
            required = {"Slot", "CPU_Id", "ProcessName", "Context"}
            missing = required - set(diag_re.groupindex)
            if missing:
                errors.append(
                    f"mechanism_modules.{module_key}.diag_pattern 缺少命名组: {sorted(missing)}"
                )

    journal_cfg = cfg.get("journal", {})
    for field in ("line_pattern", "line_pattern2"):
        pattern = journal_cfg.get(field)
        if pattern:
            try:
                compiled = re.compile(pattern)
            except re.error as e:
                errors.append(
                    f"mechanism_modules.{module_key}.journal.{field} 正则非法: {e}"
                )
                continue

            if compiled.groups < 4:
                errors.append(
                    f"mechanism_modules.{module_key}.journal.{field} 至少需要 4 个捕获组: "
                    "process_name, pid, sequence, context"
                )

    seq_pattern = cfg.get("sequence_pattern")
    if seq_pattern:
        try:
            re.compile(seq_pattern)
        except re.error as e:
            errors.append(
                f"mechanism_modules.{module_key}.sequence_pattern 正则非法: {e}"
            )

    whitelist = cfg.get("board_restart_whitelist", [])
    name_map = cfg.get("process_name_mapping", {})
    conflict = {w.lower() for w in whitelist} & {k.lower() for k in name_map}
    if conflict:
        errors.append(
            f"mechanism_modules.{module_key}: board_restart_whitelist "
            f"不能同时出现在 process_name_mapping 中: {sorted(conflict)}"
        )

    return errors
