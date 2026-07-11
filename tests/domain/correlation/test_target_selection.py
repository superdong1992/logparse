from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.domain.correlation.target_selection import select_interval_candidate


@dataclass(frozen=True)
class Candidate:
    name: str
    start: datetime | None
    end: datetime | None

    def interval(self) -> tuple[datetime | None, datetime | None]:
        return self.start, self.end


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def test_exact_interval_wins() -> None:
    exact = Candidate("exact", _time("2026-01-03T00:00:00"), _time("2026-01-03T00:10:00"))
    later = Candidate("later", _time("2026-01-03T00:20:00"), _time("2026-01-03T00:30:00"))

    selected = select_interval_candidate([later, exact], _time("2026-01-03T00:05:00"))

    assert selected.status == "exact"
    assert selected.candidate is exact


def test_nearest_interval_is_explicit_fallback() -> None:
    earlier = Candidate("earlier", _time("2026-01-03T00:00:00"), _time("2026-01-03T00:10:00"))
    later = Candidate("later", _time("2026-01-03T00:30:00"), _time("2026-01-03T00:40:00"))

    selected = select_interval_candidate([earlier, later], _time("2026-01-03T00:12:00"))

    assert selected.status == "nearest"
    assert selected.candidate is earlier
    assert selected.caveats == ("nearest-cycle fallback",)


def test_nearest_tie_is_ambiguous() -> None:
    earlier = Candidate("earlier", _time("2026-01-03T00:00:00"), _time("2026-01-03T00:10:00"))
    later = Candidate("later", _time("2026-01-03T00:20:00"), _time("2026-01-03T00:30:00"))

    selected = select_interval_candidate([earlier, later], _time("2026-01-03T00:15:00"))

    assert selected.status == "ambiguous"
    assert selected.candidate is None


def test_single_unknown_interval_is_selected_but_multiple_are_ambiguous() -> None:
    first = Candidate("first", None, None)
    second = Candidate("second", None, None)

    selected = select_interval_candidate([first], _time("2026-01-03T00:15:00"))
    ambiguous = select_interval_candidate([first, second], _time("2026-01-03T00:15:00"))

    assert selected.status == "unknown"
    assert selected.candidate is first
    assert ambiguous.status == "ambiguous"
