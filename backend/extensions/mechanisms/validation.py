"""Current-product mechanism and lifecycle configuration rules.

These rules mention current topology fields and therefore belong to the LAN
business-policy zone rather than the product-neutral configuration core.
"""

from __future__ import annotations

import re
from typing import Any


def validate_mechanism_module_config(
    module_key: str,
    cfg: dict[str, Any],
) -> list[str]:
    """Validate current-product Module1 configuration."""

    errors: list[str] = []
    module_name = cfg.get("module_name")
    if not module_name:
        errors.append(f"mechanism_modules.{module_key}.module_name 不能为空")

    diag_pattern = cfg.get("diag_pattern")
    if diag_pattern:
        try:
            diag_re = re.compile(diag_pattern)
        except re.error as exc:
            errors.append(
                f"mechanism_modules.{module_key}.diag_pattern 正则非法: {exc}"
            )
        else:
            required = {"Slot", "CPU_Id", "ProcessName", "Context"}
            missing = required - set(diag_re.groupindex)
            if missing:
                errors.append(
                    f"mechanism_modules.{module_key}.diag_pattern "
                    f"缺少命名组: {sorted(missing)}"
                )

    journal_cfg = cfg.get("journal", {})
    required_substrings = journal_cfg.get("line_pattern2_required_substrings")
    if required_substrings is not None:
        if not isinstance(required_substrings, list):
            errors.append(
                f"mechanism_modules.{module_key}.journal."
                "line_pattern2_required_substrings 必须是字符串列表"
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
        if not pattern:
            continue
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            errors.append(
                f"mechanism_modules.{module_key}.journal.{field} 正则非法: {exc}"
            )
            continue
        if compiled.groups not in (3, 4):
            errors.append(
                f"mechanism_modules.{module_key}.journal.{field} 需要 3 或 4 个捕获组: "
                "3组=process_name, pid, context；"
                "4组=process_name, pid, sequence, context"
            )
        elif compiled.groups == 3 and _looks_like_sequence_journal_pattern(
            pattern, cfg.get("sequence_pattern")
        ):
            errors.append(
                f"mechanism_modules.{module_key}.journal.{field} "
                "包含序号格式时需要 4 个捕获组: "
                "process_name, pid, sequence, context"
            )

    seq_pattern = cfg.get("sequence_pattern")
    if seq_pattern:
        try:
            re.compile(seq_pattern)
        except re.error as exc:
            errors.append(
                f"mechanism_modules.{module_key}.sequence_pattern 正则非法: {exc}"
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
            f"reliable_processes, multi_instance_processes; "
            f"unsupported keys: {unsupported}"
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
        error
        for error in (
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
    if raw is None or isinstance(raw, list):
        return None
    return f"{path} must be a list"


def _canonical_name_set(raw: Any, alias_to_canonical: dict[str, str]) -> set[str]:
    if raw is None:
        return set()
    return {
        _norm_name(alias_to_canonical.get(_norm_name(str(name)), str(name)))
        for name in list(raw)
    }


def _norm_name(value: str) -> str:
    return value.casefold()


def _looks_like_sequence_journal_pattern(pattern: str, seq_pattern: Any) -> bool:
    if "No\\[" in pattern or "No[" in pattern:
        return True
    return bool(isinstance(seq_pattern, str) and seq_pattern and seq_pattern in pattern)
