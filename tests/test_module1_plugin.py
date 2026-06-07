from __future__ import annotations

import logging
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
            r"CPU-Id=(?P<CPU_Id>[^;,)]*).*?"
            r"ProcessName=(?P<ProcessName>[^;,)]+).*?"
            r"Context=(?P<Context>.+?)\)$"
        ),
        "active_master_keyword": "ACTIVE",
        "lifecycle_split": {
            "process_name_mapping": {},
            "reliable_processes": [],
            "multi_instance_processes": [],
        },
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
    assert mech.module_key == "module1"
    assert mech.diag_entry_count == 1
    assert mech.active_master_slots == ["1"]
    assert mech.slots[0].board_cycles[0].processes[0].process_name == "SERVICE"


def test_module1_empty_cpu_id_is_board_level(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        "2026-01-03T00:01:00 EXAMPLE Service=SERVICE; Slot=1; CPU-Id=; "
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
    proc = mech.slots[0].board_cycles[0].processes[0]
    assert proc.logs[0].cpu_id == ""
    assert proc.logs[0].context == "No[1] ACTIVE"


def test_module1_plugin_emits_perf_logs_with_elapsed(tmp_path, caplog):
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

    with caplog.at_level(logging.INFO, logger="backend.plugins.mechanisms.module1"):
        plugin.parse(result)

    assert "LOGPARSE_PERF module1.diag_scan module=module1 elapsed=" in caplog.text
    assert "LOGPARSE_PERF module1.journal_scan module=module1 elapsed=" in caplog.text
    assert "LOGPARSE_PERF module1.slot_cycle module=module1 slot=1 elapsed=" in caplog.text


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


def test_module1_plugin_filters_line_pattern2_by_required_substrings(tmp_path):
    journal_file = tmp_path / "journal.log"
    journal_file.write_text(
        "\n".join([
            "2026-01-03T00:01:00 host SERVICE-12345: EXAMPLE MODULE1 keep",
            "2026-01-03T00:02:00 host SERVICE-12345: EXAMPLE module1 lower",
            "2026-01-03T00:03:00 host SERVICE-12345: EXAMPLE other",
        ]) + "\n",
        encoding="utf-8",
    )
    private_slot = PrivateSlotInfo(
        dir_name="slot_1",
        slot_id="1",
        path=str(tmp_path),
        journal_logs=[JournalLogFile(path=str(journal_file), name="journal.log")],
    )
    result = ParseResult(private_slots=[private_slot])
    cfg = _module1_journal_no_sequence_config()
    cfg["journal"]["line_pattern2_required_substrings"] = ["MODULE1"]
    plugin = Module1Plugin(
        cfg,
        module_key="module1",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    assert mech.journal_entry_count == 1
    proc = mech.slots[0].board_cycles[0].processes[0]
    assert [log.context for log in proc.logs] == ["EXAMPLE MODULE1 keep"]


def test_module1_plugin_empty_line_pattern2_required_substrings_preserves_behavior(tmp_path):
    journal_file = tmp_path / "journal.log"
    journal_file.write_text(
        "2026-01-03T00:01:00 host SERVICE-12345: EXAMPLE no extra constraint\n",
        encoding="utf-8",
    )
    private_slot = PrivateSlotInfo(
        dir_name="slot_1",
        slot_id="1",
        path=str(tmp_path),
        journal_logs=[JournalLogFile(path=str(journal_file), name="journal.log")],
    )
    result = ParseResult(private_slots=[private_slot])
    cfg = _module1_journal_no_sequence_config()
    cfg["journal"]["line_pattern2_required_substrings"] = []
    plugin = Module1Plugin(
        cfg,
        module_key="module1",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    assert mech.journal_entry_count == 1


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


def test_module1_plugin_defaults_to_lifecycle_split_v3(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        "\n".join([
            "2026-01-03T00:00:00 EXAMPLE Service=S; Slot=1; CPU-Id=0; "
            "ProcessName=dhcp-100; Context=old)",
            "2026-01-03T00:01:00 EXAMPLE Service=S; Slot=1; CPU-Id=0; "
            "ProcessName=dhcp-200; Context=new)",
        ]) + "\n",
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="1", name="slot_1", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(diagnostic_slots=[slot])
    cfg = _module1_config()
    cfg["lifecycle_split"] = {
        "reliable_processes": ["dhcp"],
        "multi_instance_processes": [],
    }
    plugin = Module1Plugin(
        cfg,
        module_key="module1",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    slot_output = mech.slots[0]
    assert slot_output.lifecycle_split_result.algorithm == "interval_v3"
    assert len(slot_output.lifecycle_split_result.candidate_segments) == 2
    assert slot_output.lifecycle_split_result.merge_decisions[0].blocking_reason == "reliable_pid_conflict"
    assert len(slot_output.board_cycles) == 2
    assert not result.errors


def test_module1_plugin_v3_treats_cpu_id_zero_as_board_scope(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        "\n".join([
            "2026-01-03T00:00:00 EXAMPLE Service=S; Slot=1; CPU-Id=0; "
            "ProcessName=dhcp-100; Context=old)",
            "2026-01-03T01:00:00 EXAMPLE Service=S; Slot=1; CPU-Id=0; "
            "ProcessName=dhcp-200; Context=new)",
        ]) + "\n",
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="1", name="slot_1", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(diagnostic_slots=[slot])
    cfg = _module1_config()
    cfg["lifecycle_split"] = {
        "reliable_processes": ["dhcp"],
    }
    plugin = Module1Plugin(
        cfg,
        module_key="module1",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    split_result = mech.slots[0].lifecycle_split_result
    assert split_result is not None
    assert split_result.algorithm == "interval_v3"
    assert {lifecycle.scope for lifecycle in split_result.lifecycles} == {"board"}
    assert all(
        log.cpu_id == ""
        for cycle in mech.slots[0].board_cycles
        for process in cycle.processes
        for log in process.logs
    )


def test_module1_plugin_v3_ignores_no_pid_no_sequence_journal_for_reliable_process(tmp_path):
    diag_file = tmp_path / "diag.log"
    diag_file.write_text(
        "\n".join([
            "2026-01-03T00:00:00 EXAMPLE Service=S; Slot=1; CPU-Id=0; "
            "ProcessName=dhcp-100; Context=old)",
            "2026-01-03T01:00:00 EXAMPLE Service=S; Slot=1; CPU-Id=0; "
            "ProcessName=dhcp-200; Context=new)",
        ]) + "\n",
        encoding="utf-8",
    )
    journal_file = tmp_path / "journal.log"
    journal_file.write_text(
        "\n".join([
            "2026-01-03T00:10:00 host dhcp: EXAMPLE journal without pid",
            "2026-01-03T00:20:00 host dhcp: EXAMPLE another journal without pid",
        ]) + "\n",
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="1", name="slot_1", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(diag_file), name="diag.log"))
    private_slot = PrivateSlotInfo(
        dir_name="slot_1",
        slot_id="1",
        path=str(tmp_path),
        journal_logs=[JournalLogFile(path=str(journal_file), name="journal.log")],
    )
    result = ParseResult(diagnostic_slots=[slot], private_slots=[private_slot])
    cfg = _module1_config()
    cfg["journal"] = {
        "line_pattern": "",
        "line_pattern2": r"^\S+\s+\S+\s+(\S+?)(?:-(\d+))?:\s+(.+)$",
        "identifying_keyword": "example",
    }
    cfg["lifecycle_split"] = {
        "reliable_processes": ["dhcp"],
    }
    plugin = Module1Plugin(
        cfg,
        module_key="module1",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    slot_output = mech.slots[0]
    assert slot_output.lifecycle_reliable is True
    assert slot_output.lifecycle_split_result is not None
    assert slot_output.lifecycle_split_result.issues == []
    assert slot_output.lifecycle_split_result.algorithm == "interval_v3"
    assert mech.journal_entry_count == 2


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
