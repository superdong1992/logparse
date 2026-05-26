"""Tests for ParserPlugin: timestamps, ActivePeriod, cycle detection, role identification."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.models import (
    ActivePeriod,
    BoardRole,
    LogEntry,
    MechBoardCycle,
    MechLogEntry,
    MechProcessLifecycle,
    MechResult,
    ParseResult,
    PrivateSlotInfo,
    SlotInfo,
)
from backend.plugins.default.parser import ParserPlugin
from backend.parsing.cycle_detector import CycleDetector
from backend.parsing.role_identifier import RoleIdentifier


@pytest.fixture
def plugin(sample_config):
    return ParserPlugin(sample_config)


class TestExtractContentTimestamps:
    def test_with_tz(self, plugin):
        stamps = plugin._ts_extractor.extract_from_text(
            "2026-01-03T00:01:00.100000+08:00 EXAMPLE msg"
        )
        assert len(stamps) == 1
        assert stamps[0].tzinfo is not None

    def test_without_tz(self, plugin):
        stamps = plugin._ts_extractor.extract_from_text(
            "2026-01-03T00:01:00 EXAMPLE msg"
        )
        assert len(stamps) == 1
        assert stamps[0].tzinfo is None

    def test_empty(self, plugin):
        assert plugin._ts_extractor.extract_from_text("no timestamp here") == []


class TestNestedMechanismConfig:
    def test_nested_sample_config_does_not_report_missing_module_name(self, sample_config):
        result = ParserPlugin(sample_config).parse(ParseResult())

        assert not any("module_name" in error for error in result.errors)


class TestBuildActivePeriods:
    def test_single_period(self, plugin):
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        base = datetime(2026, 1, 3, 0, 0, 0)
        entry = LogEntry(path="/tmp/f", name="f.log", size_bytes=100)
        entry.content_timestamps = [base + timedelta(minutes=i) for i in range(5)]
        slot.add_diagnostic_log(entry)

        periods = plugin._active_period_builder.build(slot)
        assert len(periods) == 1
        assert periods[0].start == base
        assert periods[0].end == base + timedelta(minutes=4)

    def test_gap_creates_two_periods(self, plugin):
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        base = datetime(2026, 1, 3, 0, 0, 0)
        entry = LogEntry(path="/tmp/f", name="f.log", size_bytes=100)
        entry.content_timestamps = [
            base,
            base + timedelta(minutes=4),
            base + timedelta(minutes=14),
            base + timedelta(minutes=18),
        ]
        slot.add_diagnostic_log(entry)

        periods = plugin._active_period_builder.build(slot)
        assert len(periods) == 2

    def test_empty_slot(self, plugin):
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        assert plugin._active_period_builder.build(slot) == []


class TestParseDiagProcName:
    def test_simple_name_with_pid(self, plugin):
        from backend.parsing.process_name_resolver import ProcessNameResolver
        resolver = ProcessNameResolver()
        assert resolver.parse_diag_process_name("SERVICE-12345") == ("SERVICE", "12345")

    def test_name_only(self, plugin):
        from backend.parsing.process_name_resolver import ProcessNameResolver
        resolver = ProcessNameResolver()
        assert resolver.parse_diag_process_name("SERVICE") == ("SERVICE", "")

    def test_name_mapping(self, plugin):
        from backend.parsing.process_name_resolver import ProcessNameResolver
        resolver = ProcessNameResolver(name_map={"DHCP": "dhcpd"})
        assert resolver.parse_diag_process_name("DHCP-9881") == ("DHCP", "9881")

    def test_non_numeric_suffix(self, plugin):
        from backend.parsing.process_name_resolver import ProcessNameResolver
        resolver = ProcessNameResolver()
        assert resolver.parse_diag_process_name("SERVICE-abc") == ("SERVICE-abc", "")


class TestBuildProcesses:
    def test_single_process(self, sample_mech_entries):
        procs = CycleDetector._build_processes(sample_mech_entries[:5])
        assert len(procs) == 1
        assert procs[0].process_name == "dhcp"
        assert procs[0].pid == "100"
        assert procs[0].total_count == 5

    def test_missing_sequences(self):
        entries = [
            MechLogEntry(process_name="svc", pid="1", sequence=i, raw=f"line{i}")
            for i in [1, 2, 4, 5, 8]
        ]
        procs = CycleDetector._build_processes(entries)
        assert len(procs) == 1
        assert procs[0].missing_sequences == [3, 6, 7]

    def test_grouped_by_name_and_pid(self):
        entries = [
            MechLogEntry(process_name="svc", pid="1", sequence=1, raw="a"),
            MechLogEntry(process_name="svc", pid="2", sequence=1, raw="b"),
            MechLogEntry(process_name="other", pid="1", sequence=1, raw="c"),
        ]
        procs = CycleDetector._build_processes(entries)
        assert len(procs) == 3


class TestBuildCycles:
    def test_single_cycle_no_restart(self):
        entries = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0),
                source="journal", slot="1", cpu_id="",
                process_name="svc", pid="100", sequence=i,
                raw=f"line{i}",
            )
            for i in range(1, 6)
        ]
        detector = CycleDetector(indicator=None)
        cycles = detector.detect(entries)
        assert len(cycles) == 1

    def test_pid_change_creates_two_cycles(self):
        entries = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0),
                source="journal", slot="1", cpu_id="",
                process_name="dhcp", pid="100", sequence=i,
                raw=f"line{i}",
            )
            for i in range(1, 4)
        ] + [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 1, i, 0),
                source="journal", slot="1", cpu_id="",
                process_name="dhcp", pid="200", sequence=i,
                raw=f"line{i+10}",
            )
            for i in range(1, 4)
        ]
        detector = CycleDetector(indicator="dhcp")
        cycles = detector.detect(entries)
        assert len(cycles) == 2

    def test_cpu_subcard_isolation(self):
        board_entries = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0),
                source="journal", slot="1", cpu_id="",
                process_name="svc", pid="100", sequence=i,
                raw=f"board_line{i}",
            )
            for i in range(1, 6)
        ]
        cpu_entries = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0),
                source="journal", slot="1", cpu_id="1",
                process_name="dhcp", pid="50", sequence=i,
                raw=f"cpu_line{i}",
            )
            for i in range(1, 4)
        ] + [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 1, i, 0),
                source="journal", slot="1", cpu_id="1",
                process_name="dhcp", pid="60", sequence=i,
                raw=f"cpu_line2_{i}",
            )
            for i in range(1, 4)
        ]
        all_entries = board_entries + cpu_entries
        detector = CycleDetector(indicator="dhcp")
        cycles = detector.detect(all_entries)
        board_cycles = [c for c in cycles if any(
            p.pid == "100" for p in c.processes
        )]
        cpu_cycles = [c for c in cycles if any(
            p.pid in ("50", "60") for p in c.processes
        )]
        assert len(board_cycles) == 1
        assert len(cpu_cycles) == 2


class TestRoleIdentification:
    def test_mech_role_overrides(self, plugin, sample_parse_result):
        mech = MechResult(
            module_name="EXAMPLE",
            active_master_slots=["1"],
        )
        RoleIdentifier.apply_mech_roles(mech, sample_parse_result)
        assert sample_parse_result.diagnostic_slots[0].role == BoardRole.ACTIVE

    def test_fallback_active(self):
        result = ParseResult()
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        slot.add_active_period(ActivePeriod(
            start=datetime(2026, 1, 3, 0, 0),
            end=datetime(2026, 1, 3, 1, 0),
        ))
        result.diagnostic_slots.append(slot)
        RoleIdentifier.fallback_roles(result)
        assert slot.role == BoardRole.ACTIVE

    def test_fallback_standby(self):
        result = ParseResult()
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        slot.add_diagnostic_log(LogEntry(
            path="/tmp/f", name="f.log", size_bytes=100,
        ))
        result.diagnostic_slots.append(slot)
        RoleIdentifier.fallback_roles(result)
        assert slot.role == BoardRole.STANDBY

    def test_fallback_unknown(self):
        result = ParseResult()
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        result.diagnostic_slots.append(slot)
        RoleIdentifier.fallback_roles(result)
        assert slot.role == BoardRole.UNKNOWN


class TestFmtDir:
    def test_both_times(self):
        s = datetime(2026, 1, 3, 10, 37, 7)
        e = datetime(2026, 1, 3, 11, 37, 8)
        assert CycleDetector._fmt_dir(s, e) == "20260103T103707-20260103T113708"

    def test_start_only(self):
        assert CycleDetector._fmt_dir(datetime(2026, 1, 3, 0, 0, 0), None) == "20260103T000000"

    def test_none(self):
        assert CycleDetector._fmt_dir(None, None) == "unknown"
