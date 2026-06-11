"""配置校验：在解析前检测配置错误，避免静默失败。"""

from __future__ import annotations

import importlib
import re
from typing import Any

from backend.performance import resolve_worker_count
from backend.plugins.base import DirectoryDiscoveryPlugin, LogParserPlugin
from backend.plugins.mechanisms.base import MechanismModulePlugin


# ── 顶层入口 ──────────────────────────────────────────


def validate_config(config: dict[str, Any]) -> list[str]:
    """完整配置预飞检查，返回错误列表（空表示通过）。"""
    errors: list[str] = []
    errors.extend(_validate_pipeline_config(config.get("pipeline", {})))

    products = config.get("products")
    if not isinstance(products, dict) or not products:
        errors.append("products 必须是非空对象")
        return errors

    for product_name, product_cfg in products.items():
        errors.extend(_validate_product_config(product_name, product_cfg))

    return errors


def _validate_pipeline_config(raw: Any) -> list[str]:
    if raw is None:
        return ["pipeline must be an object"]
    if not isinstance(raw, dict):
        return ["pipeline must be an object"]

    errors: list[str] = []
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
            f"{path}.plugin={plugin_path!r} "
            f"无法导入模块 {module_path}: {type(e).__name__}: {e}"
        ]

    cls = getattr(module, class_name, None)
    if cls is None:
        return [
            f"{path}.plugin={plugin_path!r} "
            f"模块 {module_path} 缺少类 {class_name}"
        ]

    errors: list[str] = []

    try:
        is_subclass = issubclass(cls, expected_base)
    except TypeError:
        is_subclass = False

    if not is_subclass:
        errors.append(
            f"{path}.plugin={plugin_path!r} "
            f"不是 {expected_base.__name__} 的子类"
        )

    for method in expected_methods:
        if not callable(getattr(cls, method, None)):
            errors.append(
                f"{path}.plugin={plugin_path!r} "
                f"缺少方法: {method}"
            )

    return errors


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

    loose_cfg = cfg.get("loose_diagnostics")
    if loose_cfg is not None:
        if not isinstance(loose_cfg, dict):
            errors.append(
                f"products.{product_name}.discovery.config.loose_diagnostics must be an object"
            )
        else:
            enabled = loose_cfg.get("enabled")
            if enabled is not None and not isinstance(enabled, bool):
                errors.append(
                    f"products.{product_name}.discovery.config.loose_diagnostics.enabled must be a boolean"
                )

            file_patterns = loose_cfg.get("file_patterns", [])
            if not isinstance(file_patterns, list):
                errors.append(
                    f"products.{product_name}.discovery.config.loose_diagnostics.file_patterns must be a list"
                )
            else:
                for pattern in file_patterns:
                    try:
                        from backend.utils import glob_to_regex
                        glob_to_regex(pattern)
                    except Exception:
                        errors.append(
                            "products."
                            f"{product_name}.discovery.config.loose_diagnostics.file_patterns: "
                            f"glob 无效 - {pattern}"
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
                    _validate_mechanism_plugin_config(module_key, module_cfg)
                )

    return errors


# ── 机制模块配置校验 ──────────────────────────────────


