"""Tests for backend/parsing/process_name_resolver.py."""
from __future__ import annotations

from backend.parsing.process_name_resolver import ProcessNameResolver


class TestParseDiagProcessName:
    def test_simple_name_with_pid(self):
        resolver = ProcessNameResolver()
        assert resolver.parse_diag_process_name("SERVICE-12345") == ("SERVICE", "12345")

    def test_name_only(self):
        resolver = ProcessNameResolver()
        assert resolver.parse_diag_process_name("SERVICE") == ("SERVICE", "")

    def test_non_numeric_suffix_not_split(self):
        resolver = ProcessNameResolver()
        assert resolver.parse_diag_process_name("DHCP001") == ("DHCP001", "")


class TestResolveJournalProcessName:
    def test_name_with_short_pid_not_split(self):
        """PID < 3 位数字不应被拆分。"""
        resolver = ProcessNameResolver()
        proc, pid = resolver.resolve_journal_process_name("name-12", None)
        assert proc == "name-12"
        assert pid == ""

    def test_name_with_long_pid_split(self):
        """PID >= 3 位数字应该被拆分。"""
        resolver = ProcessNameResolver()
        proc, pid = resolver.resolve_journal_process_name("name-123", None)
        assert proc == "name"
        assert pid == "123"

    def test_pid_provided_directly(self):
        resolver = ProcessNameResolver()
        proc, pid = resolver.resolve_journal_process_name("someproc", "456")
        assert proc == "someproc"
        assert pid == "456"
