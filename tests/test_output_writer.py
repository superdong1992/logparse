"""Tests for MechOutputWriter."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from backend.models import (
    MechBoardCycle, MechLogEntry, MechProcessLifecycle,
    MechResult, MechSlotOutput,
)
from backend.parsing.output_writer import MechOutputWriter


@pytest.fixture
def writer():
    return MechOutputWriter()


def _make_mech_result() -> MechResult:
    tz = timezone(timedelta(hours=8))
    result = MechResult(module_name="EXAMPLE")
    slot = MechSlotOutput(slot_id="1")
    slot.board_cycles.append(MechBoardCycle(
        dir_name="20260103T000100-20260103T000200",
        start_time=datetime(2026, 1, 3, 0, 1, 0, tzinfo=tz),
        end_time=datetime(2026, 1, 3, 0, 2, 0, tzinfo=tz),
        processes=[
            MechProcessLifecycle(
                process_name="svc", pid="100",
                logs=[
                    MechLogEntry(
                        timestamp=datetime(2026, 1, 3, 0, 1, 30, tzinfo=tz),
                        source="journal", source_file="slot_1/journal.log",
                        slot="1", cpu_id="",
                        process_name="svc", pid="100",
                        context="msg", sequence=1, raw="raw line 1",
                    ),
                ],
                total_count=1,
            ),
        ],
    ))
    result.slots.append(slot)
    return result


class TestMechOutputWriter:
    def test_creates_directory_structure(self, writer, tmp_path):
        mech_result = _make_mech_result()
        output_dir = writer.write(mech_result, tmp_path)

        expected_log = (
            tmp_path / "mech_modules" / "EXAMPLE" / "slot_1"
            / "20260103T000100-20260103T000200" / "svc-100.log"
        )
        assert expected_log.exists()

    def test_log_file_content(self, writer, tmp_path):
        mech_result = _make_mech_result()
        writer.write(mech_result, tmp_path)

        log_path = (
            tmp_path / "mech_modules" / "EXAMPLE" / "slot_1"
            / "20260103T000100-20260103T000200" / "svc-100.log"
        )
        content = log_path.read_text(encoding="utf-8")
        assert "[0001]" in content
        assert "journal|slot_1/journal.log" in content
        assert "raw line 1" in content

    def test_cpu_subdirectory(self, writer, tmp_path):
        tz = timezone(timedelta(hours=8))
        result = MechResult(module_name="EXAMPLE")
        slot = MechSlotOutput(slot_id="1")
        slot.board_cycles.append(MechBoardCycle(
            dir_name="20260103T000100-20260103T000200",
            start_time=datetime(2026, 1, 3, 0, 1, 0, tzinfo=tz),
            end_time=datetime(2026, 1, 3, 0, 2, 0, tzinfo=tz),
            processes=[
                MechProcessLifecycle(
                    process_name="svc", pid="100",
                    logs=[
                        MechLogEntry(
                            source="journal", source_file="s/j.log",
                            slot="1", cpu_id="1",
                            process_name="svc", pid="100",
                            sequence=1, raw="cpu line",
                        ),
                    ],
                    total_count=1,
                ),
            ],
        ))
        result.slots.append(slot)

        writer.write(result, tmp_path)

        expected = (
            tmp_path / "mech_modules" / "EXAMPLE" / "slot_1"
            / "20260103T000100-20260103T000200" / "cpu_1" / "svc-100.log"
        )
        assert expected.exists()

    def test_returns_output_dir(self, writer, tmp_path):
        mech_result = _make_mech_result()
        result = writer.write(mech_result, tmp_path)
        assert result == tmp_path / "mech_modules" / "EXAMPLE"
