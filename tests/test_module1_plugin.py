from __future__ import annotations

import re

from backend.models import (
    BoardRole,
    JournalLogFile,
    LogEntry,
    MechResult,
    ParseResult,
    PrivateSlotInfo,
    SlotInfo,
)
from backend.parsing.timestamp_extractor import TimestampExtractor
from backend.plugins.mechanisms.module1 import Module1Plugin


def _module1_config() -> dict:
    return {
        "module_name": "EXAMPLE",
        "diag_pattern": (
            r"Service=(?P<Service>[^;]+).*?Slot=(?P<Slot>[^;,)]+).*?"
            r"CPU-Id=(?P<CPU_Id>[^;,)]+).*?"
            r"ProcessName=(?P<ProcessName>[^;,)]+).*?"
            r"Context=(?P<Context>.+?)\)$"
        ),
        "active_master_keyword": "ACTIVE",
        "board_restart_indicator": "",
        "board_restart_whitelist": [],
        "process_name_mapping": {},
        "journal": {
            "line_pattern": "",
            "line_pattern2": "",
            "identifying_keyword": "EXAMPLE",
        },
        "sequence_pattern": r"No\[(\d+)\]",
    }


def _timestamp_extractor() -> TimestampExtractor:
    return TimestampExtractor(
        re.compile(r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?")
    )


def test_module1_plugin_parses_diag_entries(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        "2026-01-03T00:01:00 EXAMPLE Service=SERVICE; Slot=1; CPU-Id=0; "
        "ProcessName=SERVICE-12345; Context=No[1] ACTIVE)\n",
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="1", name="slot_1", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(diagnostic_slots=[slot])
    plugin = Module1Plugin(
        _module1_config(),
        module_key="module1",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    assert mech.module_name == "EXAMPLE"
    assert mech.diag_entry_count == 1
    assert mech.active_master_slots == ["1"]
    assert mech.slots[0].board_cycles[0].processes[0].process_name == "SERVICE"


def _module1_journal_no_sequence_config() -> dict:
    cfg = _module1_config()
    cfg["diag_pattern"] = ""
    cfg["journal"] = {
        "line_pattern": "",
        "line_pattern2": r"^\S+\s+\S+\s+(\S+?)(?:-(\d+))?:\s+(.+)$",
        "identifying_keyword": "example",
    }
    return cfg


def test_module1_plugin_parses_journal_entries_without_no(tmp_path):
    journal_file = tmp_path / "journal.log"
    journal_file.write_text(
        "2026-01-03T00:01:00 host SERVICE-12345: EXAMPLE started without sequence\n",
        encoding="utf-8",
    )
    private_slot = PrivateSlotInfo(
        dir_name="slot_1",
        slot_id="1",
        path=str(tmp_path),
        journal_logs=[
            JournalLogFile(path=str(journal_file), name="journal.log"),
        ],
    )
    result = ParseResult(private_slots=[private_slot])
    plugin = Module1Plugin(
        _module1_journal_no_sequence_config(),
        module_key="module1",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    assert mech.journal_entry_count == 1
    proc = mech.slots[0].board_cycles[0].processes[0]
    assert proc.process_name == "SERVICE"
    assert proc.pid == "12345"
    assert proc.logs[0].sequence == 0
    assert proc.logs[0].context == "EXAMPLE started without sequence"


def test_module1_plugin_parses_diag_entries_without_no(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        "2026-01-03T00:01:00 EXAMPLE Service=SERVICE; Slot=1; CPU-Id=0; "
        "ProcessName=SERVICE-12345; Context=ACTIVE without sequence)\n",
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="1", name="slot_1", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(diagnostic_slots=[slot])
    plugin = Module1Plugin(
        _module1_config(),
        module_key="module1",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    cycle = mech.slots[0].board_cycles[0]
    proc = cycle.processes[0]
    assert proc.process_name == "SERVICE"
    assert proc.logs[0].sequence == 0
    assert proc.missing_sequences == []
    assert mech.active_master_slots == ["1"]


def _module1_journal_sequence_config() -> dict:
    cfg = _module1_config()
    cfg["diag_pattern"] = ""
    cfg["journal"] = {
        "line_pattern": "",
        "line_pattern2": r"^\S+\s+\S+\s+(\S+?)(?:-(\d+))?:\s+No\[(\d+)\](.+)$",
        "identifying_keyword": "example",
    }
    return cfg


def test_module1_plugin_auto_parses_journal_entries_without_no_from_sequence_config(tmp_path):
    journal_file = tmp_path / "journal.log"
    journal_file.write_text(
        "2026-01-03T00:01:00 host SERVICE-12345: EXAMPLE old version without sequence\n",
        encoding="utf-8",
    )
    private_slot = PrivateSlotInfo(
        dir_name="slot_1",
        slot_id="1",
        path=str(tmp_path),
        journal_logs=[
            JournalLogFile(path=str(journal_file), name="journal.log"),
        ],
    )
    result = ParseResult(private_slots=[private_slot])
    plugin = Module1Plugin(
        _module1_journal_sequence_config(),
        module_key="module1",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    assert mech.journal_entry_count == 1
    proc = mech.slots[0].board_cycles[0].processes[0]
    assert proc.process_name == "SERVICE"
    assert proc.pid == "12345"
    assert proc.logs[0].sequence == 0
    assert proc.logs[0].context == "EXAMPLE old version without sequence"


def test_module1_plugin_applies_roles(sample_parse_result):
    plugin = Module1Plugin(_module1_config(), module_key="module1", ts_extractor=None)
    mech = MechResult(module_name="EXAMPLE", active_master_slots=["1"])

    plugin.apply_roles(sample_parse_result, mech)

    assert sample_parse_result.diagnostic_slots[0].role == BoardRole.ACTIVE
