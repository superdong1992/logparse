"""Product-owned discovery configuration validation."""

from __future__ import annotations

import re
from typing import Any

from backend.utils import glob_to_regex


def validate_discovery_config(
    product_name: str,
    cfg: dict[str, Any],
) -> list[str]:
    path = f"products.{product_name}.discovery.config"
    errors: list[str] = []
    pattern_fields = (
        "diag_file_patterns",
        "private_dir_patterns",
        "journal_file_patterns",
        "syslog_file_patterns",
    )
    for field in ("slot_dir_pattern",):
        value = cfg.get(field)
        if value:
            try:
                glob_to_regex(value)
            except Exception:
                errors.append(f"{path}.{field}: glob 无效 - {value}")
    for field in pattern_fields:
        values = cfg.get(field, [])
        if not isinstance(values, list):
            errors.append(f"{path}.{field} must be a list")
            continue
        for value in values:
            try:
                glob_to_regex(value)
            except Exception:
                errors.append(f"{path}.{field}: glob 无效 - {value}")

    loose_cfg = cfg.get("loose_diagnostics")
    if loose_cfg is not None:
        if not isinstance(loose_cfg, dict):
            errors.append(f"{path}.loose_diagnostics must be an object")
        else:
            enabled = loose_cfg.get("enabled")
            if enabled is not None and not isinstance(enabled, bool):
                errors.append(f"{path}.loose_diagnostics.enabled must be a boolean")
            file_patterns = loose_cfg.get("file_patterns", [])
            if not isinstance(file_patterns, list):
                errors.append(f"{path}.loose_diagnostics.file_patterns must be a list")
            else:
                for value in file_patterns:
                    try:
                        glob_to_regex(value)
                    except Exception:
                        errors.append(
                            f"{path}.loose_diagnostics.file_patterns: glob 无效 - {value}"
                        )

    filename_pattern = cfg.get("filename_timestamp_regex")
    if filename_pattern:
        try:
            re.compile(filename_pattern)
        except re.error as exc:
            errors.append(
                f"{path}.filename_timestamp_regex: 正则无效 - {exc}"
            )
    return errors
