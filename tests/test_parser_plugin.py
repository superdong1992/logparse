"""Tests for ParserPlugin: timestamps, ActivePeriod, cycle detection, role identification."""
from __future__ import annotations

import logging
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
    MechSlotOutput,
    ParseResult,
    PrivateSlotInfo,
    SlotInfo,
)
from backend.performance import PerformanceRecorder
from backend.plugins.default.parser import ParserPlugin
from backend.plugins.mechanisms.base import MechanismModulePlugin
from backend.parsing.lifecycle_common import _build_process_lifecycles, _format_cycle_dir
from backend.parsing.role_identifier import RoleIdentifier


class ExplodingDiagnosticScannerPlugin(MechanismModulePlugin):
    def build_diagnostic_line_scanner(self):
        def _scanner(line, log_entry, slot_id):
            raise RuntimeError("scanner boom")

        return _scanner

    def parse(self, result):
        return None


class ExplodingDiagnosticScannerBuilderPlugin(MechanismModulePlugin):
    def build_diagnostic_line_scanner(self):
        raise RuntimeError("builder boom")

    def parse(self, result):
        return None


class RecoveringDiagnosticScannerPlugin(MechanismModulePlugin):
    def build_diagnostic_line_scanner(self):
        def _scanner(line, log_entry, slot_id):
            if "boom" in line:
                raise RuntimeError("recoverable scanner boom")
            if "RECOVER" not in line:
                return None
            return MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, 2),
                source="diagnostic",
                source_file=f"slot_{slot_id}/{log_entry.name}",
                slot=slot_id,
                cpu_id="",
                process_name="recover",
                pid="1",
                context="RECOVER",
                raw=line.strip(),
            )

        return _scanner

    def parse(self, result):
        entries = list(getattr(self, "_precomputed_diagnostic_entries", []))
        return MechResult(
            module_name="RECOVER",
            module_key=self.module_key,
            diag_entry_count=len(entries),
            slots=[
                MechSlotOutput(
                    slot_id="1",
                    board_cycles=[
                        MechBoardCycle(
                            dir_name="unknown",
                            processes=[
                                MechProcessLifecycle(
                                    process_name="recover",
                                    pid="1",
                                    logs=entries,
                                    total_count=len(entries),
                                )
                            ],
                        )
                    ],
                )
            ],
        )


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

    def test_non_numeric_suffix(self, plugin):
        from backend.parsing.process_name_resolver import ProcessNameResolver
        resolver = ProcessNameResolver()
        assert resolver.parse_diag_process_name("SERVICE-abc") == ("SERVICE-abc", "")


class TestBuildProcesses:
    def test_single_process(self, sample_mech_entries):
        procs = _build_process_lifecycles(sample_mech_entries[:5])
        assert len(procs) == 1
        assert procs[0].process_name == "dhcp"
        assert procs[0].pid == "100"
        assert procs[0].total_count == 5

    def test_missing_sequences(self):
        entries = [
            MechLogEntry(process_name="svc", pid="1", sequence=i, raw=f"line{i}")
            for i in [1, 2, 4, 5, 8]
        ]
        procs = _build_process_lifecycles(entries)
        assert len(procs) == 1
        assert procs[0].missing_sequences == [3, 6, 7]

    def test_grouped_by_name_and_pid(self):
        entries = [
            MechLogEntry(process_name="svc", pid="1", sequence=1, raw="a"),
            MechLogEntry(process_name="svc", pid="2", sequence=1, raw="b"),
            MechLogEntry(process_name="other", pid="1", sequence=1, raw="c"),
        ]
        procs = _build_process_lifecycles(entries)
        assert len(procs) == 3



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


