"""Tests for MechOutputWriter."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from backend.models import (
    MechBoardCycle, MechCpuCycle, MechLogEntry, MechProcessLifecycle,
    MechResult, MechSlotOutput,
)
from backend.parsing.output_writer import MechOutputWriter
from backend.utils import safe_log_filename, safe_path_segment


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
            tmp_path / "mech_modules" / safe_path_segment("EXAMPLE") / "slot_1"
            / safe_path_segment("20260103T000100-20260103T000200")
            / safe_log_filename("svc", "100")
        )
        assert expected_log.exists()

    def test_log_file_content(self, writer, tmp_path):
        mech_result = _make_mech_result()
        writer.write(mech_result, tmp_path)

        log_path = (
            tmp_path / "mech_modules" / safe_path_segment("EXAMPLE") / "slot_1"
            / safe_path_segment("20260103T000100-20260103T000200")
            / safe_log_filename("svc", "100")
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
            tmp_path / "mech_modules" / safe_path_segment("EXAMPLE") / "slot_1"
            / safe_path_segment("20260103T000100-20260103T000200")
            / "cpu_1" / safe_log_filename("svc", "100")
        )
        assert expected.exists()

    def test_nested_cpu_cycle_subdirectory(self, writer, tmp_path):
        tz = timezone(timedelta(hours=8))
        result = MechResult(module_name="EXAMPLE")
        slot = MechSlotOutput(slot_id="1")
        slot.board_cycles.append(MechBoardCycle(
            dir_name="20260103T000000-20260103T001000",
            start_time=datetime(2026, 1, 3, 0, 0, 0, tzinfo=tz),
            end_time=datetime(2026, 1, 3, 0, 10, 0, tzinfo=tz),
            cpu_cycles=[
                MechCpuCycle(
                    cpu_id="1",
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
                )
            ],
        ))
        result.slots.append(slot)

        writer.write(result, tmp_path)

        expected = (
            tmp_path / "mech_modules" / safe_path_segment("EXAMPLE") / "slot_1"
            / safe_path_segment("20260103T000000-20260103T001000") / "cpu_1"
            / safe_path_segment("20260103T000100-20260103T000200")
            / safe_log_filename("svc", "100")
        )
        assert expected.exists()

    def test_process_filename_is_sanitized(self, writer, tmp_path):
        result = MechResult(module_name="EXAMPLE")
        result.slots.append(
            MechSlotOutput(
                slot_id="1",
                board_cycles=[
                    MechBoardCycle(
                        dir_name="cycle",
                        processes=[
                            MechProcessLifecycle(
                                process_name=r"..\..\escape",
                                pid="10/20",
                                logs=[
                                    MechLogEntry(
                                        source="diagnostic",
                                        source_file="slot_1/diag.log",
                                        slot="1",
                                        cpu_id="",
                                        process_name=r"..\..\escape",
                                        pid="10/20",
                                        raw="raw",
                                    )
                                ],
                                total_count=1,
                            )
                        ],
                    )
                ],
            )
        )

        writer.write(result, tmp_path)

        safe_name = safe_log_filename(r"..\..\escape", "10/20")
        expected = (
            tmp_path / "mech_modules" / safe_path_segment("EXAMPLE") / "slot_1"
            / safe_path_segment("cycle") / safe_name
        )
        assert expected.is_file()
        assert expected.parent == (
            tmp_path / "mech_modules" / safe_path_segment("EXAMPLE") / "slot_1"
            / safe_path_segment("cycle")
        )

    def test_module_name_directory_is_sanitized(self, writer, tmp_path):
        result = _make_mech_result()
        result.module_name = r"..\..\MODULE"

        mech_dir = writer.write(result, tmp_path)

        expected_dir = tmp_path / "mech_modules" / safe_path_segment(r"..\..\MODULE")
        assert mech_dir == expected_dir
        assert (expected_dir / "slot_1" / safe_path_segment("20260103T000100-20260103T000200")).is_dir()
        assert not (tmp_path / "MODULE").exists()

    def test_process_pid_separator_avoids_filename_collision(self, writer, tmp_path):
        result = MechResult(module_name="EXAMPLE")
        result.slots.append(
            MechSlotOutput(
                slot_id="1",
                board_cycles=[
                    MechBoardCycle(
                        dir_name="cycle",
                        processes=[
                            MechProcessLifecycle(
                                process_name="svc-100",
                                pid="",
                                logs=[
                                    MechLogEntry(
                                        source="diagnostic",
                                        source_file="slot_1/diag.log",
                                        slot="1",
                                        cpu_id="",
                                        process_name="svc-100",
                                        pid="",
                                        raw="no pid",
                                    )
                                ],
                                total_count=1,
                            ),
                            MechProcessLifecycle(
                                process_name="svc",
                                pid="100",
                                logs=[
                                    MechLogEntry(
                                        source="diagnostic",
                                        source_file="slot_1/diag.log",
                                        slot="1",
                                        cpu_id="",
                                        process_name="svc",
                                        pid="100",
                                        raw="with pid",
                                    )
                                ],
                                total_count=1,
                            ),
                        ],
                    )
                ],
            )
        )

        writer.write(result, tmp_path)
        out_dir = (
            tmp_path / "mech_modules" / safe_path_segment("EXAMPLE") / "slot_1"
            / safe_path_segment("cycle")
        )

        assert (out_dir / safe_log_filename("svc-100", "")).read_text(encoding="utf-8").endswith("no pid\n")
        assert (out_dir / safe_log_filename("svc", "100")).read_text(encoding="utf-8").endswith("with pid\n")
        assert safe_log_filename("svc-100", "") != safe_log_filename("svc", "100")

    def test_returns_output_dir(self, writer, tmp_path):
        mech_result = _make_mech_result()
        result = writer.write(mech_result, tmp_path)
        assert result == tmp_path / "mech_modules" / safe_path_segment("EXAMPLE")
