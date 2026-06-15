from __future__ import annotations

from datetime import datetime, timezone

from backend.models import MechLogEntry
from backend.parsing.mech_entry_dedup import dedupe_mech_entries


def _entry(**overrides) -> MechLogEntry:
    values = {
        "timestamp": datetime(2026, 1, 3, 0, 1, tzinfo=timezone.utc),
        "source": "diagnostic",
        "source_file": "slot_1/diag-a.log",
        "slot": "1",
        "cpu_id": "",
        "process_name": "svc",
        "pid": "100",
        "context": "No[1] msg",
        "sequence": 1,
        "is_active_signal": False,
        "raw": "2026-01-03T00:01:00 svc No[1] msg",
    }
    values.update(overrides)
    return MechLogEntry(**values)


def test_dedupe_ignores_source_and_source_file() -> None:
    first = _entry(source="diagnostic", source_file="slot_1/diag-a.log")
    duplicate = _entry(source="journal", source_file="slot_2/journal.log")

    assert dedupe_mech_entries([first, duplicate]) == [first]


def test_dedupe_preserves_entries_with_different_parsed_content() -> None:
    base = _entry()
    distinct_entries = [
        base,
        _entry(timestamp=datetime(2026, 1, 3, 0, 2, tzinfo=timezone.utc)),
        _entry(slot="2"),
        _entry(cpu_id="1"),
        _entry(process_name="other"),
        _entry(pid="200"),
        _entry(context="No[2] msg"),
        _entry(sequence=2),
        _entry(is_active_signal=True),
        _entry(raw="2026-01-03T00:01:00 svc No[1] changed"),
    ]

    assert dedupe_mech_entries(distinct_entries) == distinct_entries


def test_dedupe_keeps_first_occurrence() -> None:
    first = _entry(source_file="slot_1/diag-a.log")
    duplicate = _entry(source_file="slot_1/diag-b.log")
    later = _entry(sequence=2, source_file="slot_1/diag-b.log")

    assert dedupe_mech_entries([first, duplicate, later]) == [first, later]
