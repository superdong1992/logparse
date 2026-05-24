"""诊断日志扫描器：从诊断日志文件中提取机制模块日志条目。"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from backend.models import LogEntry, MechLogEntry
from backend.parsing.process_name_resolver import ProcessNameResolver
from backend.parsing.timestamp_extractor import TimestampExtractor

logger = logging.getLogger(__name__)


class MechDiagScanner:
    def __init__(
        self,
        diag_re: re.Pattern,
        seq_re: re.Pattern,
        master_keyword: re.Pattern | None,
        resolver: ProcessNameResolver,
        module_name_upper: str,
        ts_extractor: TimestampExtractor,
    ):
        self._diag_re = diag_re
        self._seq_re = seq_re
        self._master_keyword = master_keyword
        self._resolver = resolver
        self._mod_upper = module_name_upper
        self._ts_extractor = ts_extractor

    def scan(self, log_entry: LogEntry, slot_id: str) -> list[MechLogEntry]:
        entries: list[MechLogEntry] = []
        text = self._read_log_entry(log_entry)
        if not text:
            return entries

        for line in text.splitlines():
            if self._mod_upper not in line:
                continue
            m = self._diag_re.search(line)
            if not m:
                continue

            slot = m.group("Slot")
            cpu_id = m.group("CPU_Id")
            if cpu_id == "0":
                cpu_id = ""
            raw_proc_name = m.group("ProcessName")
            context = m.group("Context")

            proc_name, pid = self._resolver.parse_diag_process_name(raw_proc_name)

            sm = self._seq_re.search(line)
            if not sm:
                continue
            try:
                seq = int(sm.group(1))
            except ValueError:
                continue

            is_active = bool(self._master_keyword and self._master_keyword.search(context))
            ts = self._extract_first_ts(line)

            src_file = f"slot_{slot_id}/{log_entry.name}"
            entries.append(MechLogEntry(
                timestamp=ts, source="diagnostic",
                source_file=src_file,
                slot=slot, cpu_id=cpu_id,
                process_name=proc_name, pid=pid,
                context=context, sequence=seq,
                is_active_signal=is_active, raw=line.strip()[:500],
            ))

        return entries

    def _extract_first_ts(self, line: str) -> datetime | None:
        stamps = self._ts_extractor.extract_from_text(line)
        return stamps[0] if stamps else None

    def _read_log_entry(self, log_entry: LogEntry) -> str:
        if log_entry.extracted_path:
            ext_dir = Path(log_entry.extracted_path)
            if ext_dir.is_dir():
                parts: list[str] = []
                for f in sorted(ext_dir.rglob("*")):
                    if f.is_file():
                        text = self._ts_extractor._read_file(f)
                        if text:
                            parts.append(text)
                return "\n".join(parts)
        file_path = Path(log_entry.path)
        if file_path.is_file():
            return self._ts_extractor._read_file(file_path)
        return ""
