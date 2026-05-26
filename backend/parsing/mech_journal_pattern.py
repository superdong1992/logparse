"""Shared module1 journal pattern matching helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class JournalLineMatch:
    pattern_name: str
    raw_name: str
    raw_pid: str | None
    sequence: int
    context: str
    auto_no_sequence: bool = False


@dataclass(frozen=True)
class _PatternCandidate:
    name: str
    regex: re.Pattern
    has_sequence: bool
    auto_no_sequence: bool = False


class JournalPatternMatcher:
    def __init__(
        self,
        journal_re: re.Pattern | None,
        journal_re2: re.Pattern | None,
        seq_re: re.Pattern,
    ):
        configured = [
            _PatternCandidate("journal.line_pattern", journal_re, _has_sequence(journal_re))
            for journal_re in [journal_re]
            if journal_re is not None
        ] + [
            _PatternCandidate("journal.line_pattern2", journal_re2, _has_sequence(journal_re2))
            for journal_re2 in [journal_re2]
            if journal_re2 is not None
        ]
        self._configured = configured
        self._fallbacks = [
            candidate
            for configured_candidate in configured
            for candidate in [_derive_no_sequence_candidate(configured_candidate, seq_re)]
            if candidate is not None
        ]

    def match(self, line: str) -> JournalLineMatch | None:
        for candidate in self._configured:
            result = self._match_candidate(candidate, line)
            if result:
                return result

        if "No[" in line:
            return None

        for candidate in self._fallbacks:
            result = self._match_candidate(candidate, line)
            if result:
                return result

        return None

    @staticmethod
    def _match_candidate(candidate: _PatternCandidate, line: str) -> JournalLineMatch | None:
        m = candidate.regex.match(line)
        if not m:
            return None

        raw_name = m.group(1)
        raw_pid = m.group(2) if m.re.groups >= 2 else None

        if candidate.has_sequence:
            try:
                sequence = int(m.group(3))
            except (TypeError, ValueError):
                sequence = 0
            context = m.group(4)
        else:
            sequence = 0
            context = m.group(3)

        return JournalLineMatch(
            pattern_name=candidate.name,
            raw_name=raw_name,
            raw_pid=raw_pid,
            sequence=sequence,
            context=context,
            auto_no_sequence=candidate.auto_no_sequence,
        )


def _has_sequence(regex: re.Pattern | None) -> bool:
    return bool(regex and regex.groups >= 4)


def _derive_no_sequence_candidate(
    candidate: _PatternCandidate,
    seq_re: re.Pattern,
) -> _PatternCandidate | None:
    if not candidate.has_sequence:
        return None

    derived_pattern = _derive_no_sequence_pattern(candidate.regex.pattern, seq_re.pattern)
    if not derived_pattern:
        return None

    try:
        derived_re = re.compile(derived_pattern)
    except re.error:
        return None

    if derived_re.groups != 3:
        return None

    return _PatternCandidate(
        name=f"{candidate.name}.auto_no_sequence",
        regex=derived_re,
        has_sequence=False,
        auto_no_sequence=True,
    )


def _derive_no_sequence_pattern(pattern: str, seq_pattern: str) -> str | None:
    markers = [seq_pattern, r"No\[(\d+)\]"]
    for marker in markers:
        if marker and pattern.count(marker) == 1:
            return pattern.replace(marker, "", 1)
    return None
