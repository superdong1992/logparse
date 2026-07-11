from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
GENERIC_ROOTS = (
    ROOT / "backend" / "contracts",
    ROOT / "backend" / "ports",
    ROOT / "backend" / "application",
    ROOT / "backend" / "infrastructure",
)
FORBIDDEN_IMPORTS = (
    "backend.models",
    "backend.extensions",
)
FORBIDDEN_PRODUCT_TOKENS = re.compile(
    r"(?:\bCPU\b|\bmodule1\b|\bmodule2\b|slot_id|slot_|cpu_id|cpu_)",
    re.IGNORECASE,
)


def _python_files() -> list[Path]:
    return [
        path
        for root in GENERIC_ROOTS
        for path in sorted(root.rglob("*.py"))
    ]


def test_generic_architecture_does_not_import_product_models_or_extensions() -> None:
    violations: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith(FORBIDDEN_IMPORTS):
                    violations.append(f"{path.relative_to(ROOT)} imports {name}")
    assert violations == []


def test_generic_architecture_contains_no_current_product_vocabulary() -> None:
    violations: list[str] = []
    for path in _python_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if FORBIDDEN_PRODUCT_TOKENS.search(line):
                violations.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert violations == []
