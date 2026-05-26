from __future__ import annotations

import re

from backend.parsing.mech_journal_pattern import JournalPatternMatcher


def _matcher() -> JournalPatternMatcher:
    return JournalPatternMatcher(
        journal_re=None,
        journal_re2=re.compile(r"^\S+\s+\S+\s+(\S+?)(?:-(\d+))?:\s+No\[(\d+)\](.+)$"),
        seq_re=re.compile(r"No\[(\d+)\]"),
    )


def test_matches_configured_sequence_pattern_first():
    matcher = _matcher()

    result = matcher.match("2026-01-03T00:01:00 host SERVICE-12345: No[7] EXAMPLE ok")

    assert result is not None
    assert result.pattern_name == "journal.line_pattern2"
    assert result.auto_no_sequence is False
    assert result.raw_name == "SERVICE"
    assert result.raw_pid == "12345"
    assert result.sequence == 7
    assert result.context == " EXAMPLE ok"


def test_auto_fallback_matches_without_sequence_from_same_config():
    matcher = _matcher()

    result = matcher.match("2026-01-03T00:01:00 host SERVICE-12345: EXAMPLE old version")

    assert result is not None
    assert result.pattern_name == "journal.line_pattern2.auto_no_sequence"
    assert result.auto_no_sequence is True
    assert result.raw_name == "SERVICE"
    assert result.raw_pid == "12345"
    assert result.sequence == 0
    assert result.context == "EXAMPLE old version"


def test_auto_fallback_is_not_used_when_line_contains_malformed_no():
    matcher = _matcher()

    result = matcher.match("2026-01-03T00:01:00 host SERVICE-12345: No[bad] EXAMPLE corrupt")

    assert result is None


def test_manual_three_group_pattern_still_matches():
    matcher = JournalPatternMatcher(
        journal_re=None,
        journal_re2=re.compile(r"^\S+\s+\S+\s+(\S+?)(?:-(\d+))?:\s+(.+)$"),
        seq_re=re.compile(r"No\[(\d+)\]"),
    )

    result = matcher.match("2026-01-03T00:01:00 host SERVICE-12345: EXAMPLE manual")

    assert result is not None
    assert result.pattern_name == "journal.line_pattern2"
    assert result.auto_no_sequence is False
    assert result.sequence == 0
    assert result.context == "EXAMPLE manual"