def _validate_mechanism_plugin_config(module_key: str, module_cfg: Any) -> list[str]:
    path = f"mechanism_modules.{module_key}"
    if not isinstance(module_cfg, dict):
        return [f"{path} 必须是对象"]

    if module_cfg.get("enabled", True) is False:
        return []

    plugin_path = module_cfg.get("plugin")
    if not isinstance(plugin_path, str) or not plugin_path.strip():
        return [f"{path}.plugin 必须是非空字符串"]

    cfg = module_cfg.get("config", {})
    if not isinstance(cfg, dict):
        return [f"{path}.config 必须是对象"]

    errors = _validate_plugin_loadable_for_base(
        path=path,
        plugin_path=plugin_path,
        expected_base=MechanismModulePlugin,
        expected_methods=["parse"],
    )
    if errors:
        return errors

    try:
        module_path, class_name = plugin_path.rsplit(".", 1)
        cls = getattr(importlib.import_module(module_path), class_name)
        validator = getattr(cls, "validate_config", None)
        if callable(validator):
            errors.extend(validator(module_key, cfg))
    except Exception as e:
        errors.append(
            f"{path}.plugin={plugin_path!r} 配置校验失败: {type(e).__name__}: {e}"
        )

    return errors


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
    required_substrings = journal_cfg.get("line_pattern2_required_substrings")
    if required_substrings is not None:
        if not isinstance(required_substrings, list):
            errors.append(
                f"mechanism_modules.{module_key}.journal.line_pattern2_required_substrings "
                "必须是字符串列表"
            )
        else:
            for idx, value in enumerate(required_substrings):
                if not isinstance(value, str) or not value:
                    errors.append(
                        f"mechanism_modules.{module_key}.journal."
                        f"line_pattern2_required_substrings[{idx}] 必须是非空字符串"
                    )

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

            if compiled.groups not in (3, 4):
                errors.append(
                    f"mechanism_modules.{module_key}.journal.{field} 需要 3 或 4 个捕获组: "
                    "3组=process_name, pid, context；4组=process_name, pid, sequence, context"
                )
            elif compiled.groups == 3 and _looks_like_sequence_journal_pattern(
                pattern, cfg.get("sequence_pattern")
            ):
                errors.append(
                    f"mechanism_modules.{module_key}.journal.{field} 包含序号格式时需要 4 个捕获组: "
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

    for legacy_field in (
        "board_restart_" + "indicator",
        "board_restart_" + "whitelist",
        "process_name_" + "mapping",
    ):
        if legacy_field in cfg:
            errors.append(
                f"mechanism_modules.{module_key}.{legacy_field} is no longer supported; "
                "use lifecycle_split V3 fields"
            )

    errors.extend(_validate_lifecycle_split_config(module_key, cfg.get("lifecycle_split")))

    return errors


def _validate_lifecycle_split_config(module_key: str, raw: Any) -> list[str]:
    if raw is None:
        return []

    path = f"mechanism_modules.{module_key}.lifecycle_split"
    if not isinstance(raw, dict):
        return [f"{path} must be an object"]

    unsupported = sorted(
        key
        for key in raw
        if key not in {
            "process_name_mapping",
            "reliable_processes",
            "multi_instance_processes",
        }
    )
    if unsupported:
        return [
            f"{path} only supports V3 fields: process_name_mapping, "
            f"reliable_processes, multi_instance_processes; unsupported keys: {unsupported}"
        ]

    mapping = raw.get("process_name_mapping", {})
    if not isinstance(mapping, dict):
        return [f"{path}.process_name_mapping must be an object"]

    alias_to_canonical: dict[str, str] = {}
    for canonical, aliases in mapping.items():
        canonical_name = str(canonical)
        alias_to_canonical[_norm_name(canonical_name)] = canonical_name
        if aliases is None:
            continue
        if isinstance(aliases, str):
            alias_iterable = [aliases]
        else:
            try:
                alias_iterable = list(aliases)
            except TypeError:
                return [f"{path}.process_name_mapping.{canonical_name} must be a list"]
        for alias in alias_iterable:
            alias_to_canonical[_norm_name(str(alias))] = canonical_name

    reliable_raw = raw.get("reliable_processes", [])
    multi_raw = raw.get("multi_instance_processes", [])
    list_errors = [
        error for error in (
            _validate_name_list(f"{path}.reliable_processes", reliable_raw),
            _validate_name_list(f"{path}.multi_instance_processes", multi_raw),
        )
        if error
    ]
    if list_errors:
        return list_errors

    reliable = _canonical_name_set(reliable_raw or [], alias_to_canonical)
    multi = _canonical_name_set(multi_raw or [], alias_to_canonical)
    conflicts = reliable & multi
    if not conflicts:
        return []

    return [
        f"{path} config conflict: each canonical process may appear in only one of "
        "reliable_processes, multi_instance_processes; "
        f"conflicts={sorted(conflicts)}"
    ]


def _validate_name_list(path: str, raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        return None
    return f"{path} must be a list"


def _canonical_name_set(raw: Any, alias_to_canonical: dict[str, str]) -> set[str]:
    if raw is None:
        return set()
    names = list(raw)
    return {
        _norm_name(alias_to_canonical.get(_norm_name(str(name)), str(name)))
        for name in names
    }


def _norm_name(value: str) -> str:
    return value.casefold()


def _looks_like_sequence_journal_pattern(pattern: str, seq_pattern: Any) -> bool:
    """Return True when a 3-group journal regex appears to contain a sequence field."""
    if "No\\[" in pattern or "No[" in pattern:
        return True
    return bool(isinstance(seq_pattern, str) and seq_pattern and seq_pattern in pattern)