class TestMechanismPluginOrchestration:
    def test_parser_loads_mechanism_plugin(self, sample_config, sample_parse_result):
        plugin = ParserPlugin(sample_config)

        result = plugin.parse(sample_parse_result)

        assert result is sample_parse_result

    def test_parser_emits_perf_logs_with_elapsed(
        self, sample_config, sample_parse_result, caplog,
    ):
        plugin = ParserPlugin(sample_config)

        with caplog.at_level(logging.INFO, logger="backend.plugins.default.parser"):
            plugin.parse(sample_parse_result)

        assert "LOGPARSE_PERF parser.timestamps elapsed=" in caplog.text
        assert "LOGPARSE_PERF parser.active_periods elapsed=" in caplog.text
        assert "LOGPARSE_PERF parser.module module=module1 elapsed=" in caplog.text
        assert "LOGPARSE_PERF parser.roles elapsed=" in caplog.text

    def test_parser_no_longer_exposes_module_specific_parse_method(self, plugin):
        assert not hasattr(plugin, "_parse_one_mech")

    def test_parser_records_shared_diagnostic_scan_metrics(self, sample_config, tmp_path):
        log_path = tmp_path / "diag.log"
        log_path.write_text(
            "\n".join(
                [
                    (
                        "2026-01-03T00:01:00 EXAMPLE Service=EXAMPLE; Slot=1; "
                        "CPU-Id=0; ProcessName=SERVICE-12345; Context=No[1] MASTER_ACTIVE)"
                    ),
                    (
                        '2026-01-03T00:01:01 MODULE2 Slot=1,CPU-Id=0,'
                        'ProcessName=WORKER[777],Context="hello"'
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        config = dict(sample_config)
        config["mechanism_modules"] = dict(sample_config["mechanism_modules"])
        config["mechanism_modules"]["module2"] = {
            "plugin": "backend.plugins.mechanisms.module2.Module2Plugin",
            "enabled": True,
            "config": {
                "module_name": "MODULE2",
                "identifying_keyword": "MODULE2",
                "depends_on_module": "module1",
                "diag_pattern": (
                    r"Slot=(?P<Slot>[\d/]+),CPU-Id=(?P<CPU_Id>[^,]*),"
                    r"ProcessName=(?P<ProcessName>[^,]+),Context=\"(?P<Context>.*?)\""
                ),
            },
        }
        plugin = ParserPlugin(config)
        plugin.performance_recorder = PerformanceRecorder(enabled=True)
        result = ParseResult(
            diagnostic_slots=[
                SlotInfo(
                    slot_id="1",
                    name="slot_1",
                    path=str(tmp_path),
                    diagnostic_logs=[
                        LogEntry(path=str(log_path), name=log_path.name, size_bytes=log_path.stat().st_size),
                    ],
                )
            ]
        )

        plugin.parse(result)
        perf = plugin.performance_recorder.to_dict()
        shared = next(stage for stage in perf["stages"] if stage["name"] == "diagnostic_scan.shared")

        assert shared["metrics"]["files"] == 1
        assert shared["metrics"]["lines"] == 2
        assert shared["metrics"]["timestamps"] == 2
        assert shared["metrics"]["module1_entries"] == 1
        assert shared["metrics"]["module2_entries"] == 1

    def test_shared_scan_isolates_module_scanner_errors(self, sample_config, tmp_path):
        log_path = tmp_path / "diag.log"
        log_path.write_text(
            (
                "2026-01-03T00:01:00 EXAMPLE Service=EXAMPLE; Slot=1; "
                "CPU-Id=0; ProcessName=SERVICE-12345; Context=No[1] MASTER_ACTIVE)\n"
            ),
            encoding="utf-8",
        )
        config = dict(sample_config)
        config["mechanism_modules"] = dict(sample_config["mechanism_modules"])
        config["mechanism_modules"]["bad_module"] = {
            "plugin": "tests.test_parser_plugin.ExplodingDiagnosticScannerPlugin",
            "enabled": True,
            "config": {},
        }
        result = ParseResult(
            diagnostic_slots=[
                SlotInfo(
                    slot_id="1",
                    name="slot_1",
                    path=str(tmp_path),
                    diagnostic_logs=[
                        LogEntry(path=str(log_path), name=log_path.name, size_bytes=log_path.stat().st_size),
                    ],
                )
            ]
        )

        ParserPlugin(config).parse(result)

        assert result.diagnostic_slots[0].diagnostic_logs[0].content_timestamps
        assert any(mech.module_key == "module1" for mech in result.mech_results)
        assert any("bad_module" in error and "shared diagnostic scan" in error for error in result.errors)

    def test_shared_scan_continues_module_after_scanner_line_error(self, sample_config, tmp_path):
        log_path = tmp_path / "diag.log"
        log_path.write_text(
            (
                "2026-01-03T00:01:00 boom\n"
                "2026-01-03T00:02:00 RECOVER\n"
            ),
            encoding="utf-8",
        )
        config = dict(sample_config)
        config["mechanism_modules"] = dict(sample_config["mechanism_modules"])
        config["mechanism_modules"]["recover"] = {
            "plugin": "tests.test_parser_plugin.RecoveringDiagnosticScannerPlugin",
            "enabled": True,
            "config": {},
        }
        result = ParseResult(
            diagnostic_slots=[
                SlotInfo(
                    slot_id="1",
                    name="slot_1",
                    path=str(tmp_path),
                    diagnostic_logs=[
                        LogEntry(path=str(log_path), name=log_path.name, size_bytes=log_path.stat().st_size),
                    ],
                )
            ]
        )

        ParserPlugin(config).parse(result)

        recover = next(mech for mech in result.mech_results if mech.module_key == "recover")
        assert recover.diag_entry_count == 1
        assert any("recover" in error and "shared diagnostic scan" in error for error in result.errors)

    def test_shared_scan_preserves_sorted_content_timestamps(self, sample_config, tmp_path):
        log_path = tmp_path / "diag.log"
        log_path.write_text(
            (
                "2026-01-03T00:02:00 EXAMPLE later\n"
                "2026-01-03T00:01:00 EXAMPLE earlier\n"
            ),
            encoding="utf-8",
        )
        result = ParseResult(
            diagnostic_slots=[
                SlotInfo(
                    slot_id="1",
                    name="slot_1",
                    path=str(tmp_path),
                    diagnostic_logs=[
                        LogEntry(path=str(log_path), name=log_path.name, size_bytes=log_path.stat().st_size),
                    ],
                )
            ]
        )

        ParserPlugin(sample_config).parse(result)

        timestamps = result.diagnostic_slots[0].diagnostic_logs[0].content_timestamps
        assert [ts.isoformat() for ts in timestamps] == [
            "2026-01-03T00:01:00",
            "2026-01-03T00:02:00",
        ]

    def test_shared_scan_isolates_module_scanner_builder_errors(self, sample_config, tmp_path):
        log_path = tmp_path / "diag.log"
        log_path.write_text(
            (
                "2026-01-03T00:01:00 EXAMPLE Service=EXAMPLE; Slot=1; "
                "CPU-Id=0; ProcessName=SERVICE-12345; Context=No[1] MASTER_ACTIVE)\n"
            ),
            encoding="utf-8",
        )
        config = dict(sample_config)
        config["mechanism_modules"] = dict(sample_config["mechanism_modules"])
        config["mechanism_modules"]["bad_builder"] = {
            "plugin": "tests.test_parser_plugin.ExplodingDiagnosticScannerBuilderPlugin",
            "enabled": True,
            "config": {},
        }
        result = ParseResult(
            diagnostic_slots=[
                SlotInfo(
                    slot_id="1",
                    name="slot_1",
                    path=str(tmp_path),
                    diagnostic_logs=[
                        LogEntry(path=str(log_path), name=log_path.name, size_bytes=log_path.stat().st_size),
                    ],
                )
            ]
        )

        ParserPlugin(config).parse(result)

        assert result.diagnostic_slots[0].diagnostic_logs[0].content_timestamps
        assert any(mech.module_key == "module1" for mech in result.mech_results)
        assert any("bad_builder" in error and "shared diagnostic scanner setup" in error for error in result.errors)


class TestFmtDir:
    def test_both_times(self):
        s = datetime(2026, 1, 3, 10, 37, 7)
        e = datetime(2026, 1, 3, 11, 37, 8)
        assert _format_cycle_dir(s, e) == "20260103103707-20260103113708"

    def test_start_only(self):
        assert _format_cycle_dir(datetime(2026, 1, 3, 0, 0, 0), None) == "unknown"

    def test_none(self):
        assert _format_cycle_dir(None, None) == "unknown"
