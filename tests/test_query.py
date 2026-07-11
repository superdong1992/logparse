"""Tests for backend/query.py."""
from __future__ import annotations

import json

import pytest

from backend.query import QueryArtifactSchemaError, ResultQueryService
from backend.utils import safe_log_filename, safe_path_segment


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
        path = svc.mech_log_path("task1", "1", "cycle1", "PROC", pid="123")
        assert path == (
            task_dir / "mech_modules" / safe_path_segment("EXAMPLE") / "slot_1"
            / safe_path_segment("cycle1") / safe_log_filename("PROC", "123")
        )

    def test_explicit_module_name(self, svc, tmp_path):
        path = svc.mech_log_path("task1", "2", "cycle1", "PROC", module_name="OTHER", pid="456")
        assert path == (
            tmp_path / "task1" / "mech_modules" / safe_path_segment("OTHER") / "slot_2"
            / safe_path_segment("cycle1") / safe_log_filename("PROC", "456")
        )

    def test_path_escapes_slot_separator(self, svc, tmp_path):
        path = svc.mech_log_path("task1", "1/2", "cycle1", "PROC", module_name="OTHER", pid="456")
        assert path == (
            tmp_path / "task1" / "mech_modules" / safe_path_segment("OTHER")
            / f"slot_{safe_path_segment('1/2')}" / safe_path_segment("cycle1")
            / safe_log_filename("PROC", "456")
        )

    def test_path_escapes_module_name_separator(self, svc, tmp_path):
        path = svc.mech_log_path("task1", "1", "cycle1", "PROC", module_name=r"..\..\OTHER", pid="456")
        assert path == (
            tmp_path
            / "task1"
            / "mech_modules"
            / safe_path_segment(r"..\..\OTHER")
            / "slot_1"
            / safe_path_segment("cycle1")
            / safe_log_filename("PROC", "456")
        )

    def test_cpu_cycle_path(self, svc, tmp_path):
        path = svc.mech_log_path(
            "task1",
            "2",
            "board-cycle",
            "PROC",
            module_name="OTHER",
            cpu_id="3",
            cpu_cycle="cpu-cycle",
            pid="456",
        )
        assert path == (
            tmp_path / "task1" / "mech_modules" / safe_path_segment("OTHER") / "slot_2"
            / safe_path_segment("board-cycle") / "cpu_3" / safe_path_segment("cpu-cycle")
            / safe_log_filename("PROC", "456")
        )

    def test_cpu_without_cpu_cycle_uses_writer_compat_path(self, svc, tmp_path):
        path = svc.mech_log_path(
            "task1",
            "2",
            "board-cycle",
            "PROC",
            module_name="OTHER",
            cpu_id="3",
            pid="456",
        )
        assert path == (
            tmp_path / "task1" / "mech_modules" / safe_path_segment("OTHER") / "slot_2"
            / safe_path_segment("board-cycle") / "cpu_3" / safe_log_filename("PROC", "456")
        )

    def test_fallback_without_module_name(self, svc, tmp_path):
        # No result.json → module_name is None → fallback path
        path = svc.mech_log_path("task1", "1", "cycle1", "PROC", pid="123")
        assert path == (
            tmp_path / "task1" / "mech_modules" / "slot_1"
            / safe_path_segment("cycle1") / safe_log_filename("PROC", "123")
        )

    def test_proc_argument_without_pid_is_exact_process_name(self, svc, tmp_path):
        path = svc.mech_log_path("task1", "1", "cycle1", "svc-100", module_name="OTHER")
        assert path == (
            tmp_path / "task1" / "mech_modules" / safe_path_segment("OTHER") / "slot_1"
            / safe_path_segment("cycle1") / safe_log_filename("svc-100", "")
        )

    def test_proc_argument_without_pid_does_not_fall_back_to_dash_pid(self, svc, tmp_path):
        base = (
            tmp_path / "task1" / "mech_modules" / safe_path_segment("OTHER") / "slot_1"
            / safe_path_segment("cycle1")
        )
        base.mkdir(parents=True)
        (base / safe_log_filename("svc", "100")).write_text("with pid\n", encoding="utf-8")

        path = svc.mech_log_path("task1", "1", "cycle1", "svc-100", module_name="OTHER")

        assert path == base / safe_log_filename("svc-100", "")


