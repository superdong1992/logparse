"""Tests for backend/query.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.query import ResultQueryService


@pytest.fixture
def svc(tmp_path):
    return ResultQueryService(tmp_path)


class TestFirstModuleName:
    def test_returns_first_module_name(self, svc, tmp_path):
        task_dir = tmp_path / "task1"
        task_dir.mkdir()
        (task_dir / "result.json").write_text(json.dumps({
            "mech_results": [
                {"module_name": "EXAMPLE"},
                {"module_name": "OTHER"},
            ]
        }), encoding="utf-8")
        assert svc.first_module_name("task1") == "EXAMPLE"

    def test_returns_none_when_no_results(self, svc, tmp_path):
        task_dir = tmp_path / "task2"
        task_dir.mkdir()
        (task_dir / "result.json").write_text(json.dumps({
            "mech_results": []
        }), encoding="utf-8")
        assert svc.first_module_name("task2") is None

    def test_returns_none_when_no_file(self, svc):
        assert svc.first_module_name("nonexistent") is None


class TestMechLogPath:
    def test_path_includes_module_name(self, svc, tmp_path):
        task_dir = tmp_path / "task1"
        task_dir.mkdir()
        (task_dir / "result.json").write_text(json.dumps({
            "mech_results": [{"module_name": "EXAMPLE"}]
        }), encoding="utf-8")
        path = svc.mech_log_path("task1", "1", "cycle1", "PROC-123")
        assert path == task_dir / "mech_modules" / "EXAMPLE" / "slot_1" / "cycle1" / "PROC-123.log"

    def test_explicit_module_name(self, svc, tmp_path):
        path = svc.mech_log_path("task1", "2", "cycle1", "PROC-456", module_name="OTHER")
        assert path == tmp_path / "task1" / "mech_modules" / "OTHER" / "slot_2" / "cycle1" / "PROC-456.log"

    def test_fallback_without_module_name(self, svc, tmp_path):
        # No result.json → module_name is None → fallback path
        path = svc.mech_log_path("task1", "1", "cycle1", "PROC-123")
        assert path == tmp_path / "task1" / "mech_modules" / "slot_1" / "cycle1" / "PROC-123.log"
