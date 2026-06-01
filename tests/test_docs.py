from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_documents_lifecycle_split_entrypoints():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "lifecycle_split" in readme
    assert "enabled: true" in readme
    assert "config.lifecycle-v2.yaml" in readme
    assert "lifecycle_split_result" in readme
    assert "interval_v3" in readme
    assert "--lifecycle-dfx decisions" in readme


def test_usage_documents_lifecycle_split_defaults_and_output():
    usage = (ROOT / "docs" / "usage.md").read_text(encoding="utf-8")

    assert "lifecycle_split" in usage
    assert "默认关闭" in usage
    assert "config.lifecycle-v2.yaml" in usage
    assert "lifecycle_split_result" in usage
    assert "interval_v3" in usage
    assert "--lifecycle-dfx full" in usage
    assert "mech-lifecycles" in usage
