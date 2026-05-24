"""进程名解析：从诊断日志和 journal 日志中解析进程名和 PID。"""

from __future__ import annotations


class ProcessNameResolver:
    def __init__(self, name_map: dict[str, str] | None = None):
        self._name_map = name_map or {}

    def parse_diag_process_name(self, raw: str) -> tuple[str, str]:
        """解析诊断日志中的进程名，返回 (process_name, pid)。"""
        for diag_name in sorted(self._name_map, key=len, reverse=True):
            if raw.startswith(diag_name):
                rest = raw[len(diag_name):]
                pid = rest[1:] if rest.startswith("-") else ""
                return diag_name, pid

        if "-" in raw:
            parts = raw.rsplit("-", 1)
            if parts[-1].isdigit():
                return parts[0], parts[-1]

        return raw, ""

    def resolve_journal_process_name(
        self,
        raw_name: str,
        raw_pid: str | None,
        indicator: str | None = None,
    ) -> tuple[str, str]:
        """解析 journal 日志中的进程名，返回 (process_name, pid)。"""
        pid = raw_pid or ""

        if pid and indicator and indicator in raw_name.lower():
            for diag_name, journal_name in self._name_map.items():
                if journal_name.lower() == raw_name.lower():
                    return diag_name, pid
            return raw_name, pid

        proc_name = raw_name
        if "-" in raw_name and not pid:
            parts = raw_name.rsplit("-", 1)
            if len(parts[-1]) >= 3 and parts[-1].isdigit():
                proc_name = parts[0]
                pid = parts[-1]

        return proc_name, pid
