"""Tests for parse output comparison helper."""
from __future__ import annotations

import json

from scripts.compare_parse_outputs import compare_outputs


def test_compare_outputs_ignores_profile_and_extracted(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    for root in (before, after):
        (root / "task" / "mech_modules" / "EXAMPLE").mkdir(parents=True)
        (root / "task" / "result.json").write_text('{"ok": true}\n', encoding="utf-8")
        (root / "task" / "metadata.json").write_text('{"task": "task"}\n', encoding="utf-8")
        (root / "task" / "mech_modules" / "EXAMPLE" / "proc.log").write_text("same\n", encoding="utf-8")
        (root / "task" / "performance.json").write_text('{"total": 1}\n', encoding="utf-8")
        (root / "task" / "extracted").mkdir()
        (root / "task" / "extracted" / "raw.log").write_text(str(root), encoding="utf-8")

    result = compare_outputs(before, after)

    assert result["ok"] is True
    assert result["differences"] == []


def test_compare_outputs_reports_business_output_difference(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    for root, content in ((before, "old\n"), (after, "new\n")):
        (root / "task" / "mech_modules" / "EXAMPLE").mkdir(parents=True)
        (root / "task" / "result.json").write_text('{"ok": true}\n', encoding="utf-8")
        (root / "task" / "metadata.json").write_text('{"task": "task"}\n', encoding="utf-8")
        (root / "task" / "mech_modules" / "EXAMPLE" / "proc.log").write_text(content, encoding="utf-8")

    result = compare_outputs(before, after)

    assert result["ok"] is False
    assert any("content differs" in item for item in result["differences"])


def test_compare_outputs_does_not_ignore_extracted_module_output(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    for root, content in ((before, "old\n"), (after, "new\n")):
        (root / "task" / "mech_modules" / "extracted").mkdir(parents=True)
        (root / "task" / "result.json").write_text('{"ok": true}\n', encoding="utf-8")
        (root / "task" / "mech_modules" / "extracted" / "proc.log").write_text(
            content,
            encoding="utf-8",
        )

    result = compare_outputs(before, after)

    assert result["ok"] is False
    assert "task\\mech_modules\\extracted\\proc.log: content differs" in {
        str(item) for item in result["differences"]
    } or any("mech_modules" in item and "content differs" in item for item in result["differences"])


def test_compare_outputs_does_not_ignore_task_named_extracted(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    for root, value in ((before, "old"), (after, "new")):
        (root / "extracted").mkdir(parents=True)
        (root / "extracted" / "result.json").write_text(
            json.dumps({"value": value}),
            encoding="utf-8",
        )

    result = compare_outputs(before, after)

    assert result["ok"] is False
    assert any("extracted" in item and "content differs" in item for item in result["differences"])


def test_compare_outputs_reports_missing_roots(tmp_path):
    result = compare_outputs(tmp_path / "missing_before", tmp_path / "missing_after")

    assert result["ok"] is False
    assert "before root does not exist" in result["differences"]
    assert "after root does not exist" in result["differences"]


def test_compare_outputs_reports_empty_roots(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()

    result = compare_outputs(before, after)

    assert result["ok"] is False
    assert "no comparable business files found" in result["differences"]


def test_compare_outputs_ignores_unrelated_files_for_parse_output_detection(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    (before / "notes.txt").write_text("same\n", encoding="utf-8")
    (after / "notes.txt").write_text("same\n", encoding="utf-8")

    result = compare_outputs(before, after)

    assert result["ok"] is False
    assert "no comparable business files found" in result["differences"]


def test_compare_outputs_normalizes_json_runtime_paths_and_created_at(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    for root, created_at in (
        (before, "2026-01-01T00:00:00+00:00"),
        (after, "2026-01-01T00:00:01+00:00"),
    ):
        (root / "task").mkdir(parents=True)
        (root / "task" / "result.json").write_text(
            json.dumps(
                {
                    "created_at": created_at,
                    "extracted_root": str(root / "task" / "extracted"),
                    "diagnostic_slots": [
                        {"path": str(root / "task" / "extracted" / "diag" / "slot_1")}
                    ],
                    "business": {"slot": "1"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    result = compare_outputs(before, after)

    assert result["ok"] is True
    assert result["differences"] == []
