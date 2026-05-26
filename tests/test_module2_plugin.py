from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from backend.models import (
    LogEntry,
    MechBoardCycle,
    MechResult,
    MechSlotOutput,
    ParseResult,
    SlotInfo,
)
from backend.parsing.timestamp_extractor import TimestampExtractor
from backend.parsing.output_writer import MechOutputWriter
from backend.plugins.mechanisms.module2 import Module2Plugin


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
            r"Slot=(?P<Slot>[\d/]+),CPU-Id=(?P<CPU_Id>\d+),"
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
    proc = cycle.processes[0]
    assert proc.process_name == "hellokitty"
    assert proc.pid == "123"
    assert proc.logs[0].cpu_id == "3"
    assert proc.logs[0].context == "xxxxx"


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
    assert cycle.processes[0].logs[0].context == "outside cycle"


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
        / "20260103T000000-20260103T010000"
        / "cpu_3"
        / "hellokitty-123.log"
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

    out_file = mech_dir / "slot_2" / "unknown" / "cpu_3" / "hellokitty-123.log"
    assert out_file.is_file()
    assert "outside cycle" in out_file.read_text(encoding="utf-8")


def test_module2_extracts_slot_from_slash_format(tmp_path):
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
    assert mech.slots[0].slot_id == "2"
    cycle = mech.slots[0].board_cycles[0]
    assert cycle.dir_name == "20260103T000000-20260103T010000"
    proc = cycle.processes[0]
    assert proc.process_name == "hellocat"
    assert proc.pid == "456"
    assert proc.logs[0].cpu_id == ""
    assert proc.logs[0].context == "slash slot"