def _write_result(tmp_path, task_id, mech_results):
    task_dir = tmp_path / task_id
    task_dir.mkdir(exist_ok=True)
    (task_dir / "result.json").write_text(json.dumps({
        "mech_results": mech_results,
    }, ensure_ascii=False), encoding="utf-8")


def _proc(name, pid, total_count=1):
    return {
        "process_name": name,
        "pid": pid,
        "total_count": total_count,
        "missing_sequences": [],
        "missing_count": 0,
    }


def _board_cycle(name, start, end, processes=None, cpu_cycles=None):
    return {
        "dir_name": name,
        "start_time": start,
        "end_time": end,
        "processes": processes or [],
        "cpu_cycles": cpu_cycles or [],
    }


def _cpu_cycle(cpu_id, name, start, end, processes=None):
    return {
        "cpu_id": cpu_id,
        "dir_name": name,
        "start_time": start,
        "end_time": end,
        "processes": processes or [],
    }


def _write_mech_log(
    tmp_path,
    task_id,
    module_name,
    slot,
    board_cycle,
    proc_file,
    *,
    cpu_id=None,
    cpu_cycle=None,
):
    log_dir = (
        tmp_path
        / task_id
        / "mech_modules"
        / safe_path_segment(module_name)
        / f"slot_{safe_path_segment(slot)}"
        / safe_path_segment(board_cycle)
    )
    if cpu_id:
        log_dir = log_dir / f"cpu_{safe_path_segment(cpu_id)}"
        if cpu_cycle:
            log_dir = log_dir / safe_path_segment(cpu_cycle)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / proc_file
    log_path.write_text("matched log\n", encoding="utf-8")
    return log_path


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

    def test_rejects_explicit_unknown_result_schema(self, svc, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        (task_dir / "result.json").write_text(
            json.dumps({"schema_version": 99, "mech_results": []}),
            encoding="utf-8",
        )

        with pytest.raises(QueryArtifactSchemaError, match="expected 2"):
            svc.read_result("task")


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

    def test_returns_lifecycle_reliability_without_legacy_issue_field(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {
                "module_name": "EXAMPLE",
                "slots": [
                    {
                        "slot_id": "1",
                        "lifecycle_reliable": False,
                        "lifecycle_split_result": {
                            "algorithm": "interval_v3",
                            "candidate_segments": [],
                            "merge_decisions": [],
                            "lifecycles": [],
                            "journal_evidence": [],
                            "issues": [],
                            "lifecycle_reliable": False,
                        },
                        "board_cycles": [{"dir_name": "c1"}],
                    },
                ],
            },
        ])

        groups = svc.mech_lifecycles("task", slot_id="1", module_name="EXAMPLE")

        assert groups[0]["lifecycle_reliable"] is False
        assert ("boundary_" + "issues") not in groups[0]
        assert groups[0]["lifecycle_split_result"]["algorithm"] == "interval_v3"

    def test_returns_lifecycle_split_v3_result(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {
                "module_name": "EXAMPLE",
                "slots": [
                    {
                        "slot_id": "1",
                        "lifecycle_reliable": True,
                        "lifecycle_split_result": {
                            "algorithm": "interval_v3",
                            "candidate_segments": [
                                {
                                    "scope": "board",
                                    "slot": "1",
                                    "candidate_index": 0,
                                },
                            ],
                            "merge_decisions": [],
                            "lifecycles": [],
                            "journal_evidence": [],
                            "issues": [],
                            "lifecycle_reliable": True,
                        },
                        "board_cycles": [{"dir_name": "c1"}],
                    },
                ],
            },
        ])

        groups = svc.mech_lifecycles("task", slot_id="1", module_name="EXAMPLE")

        assert groups[0]["lifecycle_split_result"]["algorithm"] == "interval_v3"
        assert groups[0]["lifecycle_split_result"]["candidate_segments"][0]["scope"] == "board"

    def test_returns_empty_when_no_match(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {"module_name": "EXAMPLE", "slots": [{"slot_id": "1", "board_cycles": []}]},
        ])
        groups = svc.mech_lifecycles("task", slot_id="99")
        assert groups == []


