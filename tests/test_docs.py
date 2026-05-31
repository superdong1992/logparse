from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_documents_lifecycle_split_v2_entrypoints():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "lifecycle_split" in readme
    assert "enabled: true" in readme
    assert "lifecycle_split_result" in readme
    assert "--boundary-detail full" in readme


def test_usage_documents_lifecycle_split_v2_defaults_and_output():
    usage = (ROOT / "docs" / "usage.md").read_text(encoding="utf-8")

    assert "lifecycle_split" in usage
    assert "默认关闭" in usage
    assert "lifecycle_split_result" in usage
    assert "mech-lifecycles" in usage
