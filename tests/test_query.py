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

    def test_cpu_cycle_path(self, svc, tmp_path):
        path = svc.mech_log_path(
            "task1",
            "2",
            "board-cycle",
            "PROC-456",
            module_name="OTHER",
            cpu_id="3",
            cpu_cycle="cpu-cycle",
        )
        assert path == (
            tmp_path / "task1" / "mech_modules" / "OTHER" / "slot_2"
            / "board-cycle" / "cpu_3" / "cpu-cycle" / "PROC-456.log"
        )

    def test_fallback_without_module_name(self, svc, tmp_path):
        # No result.json → module_name is None → fallback path
        path = svc.mech_log_path("task1", "1", "cycle1", "PROC-123")
        assert path == tmp_path / "task1" / "mech_modules" / "slot_1" / "cycle1" / "PROC-123.log"


def _write_result(tmp_path, task_id, mech_results):
    task_dir = tmp_path / task_id
    task_dir.mkdir(exist_ok=True)
    (task_dir / "result.json").write_text(json.dumps({
        "mech_results": mech_results,
    }, ensure_ascii=False), encoding="utf-8")


class TestMechModules:
    def test_returns_all_modules_by_default(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {"module_name": "EXAMPLE", "slots": [{"slot_id": "1", "board_cycles": []}]},
            {"module_name": "OTHER", "slots": [{"slot_id": "1", "board_cycles": []}]},
        ])
        modules = svc.mech_modules("task")
        assert len(modules) == 2

    def test_filters_by_module_name(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {"module_name": "EXAMPLE", "slots": []},
            {"module_name": "OTHER", "slots": []},
        ])
        modules = svc.mech_modules("task", module_name="OTHER")
        assert len(modules) == 1
        assert modules[0]["module_name"] == "OTHER"

    def test_returns_empty_when_no_file(self, svc):
        assert svc.mech_modules("nonexistent") == []


class TestMechSlotsMultiModule:
    def test_returns_all_modules_by_default(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {"module_name": "EXAMPLE", "slots": [
                {"slot_id": "1", "board_cycles": []},
                {"slot_id": "2", "board_cycles": []},
            ]},
            {"module_name": "OTHER", "slots": [
                {"slot_id": "1", "board_cycles": []},
            ]},
        ])
        slots = svc.mech_slots("task")
        assert {s["_module_name"] for s in slots} == {"EXAMPLE", "OTHER"}
        assert len(slots) == 3

    def test_can_filter_by_module(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {"module_name": "EXAMPLE", "slots": [{"slot_id": "1", "board_cycles": []}]},
            {"module_name": "OTHER", "slots": [{"slot_id": "1", "board_cycles": []}]},
        ])
        slots = svc.mech_slots("task", module_name="OTHER")
        assert len(slots) == 1
        assert slots[0]["_module_name"] == "OTHER"


class TestMechLifecyclesMultiModule:
    def test_returns_all_matching_modules(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {"module_name": "EXAMPLE", "slots": [
                {"slot_id": "1", "board_cycles": [{"dir_name": "c1"}]},
            ]},
            {"module_name": "OTHER", "slots": [
                {"slot_id": "1", "board_cycles": [{"dir_name": "c2"}]},
            ]},
        ])
        groups = svc.mech_lifecycles("task", slot_id="1")
        assert {g["module_name"] for g in groups} == {"EXAMPLE", "OTHER"}
        assert len(groups) == 2

    def test_can_filter_by_module(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {"module_name": "EXAMPLE", "slots": [
                {"slot_id": "1", "board_cycles": [{"dir_name": "c1"}]},
            ]},
            {"module_name": "OTHER", "slots": [
                {"slot_id": "1", "board_cycles": [{"dir_name": "c2"}]},
            ]},
        ])
        groups = svc.mech_lifecycles("task", slot_id="1", module_name="EXAMPLE")
        assert len(groups) == 1
        assert groups[0]["module_name"] == "EXAMPLE"

    def test_returns_lifecycle_reliability_and_boundary_issues(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {
                "module_name": "EXAMPLE",
                "slots": [
                    {
                        "slot_id": "1",
                        "lifecycle_reliable": False,
                        "boundary_issues": [
                            {
                                "kind": "unsafe_cycle_split",
                                "severity": "error",
                                "split_time": "2026-01-03T00:00:10+08:00",
                            },
                        ],
                        "board_cycles": [{"dir_name": "c1"}],
                    },
                ],
            },
        ])

        groups = svc.mech_lifecycles("task", slot_id="1", module_name="EXAMPLE")

        assert groups[0]["lifecycle_reliable"] is False
        assert groups[0]["boundary_issues"][0]["kind"] == "unsafe_cycle_split"

    def test_returns_empty_when_no_match(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {"module_name": "EXAMPLE", "slots": [{"slot_id": "1", "board_cycles": []}]},
        ])
        groups = svc.mech_lifecycles("task", slot_id="99")
        assert groups == []
