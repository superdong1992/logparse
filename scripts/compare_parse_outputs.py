#!/usr/bin/env python3
"""Compare parse business outputs while ignoring profiling and raw extraction."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


IGNORED_FILE_NAMES = {"performance.json"}
BUSINESS_JSON_FILE_NAMES = {"metadata.json", "result.json"}
BUSINESS_PATH_PARTS = {"mech_modules"}
VOLATILE_JSON_KEYS = {"created_at"}


def compare_outputs(before: Path, after: Path) -> dict[str, Any]:
    before = Path(before)
    after = Path(after)
    differences: list[str] = []
    if not before.exists():
        differences.append("before root does not exist")
    if not after.exists():
        differences.append("after root does not exist")
    if differences:
        return {"ok": False, "differences": differences}

    before_files = _business_files(before)
    after_files = _business_files(after)

    if not before_files and not after_files:
        return {
            "ok": False,
            "differences": ["no comparable business files found"],
        }

    for rel in sorted(before_files - after_files):
        differences.append(f"{rel}: missing from after")
    for rel in sorted(after_files - before_files):
        differences.append(f"{rel}: missing from before")
    for rel in sorted(before_files & after_files):
        before_hash = _content_hash(before / rel, before)
        after_hash = _content_hash(after / rel, after)
        if before_hash != after_hash:
            differences.append(f"{rel}: content differs")

    return {"ok": not differences, "differences": differences}


def _business_files(root: Path) -> set[Path]:
    files: set[Path] = set()
    if not root.exists():
        return files
    root_is_task_dir = _looks_like_task_dir(root)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if path.name in IGNORED_FILE_NAMES:
            continue
        if _is_extracted_workspace(rel, root_is_task_dir):
            continue
        if not _is_business_file(rel):
            continue
        files.add(rel)
    return files


def _looks_like_task_dir(root: Path) -> bool:
    return any((root / name).exists() for name in ("result.json", "metadata.json", "mech_modules"))


def _is_extracted_workspace(rel: Path, root_is_task_dir: bool) -> bool:
    parts = rel.parts
    if root_is_task_dir:
        return len(parts) >= 2 and parts[0] == "extracted"
    return len(parts) >= 3 and parts[1] == "extracted"


def _is_business_file(rel: Path) -> bool:
    return rel.name in BUSINESS_JSON_FILE_NAMES or any(
        part in BUSINESS_PATH_PARTS for part in rel.parts
    )


def _content_hash(path: Path, root: Path) -> str:
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _sha256(path)
        normalized = _normalize_json(payload, root)
        data = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(data).hexdigest()
    return _sha256(path)


def _normalize_json(value: Any, root: Path) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_json(item, root)
            for key, item in value.items()
            if key not in VOLATILE_JSON_KEYS
        }
    if isinstance(value, list):
        return [_normalize_json(item, root) for item in value]
    if isinstance(value, str):
        return _normalize_output_root(value, root)
    return value


def _normalize_output_root(value: str, root: Path) -> str:
    candidates = {str(root), str(root.resolve())}
    for candidate in list(candidates):
        candidates.add(candidate.replace("/", "\\"))
        candidates.add(candidate.replace("\\", "/"))
    normalized = value
    for candidate in sorted(candidates, key=len, reverse=True):
        if candidate:
            normalized = normalized.replace(candidate, "<OUTPUT>")
    return normalized


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    args = parser.parse_args()

    result = compare_outputs(args.before, args.after)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
