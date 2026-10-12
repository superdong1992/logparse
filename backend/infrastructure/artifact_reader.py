"""Read versioned JSON artifacts with deterministic, content-free errors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ArtifactReadError(ValueError):
    """An artifact could not be read; messages never include its contents."""

    def __init__(self, path: Path, reason: str) -> None:
        self.artifact = path.name
        self.reason = reason
        messages = {
            "missing": "不存在。",
            "unreadable": "无法读取。",
            "invalid_json": "不是有效的 UTF-8 JSON 文件。",
            "invalid_root": "的 JSON 根节点必须是对象。",
            "invalid_shape": "的字段结构或类型无效。",
            "missing_schema": "缺少 schema_version。",
            "unsupported_schema": "的 schema_version 不受支持。",
        }
        super().__init__(f"{self.artifact} {messages[reason]}")


class ArtifactSchemaError(ArtifactReadError):
    """The artifact has no explicit supported integer schema version."""

    def __init__(
        self, path: Path, actual: Any, expected: int, *, missing: bool = False,
    ) -> None:
        self.actual = actual
        self.expected = expected
        super().__init__(
            path,
            "missing_schema" if missing else "unsupported_schema",
        )
        self.args = (f"{self.args[0]} 期望版本为 {expected}。",)


def read_json_artifact(path: Path, *, expected_schema: int) -> dict[str, Any]:
    """Require an object and an explicit version; preserve failure categories."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ArtifactReadError(path, "missing") from exc
    except OSError as exc:
        raise ArtifactReadError(path, "unreadable") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactReadError(path, "invalid_json") from exc
    if not isinstance(payload, dict):
        raise ArtifactReadError(path, "invalid_root")
    if "schema_version" not in payload:
        raise ArtifactSchemaError(path, None, expected_schema, missing=True)
    actual_schema = payload.get("schema_version")
    if type(actual_schema) is not int or actual_schema != expected_schema:
        raise ArtifactSchemaError(path, actual_schema, expected_schema)
    return payload
