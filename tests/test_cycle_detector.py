"""Tests for CycleDetector."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.models import MechBoardCycle, MechLogEntry
from backend.parsing.cycle_detector import CycleDetector


@pytest.fixture
def detector():
    return CycleDetector(indicator="dhcp")


class TestCycleDetector:
    def test_single_cycle(self, detector):
        tz = timezone(timedelta(hours=8))
        entries = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0, tzinfo=tz),
                source="journal", slot="1", cpu_id="",
                process_name="svc", pid="100", sequence=i,
                raw=f"line{i}",
            )
            for i in range(1, 6)
        ]
        cycles = detector.detect(entries)
        assert len(cycles) == 1
        assert len(cycles[0].processes) == 1

    def test_pid_change_splits(self, detector):
        tz = timezone(timedelta(hours=8))
        entries = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0, tzinfo=tz),
                source="journal", slot="1", cpu_id="",
                process_name="dhcp", pid="100", sequence=i,
                raw=f"line{i}",
            )
            for i in range(1, 4)
        ] + [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 1, i, 0, tzinfo=tz),
                source="journal", slot="1", cpu_id="",
                process_name="dhcp", pid="200", sequence=i,
                raw=f"line2_{i}",
            )
            for i in range(1, 4)
        ]
        cycles = detector.detect(entries)
        assert len(cycles) == 2

    def test_no_indicator(self):
        detector = CycleDetector(indicator=None)
        tz = timezone(timedelta(hours=8))
        entries = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0, tzinfo=tz),
                source="journal", slot="1", cpu_id="",
                process_name="svc", pid="100", sequence=i,
                raw=f"line{i}",
            )
            for i in range(1, 11)
        ]
        cycles = detector.detect(entries)
        assert len(cycles) == 1

    def test_empty_entries(self, detector):
        assert detector.detect([]) == []

    def test_cpu_subcard_isolation(self, detector):
        tz = timezone(timedelta(hours=8))
        board = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0, tzinfo=tz),
                source="journal", slot="1", cpu_id="",
                process_name="svc", pid="100", sequence=i,
                raw=f"board_{i}",
            )
            for i in range(1, 6)
        ]
        cpu = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0, tzinfo=tz),
                source="journal", slot="1", cpu_id="1",
                process_name="dhcp", pid="50", sequence=i,
                raw=f"cpu_{i}",
            )
            for i in range(1, 4)
        ] + [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 1, i, 0, tzinfo=tz),
                source="journal", slot="1", cpu_id="1",
                process_name="dhcp", pid="60", sequence=i,
                raw=f"cpu2_{i}",
            )
            for i in range(1, 4)
        ]
        cycles = detector.detect(board + cpu)
        assert len(cycles) >= 3
