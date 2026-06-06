from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from backend.models import (
    LogEntry,
    MechBoardCycle,
    MechCpuCycle,
    MechLogEntry,
    MechProcessLifecycle,
    MechResult,
    MechSlotOutput,
    ParseResult,
    SlotInfo,
)
from backend.parsing.timestamp_extractor import TimestampExtractor
from backend.parsing.output_writer import MechOutputWriter
from backend.plugins.mechanisms import module2 as module2_impl
from backend.plugins.mechanisms.module2 import Module2Plugin
from backend.utils import safe_log_filename, safe_path_segment


def _ts(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 1, 3, hour, minute, 0, tzinfo=timezone(timedelta(hours=8)))


def _timestamp_extractor() -> TimestampExtractor:
    return TimestampExtractor(
        re.compile(r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?")
    )


def _module2_config() -> dict:
    return {
        "module_name": "MODULE2",
        "identifying_keyword": "xxx",
        "depends_on_module": "module1",
        "diag_pattern": (
            r"Slot=(?P<Slot>[\d/]+),CPU-Id=(?P<CPU_Id>[^,]*),"
            r"ProcessName=(?P<ProcessName>[^,]+),Context=\"(?P<Context>.*?)\""
        ),
    }


def test_module2_validate_config_requires_fields():
    cfg = {}

    errors = Module2Plugin.validate_config("module2", cfg)

    assert any("module_name" in e for e in errors)
    assert any("identifying_keyword" in e for e in errors)
    assert any("depends_on_module" in e for e in errors)
    assert any("diag_pattern" in e for e in errors)


def _module1_result() -> MechResult:
    return MechResult(
        module_name="EXAMPLE",
        module_key="module1",
        slots=[
            MechSlotOutput(
                slot_id="2",
                board_cycles=[
                    MechBoardCycle(
                        dir_name="20260103T000000-20260103T010000",
                        start_time=_ts(0),
                        end_time=_ts(1),
                    )
                ],
            )
        ],
    )


def _module1_nested_result() -> MechResult:
    return MechResult(
        module_name="EXAMPLE",
        module_key="module1",
        slots=[
            MechSlotOutput(
                slot_id="2",
                board_cycles=[
                    MechBoardCycle(
                        dir_name="20260103T000000-20260103T001000",
                        start_time=_ts(0),
                        end_time=_ts(0, 10),
                        cpu_cycles=[
                            MechCpuCycle(
                                cpu_id="3",
                                dir_name="20260103T000500-20260103T000700",
                                start_time=_ts(0, 5),
                                end_time=_ts(0, 7),
                                processes=[
                                    MechProcessLifecycle(
                                        process_name="upstream",
                                        pid="1",
                                        total_count=0,
                                    )
                                ],
                            )
                        ],
                    )
                ],
            )
        ],
    )


def _module1_board_pid_result() -> MechResult:
    return MechResult(
        module_name="EXAMPLE",
        module_key="module1",
        slots=[
            MechSlotOutput(
                slot_id="2",
                board_cycles=[
                    MechBoardCycle(
                        dir_name="20260103T000000-20260103T010000",
                        start_time=_ts(0),
                        end_time=_ts(1),
                        processes=[
                            MechProcessLifecycle(
                                process_name="svc",
                                pid="100",
                                total_count=0,
                            )
                        ],
                    )
                ],
            )
        ],
    )


def _module1_nested_pid_result() -> MechResult:
    return MechResult(
        module_name="EXAMPLE",
        module_key="module1",
        slots=[
            MechSlotOutput(
                slot_id="2",
                board_cycles=[
                    MechBoardCycle(
                        dir_name="20260103T000000-20260103T001000",
                        start_time=_ts(0),
                        end_time=_ts(0, 10),
                        cpu_cycles=[
                            MechCpuCycle(
                                cpu_id="3",
                                dir_name="20260103T000500-20260103T000700",
                                start_time=_ts(0, 5),
                                end_time=_ts(0, 7),
                                processes=[
                                    MechProcessLifecycle(
                                        process_name="hellokitty",
                                        pid="123",
                                        total_count=0,
                                    )
                                ],
                            )
                        ],
                    )
                ],
            )
        ],
    )


def _module1_reused_pid_result() -> MechResult:
    return MechResult(
        module_name="EXAMPLE",
        module_key="module1",
        slots=[
            MechSlotOutput(
                slot_id="2",
                board_cycles=[
                    MechBoardCycle(
                        dir_name="20260103T000000-20260103T001000",
                        start_time=_ts(0),
                        end_time=_ts(0, 10),
                        processes=[
                            MechProcessLifecycle(
                                process_name="svc",
                                pid="100",
                                total_count=0,
                            )
                        ],
                    ),
                    MechBoardCycle(
                        dir_name="20260103T010000-20260103T011000",
                        start_time=_ts(1),
                        end_time=_ts(1, 10),
                        processes=[
                            MechProcessLifecycle(
                                process_name="svc",
                                pid="100",
                                total_count=0,
                            )
                        ],
                    ),
                ],
            )
        ],
    )


def _module1_two_pid_expansion_result() -> MechResult:
    return MechResult(
        module_name="EXAMPLE",
        module_key="module1",
        slots=[
            MechSlotOutput(
                slot_id="2",
                board_cycles=[
                    MechBoardCycle(
                        dir_name="20260103T000000-20260103T001000",
                        start_time=_ts(0),
                        end_time=_ts(0, 10),
                        processes=[
                            MechProcessLifecycle(
                                process_name="left",
                                pid="100",
                                total_count=0,
                            )
                        ],
                    ),
                    MechBoardCycle(
                        dir_name="20260103T010000-20260103T011000",
                        start_time=_ts(1),
                        end_time=_ts(1, 10),
                        processes=[
                            MechProcessLifecycle(
                                process_name="right",
                                pid="200",
                                total_count=0,
                            )
                        ],
                    ),
                ],
            )
        ],
    )


def _module1_pid_then_unrelated_result() -> MechResult:
    return MechResult(
        module_name="EXAMPLE",
        module_key="module1",
        slots=[
            MechSlotOutput(
                slot_id="2",
                board_cycles=[
                    MechBoardCycle(
                        dir_name="20260103T000000-20260103T001000",
                        start_time=_ts(0),
                        end_time=_ts(0, 10),
                        processes=[
                            MechProcessLifecycle(
                                process_name="svc",
                                pid="100",
                                total_count=0,
                            )
                        ],
                    ),
                    MechBoardCycle(
                        dir_name="20260103T010000-20260103T011000",
                        start_time=_ts(1),
                        end_time=_ts(1, 10),
                        processes=[
                            MechProcessLifecycle(
                                process_name="other",
                                pid="200",
                                total_count=0,
                            )
                        ],
                    ),
                ],
            )
        ],
    )


def _module1_nested_pid_then_unrelated_cpu_result() -> MechResult:
    return MechResult(
        module_name="EXAMPLE",
        module_key="module1",
        slots=[
            MechSlotOutput(
                slot_id="2",
                board_cycles=[
                    MechBoardCycle(
                        dir_name="20260103T000000-20260103T020000",
                        start_time=_ts(0),
                        end_time=_ts(2),
                        cpu_cycles=[
                            MechCpuCycle(
                                cpu_id="3",
                                dir_name="20260103T000000-20260103T001000",
                                start_time=_ts(0),
                                end_time=_ts(0, 10),
                                processes=[
                                    MechProcessLifecycle(
                                        process_name="svc",
                                        pid="100",
                                        total_count=0,
                                    )
                                ],
                            ),
                            MechCpuCycle(
                                cpu_id="3",
                                dir_name="20260103T010000-20260103T011000",
                                start_time=_ts(1),
                                end_time=_ts(1, 10),
                                processes=[
                                    MechProcessLifecycle(
                                        process_name="other",
                                        pid="200",
                                        total_count=0,
                                    )
                                ],
                            ),
                        ],
                    )
                ],
            )
        ],
    )


def test_module2_scans_diag_logs_and_parses_bracket_pid(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:10:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="xxxxx"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    assert mech.module_name == "MODULE2"
    assert mech.module_key == "module2"
    assert mech.diag_entry_count == 1
    cycle = mech.slots[0].board_cycles[0]
    assert cycle.dir_name == "20260103T000000-20260103T010000"
    assert cycle.processes == []
    cpu_cycle = cycle.cpu_cycles[0]
    assert cpu_cycle.cpu_id == "3"
    assert cpu_cycle.dir_name == "unknown"
    proc = cpu_cycle.processes[0]
    assert proc.process_name == "hellokitty"
    assert proc.pid == "123"
    assert proc.logs[0].cpu_id == "3"
    assert proc.logs[0].context == "xxxxx"


def test_module2_emits_perf_logs_with_elapsed(tmp_path, caplog):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:10:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="xxxxx"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    with caplog.at_level(logging.INFO, logger="backend.plugins.mechanisms.module2"):
        plugin.parse(result)

    assert "LOGPARSE_PERF module2.find_dependency module=module2 elapsed=" in caplog.text
    assert "LOGPARSE_PERF module2.diag_scan module=module2 elapsed=" in caplog.text
    assert "LOGPARSE_PERF module2.normalize_timezones module=module2 elapsed=" in caplog.text
    assert "LOGPARSE_PERF module2.build_result module=module2 elapsed=" in caplog.text
    assert "LOGPARSE_PERF module2.assign_cycles module=module2 slot=2 elapsed=" in caplog.text
    assert "LOGPARSE_PERF module2.assign_initial module=module2 slot=2 elapsed=" in caplog.text
    assert "LOGPARSE_PERF module2.merge_known_unknown module=module2 slot=2 elapsed=" in caplog.text
    assert "LOGPARSE_PERF module2.merge_projected_unknown module=module2 slot=2 elapsed=" in caplog.text
    assert "LOGPARSE_PERF module2.build_cycles module=module2 slot=2 elapsed=" in caplog.text


def test_module2_logs_outside_module1_cycle_go_to_unknown(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T02:10:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="outside cycle"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    cycle = mech.slots[0].board_cycles[0]
    assert cycle.dir_name == "unknown"
    assert cycle.start_time is None
    assert cycle.end_time is None
    assert cycle.processes == []
    assert cycle.cpu_cycles[0].cpu_id == "3"
    assert cycle.cpu_cycles[0].dir_name == "unknown"
    assert cycle.cpu_cycles[0].processes[0].logs[0].context == "outside cycle"


def test_module2_board_entry_before_module1_window_uses_pid_match_and_expands_start(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-02T23:50:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc[100],Context="early pid match"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_board_pid_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    cycle = mech.slots[0].board_cycles[0]
    assert cycle.dir_name == "20260102T235000-20260103T010000"
    assert cycle.start_time == datetime(2026, 1, 2, 23, 50, 0, tzinfo=timezone(timedelta(hours=8)))
    assert cycle.end_time == _ts(1)
    assert cycle.processes[0].logs[0].context == "early pid match"


def test_module2_board_entry_after_module1_window_uses_pid_match_and_expands_end(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T02:10:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc[100],Context="late pid match"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_board_pid_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    cycle = mech.slots[0].board_cycles[0]
    assert cycle.dir_name == "20260103T000000-20260103T021000"
    assert cycle.start_time == _ts(0)
    assert cycle.end_time == _ts(2, 10)
    assert cycle.processes[0].logs[0].context == "late pid match"


def test_module2_cpu_entry_matches_nested_cpu_cycle_before_board_cycle(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:06:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="nested cpu cycle"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_nested_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    board_cycle = mech.slots[0].board_cycles[0]
    assert board_cycle.dir_name == "20260103T000000-20260103T001000"
    assert board_cycle.processes == []
    assert len(board_cycle.cpu_cycles) == 1
    cpu_cycle = board_cycle.cpu_cycles[0]
    assert cpu_cycle.cpu_id == "3"
    assert cpu_cycle.dir_name == "20260103T000500-20260103T000700"
    assert cpu_cycle.processes[0].logs[0].context == "nested cpu cycle"


def test_module2_cpu_entry_after_module1_cpu_window_uses_pid_match_and_expands_cycles(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:12:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="late nested pid match"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_nested_pid_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    board_cycle = mech.slots[0].board_cycles[0]
    assert board_cycle.dir_name == "20260103T000000-20260103T001200"
    assert board_cycle.end_time == _ts(0, 12)
    cpu_cycle = board_cycle.cpu_cycles[0]
    assert cpu_cycle.cpu_id == "3"
    assert cpu_cycle.dir_name == "20260103T000500-20260103T001200"
    assert cpu_cycle.end_time == _ts(0, 12)
    assert cpu_cycle.processes[0].logs[0].context == "late nested pid match"


def test_module2_reused_pid_uses_timestamp_to_choose_matching_cycle(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T01:05:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc[100],Context="second pid generation"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_reused_pid_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    cycle = mech.slots[0].board_cycles[0]
    assert cycle.dir_name == "20260103T010000-20260103T011000"
    assert cycle.processes[0].logs[0].context == "second pid generation"


def test_module2_timestamp_inside_cycle_overrides_pid_from_other_cycle(tmp_path, caplog):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T01:05:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc[100],Context="time cycle wins over old pid"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_pid_then_unrelated_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    with caplog.at_level(logging.DEBUG, logger="backend.plugins.mechanisms.module2"):
        mech = plugin.parse(result)

    assert mech is not None
    assert [cycle.dir_name for cycle in mech.slots[0].board_cycles] == [
        "20260103T010000-20260103T011000"
    ]
    cycle = mech.slots[0].board_cycles[0]
    assert cycle.processes[0].process_name == "svc"
    assert cycle.processes[0].logs[0].context == "time cycle wins over old pid"
    assert "pid_fallback_blocked_by_time_cycle=true" in caplog.text
    assert "归属到unknown" not in caplog.text


def test_module2_pid_fallback_does_not_cross_adjacent_module1_cycle(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T01:20:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc[100],Context="far old pid should stay unknown"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_pid_then_unrelated_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    assert [cycle.dir_name for cycle in mech.slots[0].board_cycles] == ["unknown"]
    assert mech.slots[0].board_cycles[0].processes[0].logs[0].context == (
        "far old pid should stay unknown"
    )


def test_module2_gap_pid_fallback_uses_nearest_adjacent_cycle_and_avoids_overlap(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:50:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=left[100],Context="left pid too close to right cycle"\n'
        '2026-01-03T00:20:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=right[200],Context="right pid too close to left cycle"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_two_pid_expansion_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    assert [cycle.dir_name for cycle in mech.slots[0].board_cycles] == ["unknown"]
    contexts = [
        log.context
        for process in mech.slots[0].board_cycles[0].processes
        for log in process.logs
    ]
    assert sorted(contexts) == [
        "left pid too close to right cycle",
        "right pid too close to left cycle",
    ]


def test_module2_cpu_timestamp_inside_cycle_overrides_pid_from_other_cpu_cycle(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T01:05:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=svc[100],Context="cpu time cycle wins over old pid"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_nested_pid_then_unrelated_cpu_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    board_cycle = mech.slots[0].board_cycles[0]
    assert board_cycle.dir_name == "20260103T000000-20260103T020000"
    assert len(board_cycle.cpu_cycles) == 1
    cpu_cycle = board_cycle.cpu_cycles[0]
    assert cpu_cycle.dir_name == "20260103T010000-20260103T011000"
    assert cpu_cycle.processes[0].logs[0].context == "cpu time cycle wins over old pid"


def test_module2_entry_without_timestamp_stays_unknown_even_when_pid_matches(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        'xxx Slot=2,CPU-Id=0,ProcessName=svc[100],Context="no timestamp"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_board_pid_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    cycle = mech.slots[0].board_cycles[0]
    assert cycle.dir_name == "unknown"
    assert cycle.processes[0].logs[0].context == "no timestamp"


def test_module2_missing_dependency_records_error(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:10:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="xxxxx"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(diagnostic_slots=[slot])
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is None
    assert any("depends_on_module='module1' result not found" in e for e in result.errors)


def test_module2_output_uses_existing_mech_layout(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:10:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="xxxxx"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)
    assert mech is not None
    mech_dir = MechOutputWriter().write(mech, tmp_path / "output")

    out_file = (
        mech_dir
        / "slot_2"
        / safe_path_segment("20260103T000000-20260103T010000")
        / "cpu_3"
        / safe_path_segment("unknown")
        / safe_log_filename("hellokitty", "123")
    )
    assert out_file.is_file()
    assert "Context=\"xxxxx\"" in out_file.read_text(encoding="utf-8")


def test_module2_unknown_output_uses_existing_mech_layout(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T02:10:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="outside cycle"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)
    assert mech is not None
    mech_dir = MechOutputWriter().write(mech, tmp_path / "output")

    out_file = (
        mech_dir / "slot_2" / safe_path_segment("unknown") / "cpu_3" / safe_path_segment("unknown")
        / safe_log_filename("hellokitty", "123")
    )
    assert out_file.is_file()
    assert "outside cycle" in out_file.read_text(encoding="utf-8")


def test_module2_empty_cpu_id_is_board_level(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:10:00+08:00 xxx Slot=2,CPU-Id=,'
        'ProcessName=other[999],Context="empty cpu id"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    proc = mech.slots[0].board_cycles[0].processes[0]
    assert proc.logs[0].cpu_id == ""
    assert proc.logs[0].context == "empty cpu id"


def test_module2_logs_unknown_reason_when_no_board_cycle_contains_timestamp(tmp_path, caplog):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T02:10:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=other[999],Context="outside board cycle"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    with caplog.at_level(logging.INFO, logger="backend.plugins.mechanisms.module2"):
        mech = plugin.parse(result)

    assert mech is not None
    assert "归属到unknown" in caplog.text
    assert "reason=no_board_cycle_contains_timestamp" in caplog.text
    assert "slot=2" in caplog.text
    assert "cpu=<board>" in caplog.text
    assert "process=other" in caplog.text
    assert "pid=999" in caplog.text
    assert "timestamp=2026-01-03T02:10:00+08:00" in caplog.text
    assert "source=slot_2/diag.log" in caplog.text
    assert "board_cycles=1" in caplog.text
    assert "20260103T000000-20260103T010000" in caplog.text
    assert "projected_target_count=0" in caplog.text
    assert "raw=\"2026-01-03T02:10:00+08:00 xxx Slot=2" in caplog.text


def test_module2_logs_unknown_reason_when_upstream_slot_missing(tmp_path, caplog):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:10:00+08:00 xxx Slot=9,CPU-Id=0,'
        'ProcessName=other[999],Context="unknown slot"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="9", name="slot_9", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    with caplog.at_level(logging.INFO, logger="backend.plugins.mechanisms.module2"):
        mech = plugin.parse(result)

    assert mech is not None
    assert "reason=no_upstream_slot" in caplog.text
    assert "slot=9" in caplog.text
    assert "available_slots=[2]" in caplog.text


def test_module2_logs_unknown_reason_when_timestamp_missing(tmp_path, caplog):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        'xxx Slot=2,CPU-Id=0,ProcessName=svc[100],Context="no timestamp"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_board_pid_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    with caplog.at_level(logging.INFO, logger="backend.plugins.mechanisms.module2"):
        mech = plugin.parse(result)

    assert mech is not None
    assert "reason=missing_timestamp" in caplog.text
    assert "timestamp=<none>" in caplog.text
    assert "process=svc" in caplog.text
    assert "pid=100" in caplog.text


def test_module2_does_not_log_unknown_reason_for_successful_lifecycle_match(tmp_path, caplog):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:10:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc[100],Context="inside cycle"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    with caplog.at_level(logging.INFO, logger="backend.plugins.mechanisms.module2"):
        mech = plugin.parse(result)

    assert mech is not None
    assert "归属到unknown" not in caplog.text


def test_module2_merges_unknown_entries_into_unique_same_process_lifecycle(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:10:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="inside cycle"\n'
        '2026-01-03T02:10:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="outside cycle"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    assert [cycle.dir_name for cycle in mech.slots[0].board_cycles] == [
        "20260103T000000-20260103T021000"
    ]
    cpu_cycle = mech.slots[0].board_cycles[0].cpu_cycles[0]
    proc = cpu_cycle.processes[0]
    assert proc.process_name == "hellokitty"
    assert proc.pid == "123"
    assert [log.context for log in proc.logs] == ["inside cycle", "outside cycle"]

    mech_dir = MechOutputWriter().write(mech, tmp_path / "output")
    merged_file = (
        mech_dir
        / "slot_2"
        / safe_path_segment("20260103T000000-20260103T021000")
        / "cpu_3"
        / safe_path_segment("unknown")
        / safe_log_filename("hellokitty", "123")
    )
    unknown_file = (
        mech_dir / "slot_2" / safe_path_segment("unknown") / "cpu_3" / safe_path_segment("unknown")
        / safe_log_filename("hellokitty", "123")
    )
    assert merged_file.is_file()
    assert "inside cycle" in merged_file.read_text(encoding="utf-8")
    assert "outside cycle" in merged_file.read_text(encoding="utf-8")
    assert not unknown_file.exists()


def test_module2_known_unknown_merge_reuses_target_range_cache(monkeypatch):
    board_cycle = MechBoardCycle(
        dir_name="20260103T000000-20260103T010000",
        start_time=_ts(0),
        end_time=_ts(1),
    )
    known_cpu_cycle = MechCpuCycle(
        cpu_id="3",
        dir_name="20260103T000000-20260103T001000",
        start_time=_ts(0),
        end_time=_ts(0, 10),
    )
    board_cycle.cpu_cycles.append(known_cpu_cycle)
    unknown_cpu_cycle = MechCpuCycle(
        cpu_id="3",
        dir_name="unknown",
        start_time=board_cycle.start_time,
        end_time=board_cycle.end_time,
    )
    known_entries = [
        MechLogEntry(
            timestamp=_ts(0, 5),
            slot="2",
            cpu_id="3",
            process_name="svc",
            pid="100",
            context="known",
        )
    ]
    unknown_entries = [
        MechLogEntry(
            timestamp=_ts(0, 11),
            slot="2",
            cpu_id="3",
            process_name="svc",
            pid="100",
            context="unknown 1",
        ),
        MechLogEntry(
            timestamp=_ts(0, 12),
            slot="2",
            cpu_id="3",
            process_name="svc",
            pid="100",
            context="unknown 2",
        ),
    ]
    buckets = [
        (board_cycle, known_cpu_cycle, known_entries),
        (board_cycle, unknown_cpu_cycle, unknown_entries),
    ]
    upstream_slot = MechSlotOutput(slot_id="2", board_cycles=[board_cycle])
    projected_bounds_calls = 0
    original_projected_bounds = module2_impl._projected_bounds

    def counting_projected_bounds(*args, **kwargs):
        nonlocal projected_bounds_calls
        projected_bounds_calls += 1
        return original_projected_bounds(*args, **kwargs)

    monkeypatch.setattr(module2_impl, "_projected_bounds", counting_projected_bounds)

    module2_impl._merge_unknown_entries_into_unique_known_bucket(
        buckets,
        {},
        upstream_slot,
        module_key="module2",
    )

    assert [entry.context for entry in known_entries] == ["known", "unknown 1", "unknown 2"]
    assert unknown_entries == []
    assert projected_bounds_calls <= 2


def test_module2_does_not_log_unknown_reason_after_successful_unknown_merge(tmp_path, caplog):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:06:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="inside cycle"\n'
        '2026-01-03T00:12:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="outside cycle"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_nested_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    with caplog.at_level(logging.INFO, logger="backend.plugins.mechanisms.module2"):
        mech = plugin.parse(result)

    assert mech is not None
    assert "归属到unknown" not in caplog.text


def test_module2_resolves_same_process_unknown_to_nearest_later_lifecycle(tmp_path, caplog):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:05:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc,Context="first cycle"\n'
        '2026-01-03T01:05:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc,Context="second cycle"\n'
        '2026-01-03T02:00:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc,Context="ambiguous outside"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_reused_pid_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    with caplog.at_level(logging.INFO, logger="backend.plugins.mechanisms.module2"):
        mech = plugin.parse(result)

    assert mech is not None
    cycles = {cycle.dir_name: cycle for cycle in mech.slots[0].board_cycles}
    assert sorted(cycles) == [
        "20260103T000000-20260103T001000",
        "20260103T010000-20260103T020000",
    ]
    later_contexts = [
        log.context
        for process in cycles["20260103T010000-20260103T020000"].processes
        for log in process.logs
    ]
    assert later_contexts == ["second cycle", "ambiguous outside"]
    assert "resolved_by_nearest_time=1" in caplog.text
    assert "resolved_unknown_by_nearest_time=true" not in caplog.text
    assert "归属到unknown" not in caplog.text


def test_module2_resolves_same_process_unknown_to_nearest_earlier_lifecycle(tmp_path, caplog):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:05:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc,Context="first cycle"\n'
        '2026-01-03T01:05:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc,Context="second cycle"\n'
        '2026-01-03T00:20:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc,Context="ambiguous outside nearer first"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_reused_pid_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    with caplog.at_level(logging.INFO, logger="backend.plugins.mechanisms.module2"):
        mech = plugin.parse(result)

    assert mech is not None
    cycles = {cycle.dir_name: cycle for cycle in mech.slots[0].board_cycles}
    assert sorted(cycles) == [
        "20260103T000000-20260103T002000",
        "20260103T010000-20260103T011000",
    ]
    earlier_contexts = [
        log.context
        for process in cycles["20260103T000000-20260103T002000"].processes
        for log in process.logs
    ]
    assert earlier_contexts == ["first cycle", "ambiguous outside nearer first"]
    assert "resolved_by_nearest_time=1" in caplog.text
    assert "resolved_unknown_by_nearest_time=true" not in caplog.text
    assert "归属到unknown" not in caplog.text


def test_module2_keeps_unknown_when_nearest_time_candidate_ties():
    entry = MechLogEntry(timestamp=_ts(0, 15), process_name="svc")

    resolution = module2_impl._resolve_candidate_by_nearest_time(
        entry,
        ["left", "right"],
        range_getter=lambda candidate: (
            (_ts(0), _ts(0, 10)) if candidate == "left" else (_ts(0, 20), _ts(0, 30))
        ),
        admissible_range_getter=lambda _candidate: (None, None),
        summary_formatter=lambda candidate: candidate,
    )

    assert resolution.target is None
    assert resolution.target_count == 2
    assert resolution.admissible_count == 2
    assert resolution.tie
    assert "distance=300.000000" in resolution.detail


def test_module2_logs_unknown_reason_when_unknown_merge_target_ties(tmp_path, caplog):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:05:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc,Context="first cycle"\n'
        '2026-01-03T01:05:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc,Context="second cycle"\n'
        'xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc,Context="ambiguous without timestamp"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_reused_pid_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    with caplog.at_level(logging.INFO, logger="backend.plugins.mechanisms.module2"):
        mech = plugin.parse(result)

    assert mech is not None
    assert "reason=no_unique_known_process_target" in caplog.text
    assert "original_reason=missing_timestamp" in caplog.text
    assert "target_count=2" in caplog.text
    assert "admissible_count=0" in caplog.text
    assert "candidates=[" in caplog.text
    assert "ambiguous without timestamp" in caplog.text


def test_module2_merges_top_level_unknown_into_unique_projected_board_cycle(tmp_path, caplog):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T02:10:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc[100],Context="late pid extender"\n'
        '2026-01-03T01:30:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=other[999],Context="between projected bounds"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_board_pid_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    with caplog.at_level(logging.INFO, logger="backend.plugins.mechanisms.module2"):
        mech = plugin.parse(result)

    assert mech is not None
    cycles = mech.slots[0].board_cycles
    assert [cycle.dir_name for cycle in cycles] == ["20260103T000000-20260103T021000"]
    processes = {
        (process.process_name, process.pid): process
        for process in cycles[0].processes
    }
    assert sorted(processes) == [("other", "999"), ("svc", "100")]
    assert [log.context for log in processes[("other", "999")].logs] == [
        "between projected bounds"
    ]
    assert "归属到unknown" not in caplog.text


def test_module2_resolves_projected_unknown_to_nearest_admissible_target(tmp_path, caplog):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:20:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=left[100],Context="left extender"\n'
        '2026-01-03T00:50:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=right[200],Context="right extender"\n'
        '2026-01-03T00:35:00+08:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=other[999],Context="between clamped projected targets"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_two_pid_expansion_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    with caplog.at_level(logging.INFO, logger="backend.plugins.mechanisms.module2"):
        mech = plugin.parse(result)

    assert mech is not None
    cycles = {cycle.dir_name: cycle for cycle in mech.slots[0].board_cycles}
    assert sorted(cycles) == [
        "20260103T000000-20260103T003500",
        "20260103T005000-20260103T011000",
    ]
    left_contexts = [
        log.context
        for process in cycles["20260103T000000-20260103T003500"].processes
        for log in process.logs
    ]
    assert sorted(left_contexts) == ["between clamped projected targets", "left extender"]
    assert "resolved_by_nearest_time=1" in caplog.text
    assert "projected=1" in caplog.text
    assert "resolved_unknown_by_nearest_time=true" not in caplog.text
    assert "归属到unknown" not in caplog.text


def test_module2_preserves_slot_from_slash_format(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:10:00+08:00 xxx Slot=1/2,CPU-Id=0,'
        'ProcessName=hellocat[456],Context="slash slot"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    assert mech.slots[0].slot_id == "1/2"
    cycle = mech.slots[0].board_cycles[0]
    assert cycle.dir_name == "unknown"
    proc = cycle.processes[0]
    assert proc.process_name == "hellocat"
    assert proc.pid == "456"
    assert proc.logs[0].cpu_id == ""
    assert proc.logs[0].context == "slash slot"

    mech_dir = MechOutputWriter().write(mech, tmp_path / "output")
    assert (
        mech_dir / f"slot_{safe_path_segment('1/2')}" / safe_path_segment("unknown")
        / safe_log_filename("hellocat", "456")
    ).is_file()
    assert not (mech_dir / "slot_1" / "2").exists()


def test_module2_logs_when_no_diagnostic_entries_found(tmp_path, caplog):
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    with caplog.at_level(logging.INFO, logger="backend.plugins.mechanisms.module2"):
        mech = plugin.parse(result)

    assert mech is None
    assert "未扫描到诊断日志条目" in caplog.text
    assert "xxx" in caplog.text


def test_module2_logs_when_dependency_not_found(tmp_path, caplog):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:10:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="xxxxx"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(diagnostic_slots=[slot])
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    with caplog.at_level(logging.WARNING, logger="backend.plugins.mechanisms.module2"):
        mech = plugin.parse(result)

    assert mech is None
    assert "依赖未找到" in caplog.text


def test_module2_logs_when_config_invalid(caplog):
    plugin = Module2Plugin(
        {},
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )
    result = ParseResult()

    with caplog.at_level(logging.WARNING, logger="backend.plugins.mechanisms.module2"):
        mech = plugin.parse(result)

    assert mech is None
    assert "配置校验失败" in caplog.text


def test_module2_normalizes_naive_timestamps_to_match_aware_cycles(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:10:00 xxx Slot=2,CPU-Id=0,'
        'ProcessName=svc[100],Context="no tz"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    cycle = mech.slots[0].board_cycles[0]
    assert cycle.dir_name == "20260103T000000-20260103T010000"
    assert cycle.processes[0].logs[0].timestamp is not None
    assert cycle.processes[0].logs[0].timestamp.tzinfo is not None