class TestResolveTargetLogs:
    def test_exact_board_cycle_returns_target_log_path(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {
                "module_key": "module1",
                "module_name": "EXAMPLE",
                "slots": [
                    {
                        "slot_id": "1",
                        "board_cycles": [
                            _board_cycle(
                                "20260103T000000-20260103T001000",
                                "2026-01-03T00:00:00",
                                "2026-01-03T00:10:00",
                                [_proc("SERVICE", "123")],
                            ),
                        ],
                    },
                ],
            },
        ])
        log_path = _write_mech_log(
            tmp_path,
            "task",
            "EXAMPLE",
            "1",
            "20260103T000000-20260103T001000",
            safe_log_filename("SERVICE", "123"),
        )

        payload = svc.resolve_target_logs(
            "task",
            problem_time="2026-01-03T00:05:00",
            module="module1",
            slot="slot_1",
            process_name="service",
            pid="123",
            label="client",
        )

        target = payload["target_logs"][0]
        assert payload["schema_version"] == 1
        assert payload["api_version"] == 1
        assert target["label"] == "client"
        assert target["module_key"] == "module1"
        assert target["module_name"] == "EXAMPLE"
        assert target["slot"] == "1"
        assert target["process_name"] == "SERVICE"
        assert target["pid"] == "123"
        assert target["match_status"] == "exact"
        assert target["board_cycle"] == "20260103T000000-20260103T001000"
        assert target["cpu_cycle"] is None
        assert target["log_path"] == str(log_path)

    def test_unknown_result_schema_returns_stable_error(self, svc, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        (task_dir / "result.json").write_text(
            json.dumps({"schema_version": 99, "mech_results": []}),
            encoding="utf-8",
        )

        payload = svc.resolve_target_logs(
            "task",
            problem_time="2026-01-03T00:05:00",
            module="module1",
            slot="1",
            process_name="SERVICE",
            explain=True,
        )

        assert payload["schema_version"] == 1
        assert payload["api_version"] == 1
        assert payload["target_logs"][0]["error_code"] == "LP_SCHEMA_UNSUPPORTED"
        assert payload["selection_diagnostics"]["error_code"] == "LP_SCHEMA_UNSUPPORTED"

    def test_exact_board_cycle_with_slash_slot_uses_safe_output_path(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {
                "module_key": "module1",
                "module_name": "EXAMPLE",
                "slots": [
                    {
                        "slot_id": "1/2",
                        "board_cycles": [
                            _board_cycle(
                                "cycle",
                                "2026-01-03T00:00:00",
                                "2026-01-03T00:10:00",
                                [_proc("SERVICE", "123")],
                            ),
                        ],
                    },
                ],
            },
        ])
        log_path = _write_mech_log(
            tmp_path,
            "task",
            "EXAMPLE",
            "1/2",
            "cycle",
            safe_log_filename("SERVICE", "123"),
        )

        payload = svc.resolve_target_logs(
            "task",
            problem_time="2026-01-03T00:05:00",
            module="module1",
            slot="1/2",
            process_name="SERVICE",
            pid="123",
        )

        target = payload["target_logs"][0]
        assert target["match_status"] == "exact"
        assert target["slot"] == "1/2"
        assert target["log_path"] == str(log_path)
        assert f"slot_{safe_path_segment('1/2')}" in str(log_path)

    def test_exact_board_cycle_with_unsafe_process_uses_safe_filename(self, svc, tmp_path):
        process_name = r"..\..\escape"
        pid = "10/20"
        _write_result(tmp_path, "task", [
            {
                "module_key": "module1",
                "module_name": "EXAMPLE",
                "slots": [
                    {
                        "slot_id": "1",
                        "board_cycles": [
                            _board_cycle(
                                "cycle",
                                "2026-01-03T00:00:00",
                                "2026-01-03T00:10:00",
                                [_proc(process_name, pid)],
                            ),
                        ],
                    },
                ],
            },
        ])
        log_path = _write_mech_log(
            tmp_path,
            "task",
            "EXAMPLE",
            "1",
            "cycle",
            safe_log_filename(process_name, pid),
        )

        payload = svc.resolve_target_logs(
            "task",
            problem_time="2026-01-03T00:05:00",
            module="module1",
            slot="1",
            process_name=process_name,
            pid=pid,
        )

        target = payload["target_logs"][0]
        assert target["match_status"] == "exact"
        assert target["log_path"] == str(log_path)

    def test_exact_board_cycle_with_unsafe_module_name_uses_safe_directory(self, svc, tmp_path):
        module_name = r"..\..\EXAMPLE"
        _write_result(tmp_path, "task", [
            {
                "module_key": "module1",
                "module_name": module_name,
                "slots": [
                    {
                        "slot_id": "1",
                        "board_cycles": [
                            _board_cycle(
                                "cycle",
                                "2026-01-03T00:00:00",
                                "2026-01-03T00:10:00",
                                [_proc("SERVICE", "123")],
                            ),
                        ],
                    },
                ],
            },
        ])
        log_path = _write_mech_log(
            tmp_path,
            "task",
            module_name,
            "1",
            "cycle",
            safe_log_filename("SERVICE", "123"),
        )

        payload = svc.resolve_target_logs(
            "task",
            problem_time="2026-01-03T00:05:00",
            module="module1",
            slot="1",
            process_name="SERVICE",
            pid="123",
        )

        target = payload["target_logs"][0]
        assert target["match_status"] == "exact"
        assert target["log_path"] == str(log_path)
        assert safe_path_segment(module_name) in str(log_path)

    def test_out_of_range_time_uses_unique_nearest_cycle(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {
                "module_key": "module1",
                "module_name": "EXAMPLE",
                "slots": [
                    {
                        "slot_id": "1",
                        "board_cycles": [
                            _board_cycle("old", "2026-01-03T00:00:00", "2026-01-03T00:10:00", [_proc("SERVICE", "123")]),
                            _board_cycle("new", "2026-01-03T00:15:00", "2026-01-03T00:20:00", [_proc("SERVICE", "123")]),
                        ],
                    },
                ],
            },
        ])
        log_path = _write_mech_log(
            tmp_path,
            "task",
            "EXAMPLE",
            "1",
            "new",
            safe_log_filename("SERVICE", "123"),
        )

        payload = svc.resolve_target_logs(
            "task",
            problem_time="2026-01-03T00:30:00",
            module="EXAMPLE",
            slot="1",
            process_name="SERVICE",
            pid="123",
        )

        target = payload["target_logs"][0]
        assert target["match_status"] == "nearest"
        assert target["board_cycle"] == "new"
        assert target["log_path"] == str(log_path)
        assert any("nearest" in caveat for caveat in target["caveats"])

    def test_nearest_tie_is_ambiguous_without_log_path(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {
                "module_key": "module1",
                "module_name": "EXAMPLE",
                "slots": [
                    {
                        "slot_id": "1",
                        "board_cycles": [
                            _board_cycle("left", "2026-01-03T00:00:00", "2026-01-03T00:10:00", [_proc("SERVICE", "123")]),
                            _board_cycle("right", "2026-01-03T00:15:00", "2026-01-03T00:20:00", [_proc("SERVICE", "123")]),
                        ],
                    },
                ],
            },
        ])

        payload = svc.resolve_target_logs(
            "task",
            problem_time="2026-01-03T00:12:30",
            module="EXAMPLE",
            slot="1",
            process_name="SERVICE",
            pid="123",
        )

        target = payload["target_logs"][0]
        assert target["match_status"] == "ambiguous"
        assert "log_path" not in target
        assert any("nearest tie" in caveat for caveat in target["caveats"])

    def test_nested_cpu_cycle_uses_cpu_time_window(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {
                "module_key": "module1",
                "module_name": "EXAMPLE",
                "slots": [
                    {
                        "slot_id": "1",
                        "board_cycles": [
                            _board_cycle(
                                "board",
                                "2026-01-03T00:00:00",
                                "2026-01-03T00:20:00",
                                [],
                                [
                                    _cpu_cycle(
                                        "3",
                                        "cpu-window",
                                        "2026-01-03T00:05:00",
                                        "2026-01-03T00:10:00",
                                        [_proc("WORKER", "777")],
                                    ),
                                ],
                            ),
                        ],
                    },
                ],
            },
        ])
        log_path = _write_mech_log(
            tmp_path,
            "task",
            "EXAMPLE",
            "1",
            "board",
            safe_log_filename("WORKER", "777"),
            cpu_id="3",
            cpu_cycle="cpu-window",
        )

        payload = svc.resolve_target_logs(
            "task",
            problem_time="2026-01-03T00:06:00",
            module="EXAMPLE",
            slot="1",
            process_name="WORKER",
            pid="777",
        )

        target = payload["target_logs"][0]
        assert target["match_status"] == "exact"
        assert target["board_cycle"] == "board"
        assert target["cpu_id"] == "3"
        assert target["cpu_cycle"] == "cpu-window"
        assert target["log_path"] == str(log_path)

    def test_pid_is_strict_when_provided(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {
                "module_key": "module1",
                "module_name": "EXAMPLE",
                "slots": [
                    {
                        "slot_id": "1",
                        "board_cycles": [
                            _board_cycle("cycle", "2026-01-03T00:00:00", "2026-01-03T00:10:00", [_proc("SERVICE", "123")]),
                        ],
                    },
                ],
            },
        ])

        payload = svc.resolve_target_logs(
            "task",
            problem_time="2026-01-03T00:05:00",
            module="EXAMPLE",
            slot="1",
            process_name="SERVICE",
            pid="999",
        )

        target = payload["target_logs"][0]
        assert target["match_status"] == "missing"
        assert "log_path" not in target
        assert any("process not found" in caveat for caveat in target["caveats"])

    def test_board_level_cpu_log_path_is_detected_from_filesystem(self, svc, tmp_path):
        _write_result(tmp_path, "task", [
            {
                "module_key": "module1",
                "module_name": "EXAMPLE",
                "slots": [
                    {
                        "slot_id": "1",
                        "board_cycles": [
                            _board_cycle("cycle", "2026-01-03T00:00:00+08:00", "2026-01-03T00:10:00+08:00", [_proc("CPU_PROC", "44")]),
                        ],
                    },
                ],
            },
        ])
        log_path = _write_mech_log(
            tmp_path,
            "task",
            "EXAMPLE",
            "1",
            "cycle",
            safe_log_filename("CPU_PROC", "44"),
            cpu_id="2",
        )

        payload = svc.resolve_target_logs(
            "task",
            problem_time="2026-01-03T00:05:00",
            module="module1",
            slot="slot_1",
            process_name="CPU_PROC",
            pid="44",
        )

        target = payload["target_logs"][0]
        assert target["match_status"] == "exact"
        assert target["cpu_id"] == "2"
        assert target["cpu_cycle"] is None
        assert target["log_path"] == str(log_path)
