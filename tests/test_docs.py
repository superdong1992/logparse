from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_documents_v3_only_lifecycle_split_entrypoint():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "lifecycle_split" in readme
    assert "interval_v3" in readme
    assert ("config.lifecycle-" + "v2.yaml") not in readme
    assert "lifecycle_split_result" in readme
    assert "--lifecycle-dfx decisions" in readme


def test_usage_documents_v3_only_lifecycle_split_output():
    usage = (ROOT / "docs" / "usage.md").read_text(encoding="utf-8")

    assert "lifecycle_split" in usage
    assert "interval_v3" in usage
    assert ("config.lifecycle-" + "v2.yaml") not in usage
    assert "lifecycle_split_result" in usage
    assert "--lifecycle-dfx full" in usage
    assert "mech-lifecycles" in usage


def test_lifecycle_dfx_guide_documents_no_wrap_boundary_evidence():
    guide = (ROOT / "docs" / "lifecycle-dfx-guide.md").read_text(encoding="utf-8")

    assert "No 回绕只是否定证据" in guide
    assert "可靠进程" in guide
    assert "忽略 PID" in guide
    assert "diagnostic" in guide
    assert "journal" in guide
    assert "非可靠进程" in guide
    assert "左候选段" in guide
    assert "右候选段" in guide
    assert "默认尽量合并" in guide
