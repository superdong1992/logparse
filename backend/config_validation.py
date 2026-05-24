"""配置校验：在解析前检测机制模块配置错误，避免静默失败。"""

from __future__ import annotations

import re
from typing import Any


class ConfigValidationError(ValueError):
    pass


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
