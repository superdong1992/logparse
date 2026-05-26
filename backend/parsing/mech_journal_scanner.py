"""Journal 日志扫描器：从 journal 日志文件中提取机制模块日志条目。"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.models import MechLogEntry, PrivateSlotInfo
from backend.parsing.file_iter import iter_text_file_lines
from backend.parsing.mech_journal_pattern import JournalPatternMatcher
from backend.parsing.process_name_resolver import ProcessNameResolver
from backend.parsing.timestamp_extractor import TimestampExtractor

logger = logging.getLogger(__name__)


class MechJournalScanner:
    def __init__(
        self,
        journal_re: re.Pattern,
        journal_re2: re.Pattern | None,
        journal_keyword: str,
        seq_re: re.Pattern,
        master_keyword: re.Pattern | None,
        resolver: ProcessNameResolver,
        indicator: str | None,
        module_name_upper: str,
        ts_extractor: TimestampExtractor,
    ):
        self._journal_re = journal_re
        self._journal_re2 = journal_re2
        self._journal_keyword = journal_keyword
        self._seq_re = seq_re
        self._matcher = JournalPatternMatcher(journal_re, journal_re2, seq_re)
        self._master_keyword = master_keyword
        self._resolver = resolver
        self._indicator = indicator
        self._mod_upper = module_name_upper
        self._ts_extractor = ts_extractor

    def scan(self, ps: PrivateSlotInfo, tzinfo: Any) -> list[MechLogEntry]:
        entries: list[MechLogEntry] = []

        for jl in ps.journal_logs:
            jl_path = Path(jl.path)
            if not jl_path.is_file():
                continue

            for line in iter_text_file_lines(jl_path):
                if self._mod_upper not in line:
                    continue
                if self._journal_keyword not in line.lower():
                    continue

                match = self._matcher.match(line)
                if not match:
                    continue

                raw_name = match.raw_name
                raw_pid = match.raw_pid
                seq = match.sequence
                context = match.context

                proc_name, pid = self._resolver.resolve_journal_process_name(
                    raw_name, raw_pid, self._indicator,
                )
                is_active = bool(self._master_keyword and self._master_keyword.search(context))
                ts = self._extract_first_ts(line)
                if ts and ts.tzinfo is None and tzinfo is not None:
                    ts = ts.replace(tzinfo=tzinfo)

                src_file = f"{ps.dir_name}/{jl.name}"
                entries.append(MechLogEntry(
                    timestamp=ts, source="journal",
                    source_file=src_file,
                    slot=ps.slot_id, cpu_id=ps.cpu_id if ps.cpu_id not in (None, "", "0") else "",
                    process_name=proc_name, pid=pid,
                    context=context, sequence=seq,
                    is_active_signal=is_active, raw=line.strip()[:500],
                ))

        return entries

    def _extract_first_ts(self, line: str) -> datetime | None:
        stamps = self._ts_extractor.extract_from_text(line)
        return stamps[0] if stamps else None
