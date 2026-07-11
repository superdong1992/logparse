"""Protected interval-selection policy for deterministic target resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Protocol, Sequence, TypeVar


class IntervalCandidate(Protocol):
    def interval(self) -> tuple[datetime | None, datetime | None]: ...


CandidateT = TypeVar("CandidateT", bound=IntervalCandidate)


@dataclass(frozen=True, slots=True)
class TargetSelection(Generic[CandidateT]):
    status: str
    candidate: CandidateT | None = None
    reason: str = ""
    caveats: tuple[str, ...] = ()


def select_interval_candidate(
    candidates: Sequence[CandidateT],
    problem_time: datetime,
) -> TargetSelection[CandidateT]:
    """Apply the exact, nearest, unknown and ambiguity policy.

    This is intentionally yellow policy: changing its tie, fallback or interval
    semantics requires a real LAN case and corpus comparison.
    """

    timed: list[tuple[CandidateT, datetime | None, datetime | None]] = []
    unknown: list[CandidateT] = []
    for candidate in candidates:
        start, end = candidate.interval()
        if start is None and end is None:
            unknown.append(candidate)
            continue
        timed.append((candidate, start, end))

    exact = [
        candidate
        for candidate, start, end in timed
        if _contains_time(start, end, problem_time)
    ]
    if len(exact) == 1:
        return TargetSelection(status="exact", candidate=exact[0])
    if len(exact) > 1:
        return TargetSelection(
            status="ambiguous",
            reason="multiple exact cycles match target process and problem_time",
        )

    if timed:
        by_distance = [
            (candidate, _distance_to_interval(start, end, problem_time))
            for candidate, start, end in timed
        ]
        best_distance = min(distance for _candidate, distance in by_distance)
        nearest = [
            candidate
            for candidate, distance in by_distance
            if distance == best_distance
        ]
        if len(nearest) == 1:
            return TargetSelection(
                status="nearest",
                candidate=nearest[0],
                caveats=("nearest-cycle fallback",),
            )
        return TargetSelection(
            status="ambiguous",
            reason="nearest tie for target process and problem_time",
            caveats=("nearest-cycle fallback tied",),
        )

    if len(unknown) == 1:
        return TargetSelection(
            status="unknown",
            candidate=unknown[0],
            caveats=("no timed cycle available; using unknown cycle",),
        )
    return TargetSelection(
        status="ambiguous",
        reason="multiple unknown cycles match target process",
        caveats=("no timed cycle available",),
    )


def _align_for_compare(
    problem_time: datetime,
    start: datetime | None,
    end: datetime | None,
) -> tuple[datetime, datetime | None, datetime | None]:
    reference_tz = problem_time.tzinfo
    if reference_tz is None:
        reference_tz = (start.tzinfo if start and start.tzinfo else None) or (
            end.tzinfo if end and end.tzinfo else None
        )
    if reference_tz is None:
        return problem_time.replace(tzinfo=None), _strip_tz(start), _strip_tz(end)
    aligned_problem = problem_time
    if aligned_problem.tzinfo is None:
        aligned_problem = aligned_problem.replace(tzinfo=reference_tz)
    return aligned_problem, _with_tz(start, reference_tz), _with_tz(end, reference_tz)


def _strip_tz(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None)


def _with_tz(value: datetime | None, tzinfo) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=tzinfo)
    return value


def _contains_time(
    start: datetime | None,
    end: datetime | None,
    problem_time: datetime,
) -> bool:
    problem, aligned_start, aligned_end = _align_for_compare(problem_time, start, end)
    if aligned_start and problem < aligned_start:
        return False
    if aligned_end and problem > aligned_end:
        return False
    return aligned_start is not None or aligned_end is not None


def _distance_to_interval(
    start: datetime | None,
    end: datetime | None,
    problem_time: datetime,
) -> float:
    problem, aligned_start, aligned_end = _align_for_compare(problem_time, start, end)
    if _contains_time(aligned_start, aligned_end, problem):
        return 0.0
    distances = []
    if aligned_start is not None:
        distances.append(abs((problem - aligned_start).total_seconds()))
    if aligned_end is not None:
        distances.append(abs((problem - aligned_end).total_seconds()))
    return min(distances) if distances else float("inf")
