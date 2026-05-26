"""Shared pytest fixtures."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from backend.models import (
    ActivePeriod,
    BoardRole,
    LogEntry,
    MechLogEntry,
    ParseResult,
    PrivateSlotInfo,
    SlotInfo,
)


@pytest.fixture
def sample_config() -> dict:
    """Minimal parser config matching config.yaml structure."""
    return {
        "timestamp_regex": r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?",
        "active_period_gap_threshold": 300,
        "mechanism_modules": {
            "module1": {
                "plugin": "backend.plugins.mechanisms.module1.Module1Plugin",
                "enabled": True,
                "config": {
                    "module_name": "EXAMPLE",
                    "diag_pattern": r"Service=(?P<Service>[^;]+).*?Slot=(?P<Slot>[^;,)]+).*?CPU-Id=(?P<CPU_Id>[^;,)]+).*?ProcessName=(?P<ProcessName>[^;,)]+).*?Context=(?P<Context>.+?)\)$",
                    "active_master_keyword": "MASTER_ACTIVE",
                    "board_restart_indicator": "dhcp",
                    "process_name_mapping": {},
                    "journal": {
                        "line_pattern": r"^\S+\s+\S+\s+\S+?:\s+\[slotId\s*=\s*\d+,\s*cpuId\s*=\s*\d+,\s*processName\s*=\s*(\S+?)-(\d+)\]:\s+No\[(\d+)\](.+)$",
                        "line_pattern2": r"^\S+\s+\S+\s+(\S+?)(?:-(\d+))?:\s+No\[(\d+)\](.+)$",
                        "identifying_keyword": "EXAMPLE",
                    },
                    "sequence_pattern": r"No\[(\d+)\]",
                },
            }
        },
    }


@pytest.fixture
def sample_slot() -> SlotInfo:
    """A slot with two diagnostic log entries."""
    slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp/slot_1")
    slot.add_diagnostic_log(LogEntry(
        path="/tmp/slot_1/diag.zip", name="diag.zip",
        size_bytes=1024, compressed=True,
    ))
    slot.add_diagnostic_log(LogEntry(
        path="/tmp/slot_1/diaglog_1_20260103000000.log.zip",
        name="diaglog_1_20260103000000.log.zip",
        size_bytes=2048, compressed=True,
    ))
    return slot


@pytest.fixture
def sample_parse_result(sample_slot) -> ParseResult:
    """Minimal ParseResult with one diagnostic slot."""
    return ParseResult(
        task_id="test_task",
        package_name="test.zip",
        extracted_root="/tmp/extracted",
        diagnostic_slots=[sample_slot],
        private_slots=[],
    )


@pytest.fixture
def tz_east8() -> timezone:
    return timezone(timedelta(hours=8))


@pytest.fixture
def sample_mech_entries(tz_east8) -> list[MechLogEntry]:
    """10 journal entries from a single process with a PID change at index 5."""
    entries = []
    base = datetime(2026, 1, 3, 0, 0, 0, tzinfo=tz_east8)
    # First lifecycle: dhcp PID=100, seq 1-5
    for i in range(1, 6):
        entries.append(MechLogEntry(
            timestamp=base + timedelta(minutes=i),
            source="journal", source_file="slot_1/journal.log",
            slot="1", cpu_id="",
            process_name="dhcp", pid="100",
            context="EXAMPLE msg", sequence=i,
            raw=f"Jan  3 00:{i:02d}:00 dhcp-100: No[{i}] EXAMPLE msg",
        ))
    # Second lifecycle: dhcp PID=200 (restart), seq 1-5
    for i in range(1, 6):
        entries.append(MechLogEntry(
            timestamp=base + timedelta(hours=1, minutes=i),
            source="journal", source_file="slot_1/journal.log",
            slot="1", cpu_id="",
            process_name="dhcp", pid="200",
            context="EXAMPLE msg", sequence=i,
            raw=f"Jan  3 01:{i:02d}:00 dhcp-200: No[{i}] EXAMPLE msg",
        ))
    return entries
