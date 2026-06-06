"""Tests for structured performance DFX."""
from __future__ import annotations

import json

import pytest

from backend.models import MechLogEntry
from backend.performance import PerformanceRecorder, resolve_worker_count


def test_performance_recorder_writes_sanitized_stage_tree(tmp_path):
    recorder = PerformanceRecorder(
        enabled=True,
        config={"debug_expand_gz": False, "extraction_workers": "auto"},
    )

    recorder.record_stage(
        "diagnostic_scan.shared",
        elapsed_seconds=1.25,
        files=2,
        lines=100,
        module1_entries=3,
        raw="must not be persisted",
        context="must not be persisted",
    )
    output = recorder.write(tmp_path)

    data = json.loads(output.read_text(encoding="utf-8"))
    payload = json.dumps(data, ensure_ascii=False)

    assert data["schema_version"] == 1
    assert data["config"]["debug_expand_gz"] is False
    assert data["stages"][0]["name"] == "diagnostic_scan.shared"
    assert data["stage_tree"]["diagnostic_scan"]["children"]["shared"]["elapsed_seconds"] == 1.25
    assert data["stages"][0]["metrics"]["files"] == 2
    assert data["stages"][0]["metrics"]["lines"] == 100
    assert "must not be persisted" not in payload
    assert "raw" not in data["stages"][0]["metrics"]
    assert "context" not in data["stages"][0]["metrics"]


def test_resolve_worker_count_auto_is_bounded():
    assert resolve_worker_count("auto", default_cap=4, cpu_count=64) == 4
    assert resolve_worker_count("auto", default_cap=4, cpu_count=2) == 2
    assert resolve_worker_count(1, default_cap=4, cpu_count=64) == 1
    assert resolve_worker_count("3", default_cap=4, cpu_count=64) == 3


def test_resolve_worker_count_rejects_unbounded_explicit_values():
    with pytest.raises(ValueError):
        resolve_worker_count("999999", default_cap=4, cpu_count=8)


def test_summary_lines_include_config_and_next_step():
    recorder = PerformanceRecorder(
        enabled=True,
        config={
            "debug_expand_gz": False,
            "extraction_workers": "auto",
            "diagnostic_scan_workers": 2,
        },
    )
    recorder.record_stage("pipeline.parse", elapsed_seconds=2.0)

    lines = recorder.summary_lines()

    assert any("配置:" in line and "debug_expand_gz=False" in line for line in lines)
    assert any(line.startswith("建议:") for line in lines)


def test_performance_recorder_blocks_sensitive_key_variants_and_unknown_keys(tmp_path):
    recorder = PerformanceRecorder(enabled=True)
    recorder.record_stage(
        "diagnostic_scan.shared",
        elapsed_seconds=0.1,
        raw_excerpt="secret raw",
        rawLog="secret raw camel",
        context_before="secret context",
        contextBefore="secret context camel",
        payloadSnippet="secret payload camel",
        original_log="secret log",
        log_line="secret line",
        lineText="secret line camel",
        samples=["secret sample"],
        note="secret note",
        examples=["secret example"],
        safe_label="stage label",
        entry=MechLogEntry(raw="secret raw", context="secret context"),
        **{
            "Context=slot secret": 1,
            "raw log line text": 2,
            "payload.snippet": 3,
            "line text secret": {"files": 4},
            "customer_secret": 5,
        },
    )

    data = json.loads(recorder.write(tmp_path).read_text(encoding="utf-8"))
    payload = json.dumps(data, ensure_ascii=False)

    assert "secret" not in payload
    assert "stage label" not in payload
    assert "customer_secret" not in payload
    assert "entry" not in data["stages"][0]["metrics"]


def test_performance_recorder_does_not_persist_object_type_name(tmp_path):
    secret_type = type("SECRET_RAW_CONTEXT_PAYLOAD_LINE", (), {})
    recorder = PerformanceRecorder(enabled=True)
    recorder.record_stage("diagnostic_scan.shared", elapsed_seconds=0.1, files=secret_type())

    data = json.loads(recorder.write(tmp_path).read_text(encoding="utf-8"))
    payload = json.dumps(data, ensure_ascii=False)

    assert data["stages"][0]["metrics"]["files"] == "<object>"
    assert "SECRET_RAW_CONTEXT_PAYLOAD_LINE" not in payload


def test_performance_recorder_sanitizes_untrusted_stage_names(tmp_path):
    recorder = PerformanceRecorder(enabled=True)
    recorder.record_stage(
        "raw log secret Context=customer_payload",
        elapsed_seconds=0.1,
        files=1,
    )

    data = json.loads(recorder.write(tmp_path).read_text(encoding="utf-8"))
    payload = json.dumps(data, ensure_ascii=False)

    assert data["stages"][0]["name"] == "custom"
    assert "custom" in data["stage_tree"]
    assert "secret" not in payload
    assert "customer_payload" not in payload
