"""默认日志解析插件：提取时间戳、ActivePeriod、机制模块解析、主控判定。"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.models import (
    ActivePeriod,
    BoardRole,
    MechLogEntry,
    MechResult,
    MechSlotOutput,
    LogEntry,
    ParseResult,
    PrivateSlotInfo,
    SlotInfo,
)
from backend.parsing.cycle_detector import CycleDetector
from backend.parsing.timestamp_extractor import TimestampExtractor
from backend.plugins.base import LogParserPlugin

logger = logging.getLogger(__name__)


class ParserPlugin(LogParserPlugin):
    """标准日志解析：时间戳→ActivePeriod→机制模块→主控判定。"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._compile_patterns()
        self._ts_extractor = TimestampExtractor(self._ts_regex)

    def _compile_patterns(self) -> None:
        self._ts_regex = re.compile(
            self.config.get("timestamp_regex",
                           r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?")
        )
        self._gap_threshold = int(self.config.get("active_period_gap_threshold", 300))
        self._mech_modules = self.config.get("mechanism_modules", {})

    # ── parse() ───────────────────────────────────────────

    def parse(self, result: ParseResult) -> ParseResult:
        # 1. 提取所有内容时间戳
        self._extract_all_timestamps(result.diagnostic_slots)

        # 2. 构建 ActivePeriod
        for slot in result.diagnostic_slots:
            for p in self._build_active_periods(slot):
                slot.add_active_period(p)

        # 3. 机制模块解析
        results: dict[str, MechResult] = {}
        for module_key, cfg in self._mech_modules.items():
            if not cfg.get("enabled", True):
                continue
            if not cfg.get("module_name"):
                continue
            mech = self._parse_one_mech(result, cfg, module_key)
            if mech:
                results[module_key] = mech

        # 4. 主控判定：机制模块优先，兜底 Identifier 逻辑
        for module_key, mech in results.items():
            result.mech_results.append(mech)
            self._apply_mech_roles(mech, result)

        self._fallback_roles(result)

        return result

    # ── 时间戳提取 ────────────────────────────────────────

    def _extract_all_timestamps(self, slots: list[SlotInfo]) -> None:
        for slot in slots:
            for entry in slot.diagnostic_logs:
                entry.content_timestamps = self._ts_extractor.extract_from_entry(entry)

    # ── ActivePeriod 构建 ─────────────────────────────────

    def _build_active_periods(self, slot: SlotInfo) -> list[ActivePeriod]:
        all_stamps = slot.all_content_timestamps
        if not all_stamps:
            return []

        gap = timedelta(seconds=self._gap_threshold)
        periods: list[ActivePeriod] = []
        seg_start = all_stamps[0]
        seg_end = all_stamps[0]

        for ts in all_stamps[1:]:
            if ts - seg_end <= gap:
                seg_end = ts
            else:
                periods.append(ActivePeriod(start=seg_start, end=seg_end))
                seg_start = ts
                seg_end = ts

        periods.append(ActivePeriod(start=seg_start, end=seg_end))
        return periods

    # ── 机制模块解析 ──────────────────────────────────────

    def _parse_one_mech(
        self, result: ParseResult, cfg: dict[str, Any], module_key: str,
    ) -> MechResult | None:
        module_name: str = cfg["module_name"]
        mod_upper = module_name.upper()

        diag_re = re.compile(cfg["diag_pattern"]) if cfg.get("diag_pattern") else None
        if diag_re:
            required = {"Slot", "CPU_Id", "ProcessName", "Context"}
            if not required.issubset(diag_re.groupindex):
                return None

        jnl_cfg: dict = cfg.get("journal", {})
        journal_re = re.compile(jnl_cfg["line_pattern"]) if jnl_cfg.get("line_pattern") else None
        journal_re2 = re.compile(jnl_cfg["line_pattern2"]) if jnl_cfg.get("line_pattern2") else None
        journal_keyword = (jnl_cfg.get("identifying_keyword", "").lower()
                           if jnl_cfg.get("identifying_keyword") else None)
        seq_re = re.compile(cfg.get("sequence_pattern", r"No\[(\d+)\]"))
        master_keyword = (re.compile(cfg["active_master_keyword"])
                          if cfg.get("active_master_keyword") else None)
        indicator = (cfg.get("board_restart_indicator", "").lower()
                     if cfg.get("board_restart_indicator") else None)
        name_map: dict[str, str] = cfg.get("process_name_mapping", {})

        all_entries: list[MechLogEntry] = []

        # 扫描诊断日志
        if diag_re:
            for slot in result.diagnostic_slots:
                for log_entry in slot.diagnostic_logs:
                    all_entries.extend(
                        self._scan_diag_entries(
                            log_entry, slot.slot_id, diag_re, seq_re,
                            master_keyword, name_map, mod_upper,
                        )
                    )

        # 时区对齐起点
        tzinfo = None
        for e in all_entries:
            if e.timestamp and e.timestamp.tzinfo:
                tzinfo = e.timestamp.tzinfo
                break

        # 扫描 journal 日志
        if (journal_re or journal_re2) and journal_keyword:
            for ps in result.private_slots:
                all_entries.extend(
                    self._scan_journal_entries(
                        ps, journal_re, journal_re2, journal_keyword,
                        seq_re, master_keyword, name_map, indicator,
                        mod_upper, tzinfo,
                    )
                )

        if not all_entries:
            return None

        # 全局时区归一化
        tzinfo = None
        for e in all_entries:
            if e.timestamp and e.timestamp.tzinfo:
                tzinfo = e.timestamp.tzinfo
                break
        if tzinfo:
            for e in all_entries:
                if e.timestamp and e.timestamp.tzinfo is None:
                    e.timestamp = e.timestamp.replace(tzinfo=tzinfo)

        # 按 slot 分组
        by_slot: dict[str, list[MechLogEntry]] = defaultdict(list)
        for e in all_entries:
            by_slot[e.slot].append(e)

        mech_result = MechResult(module_name=module_name)
        for slot_id, entries in sorted(by_slot.items()):
            slot_output = MechSlotOutput(slot_id=slot_id)
            detector = CycleDetector(indicator=indicator)
            slot_output.board_cycles = detector.detect(entries)
            mech_result.slots.append(slot_output)

        active_slots: set[str] = set()
        for e in all_entries:
            if e.is_active_signal:
                active_slots.add(e.slot)
        mech_result.active_master_slots = sorted(active_slots)

        mech_result.diag_entry_count = sum(1 for e in all_entries if e.source == "diagnostic")
        mech_result.journal_entry_count = sum(1 for e in all_entries if e.source == "journal")

        return mech_result

    # ── 诊断日志扫描 ──────────────────────────────────────

    def _scan_diag_entries(
        self, log_entry: LogEntry, slot_id: str,
        diag_re: re.Pattern, seq_re: re.Pattern,
        master_keyword: re.Pattern | None,
        name_map: dict[str, str],
        mod_upper: str,
    ) -> list[MechLogEntry]:
        entries: list[MechLogEntry] = []
        text = self._read_log_entry(log_entry)
        if not text:
            return entries

        for line in text.splitlines():
            if mod_upper not in line:
                continue
            m = diag_re.search(line)
            if not m:
                continue

            slot = m.group("Slot")
            cpu_id = m.group("CPU_Id")
            raw_proc_name = m.group("ProcessName")
            context = m.group("Context")

            proc_name, pid = self._parse_diag_proc_name(raw_proc_name, name_map)

            sm = seq_re.search(line)
            if not sm:
                continue
            try:
                seq = int(sm.group(1))
            except ValueError:
                continue

            is_active = bool(master_keyword and master_keyword.search(context))
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

    # ── journal 日志扫描 ──────────────────────────────────

    def _scan_journal_entries(
        self, ps: PrivateSlotInfo,
        journal_re: re.Pattern, journal_re2: re.Pattern | None,
        journal_keyword: str,
        seq_re: re.Pattern, master_keyword: re.Pattern | None,
        name_map: dict[str, str], indicator: str | None,
        mod_upper: str, tzinfo: Any,
    ) -> list[MechLogEntry]:
        entries: list[MechLogEntry] = []

        for jl in ps.journal_logs:
            text = self._ts_extractor._read_file(Path(jl.path))
            if not text:
                continue

            for line in text.splitlines():
                if mod_upper not in line:
                    continue
                if journal_keyword not in line.lower():
                    continue

                m = journal_re.match(line) if journal_re else None
                if not m and journal_re2:
                    m = journal_re2.match(line)
                if not m:
                    continue

                raw_name = m.group(1)
                raw_pid = m.group(2)
                seq_str = m.group(3)
                context = m.group(4)

                try:
                    seq = int(seq_str)
                except ValueError:
                    seq = 0
                pid = raw_pid or ""

                if pid and indicator and indicator in raw_name.lower():
                    proc_name = None
                    for diag_n, jnl_name in name_map.items():
                        if jnl_name.lower() == raw_name.lower():
                            proc_name = diag_n
                            break
                    if proc_name is None:
                        proc_name = raw_name
                else:
                    proc_name = raw_name
                    if "-" in raw_name and not pid:
                        parts = raw_name.rsplit("-", 1)
                        if parts[-1].isdigit():
                            proc_name = parts[0]
                            pid = parts[-1]

                is_active = bool(master_keyword and master_keyword.search(context))
                ts = self._extract_first_ts(line)
                if ts and ts.tzinfo is None and tzinfo is not None:
                    ts = ts.replace(tzinfo=tzinfo)

                src_file = f"{ps.dir_name}/{jl.name}"
                entries.append(MechLogEntry(
                    timestamp=ts, source="journal",
                    source_file=src_file,
                    slot=ps.slot_id, cpu_id=ps.cpu_id or "",
                    process_name=proc_name, pid=pid,
                    context=context, sequence=seq,
                    is_active_signal=is_active, raw=line.strip()[:500],
                ))

        return entries

    # ── 进程名解析 ────────────────────────────────────────

    @staticmethod
    def _parse_diag_proc_name(raw: str, name_map: dict[str, str]) -> tuple[str, str]:
        for diag_n in name_map:
            if raw.startswith(diag_n):
                rest = raw[len(diag_n):]
                pid = rest[1:] if rest.startswith("-") else ""
                return diag_n, pid
        if "-" in raw:
            parts = raw.rsplit("-", 1)
            if parts[-1].isdigit():
                return parts[0], parts[-1]
        return raw, ""

    # ── 时间戳工具 ────────────────────────────────────────

    def _extract_first_ts(self, line: str) -> datetime | None:
        stamps = self._ts_extractor.extract_from_text(line)
        return stamps[0] if stamps else None

    # ── 文件读取 ──────────────────────────────────────────

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
        # 未压缩文件：直接读取
        file_path = Path(log_entry.path)
        if file_path.is_file():
            return self._ts_extractor._read_file(file_path)
        return ""

    # ── 角色判定 ──────────────────────────────────────────

    @staticmethod
    def _apply_mech_roles(mech_result: MechResult, result: ParseResult) -> None:
        if not mech_result.active_master_slots:
            return
        for slot in result.diagnostic_slots:
            if slot.slot_id in mech_result.active_master_slots:
                slot.role = BoardRole.ACTIVE

    @staticmethod
    def _fallback_roles(result: ParseResult) -> None:
        for slot in result.diagnostic_slots:
            if slot.role != BoardRole.UNKNOWN:
                continue
            if slot.active_periods:
                slot.role = BoardRole.ACTIVE
            elif slot.diagnostic_logs:
                slot.role = BoardRole.STANDBY
            else:
                slot.role = BoardRole.UNKNOWN

    # ── 输出落盘 ──────────────────────────────────────────

    def write_output(
        self, mech_result: MechResult, output_dir: Path,
    ) -> Path:
        mech_dir = output_dir / "mech_modules" / mech_result.module_name
        mech_dir.mkdir(parents=True, exist_ok=True)
        for slot in mech_result.slots:
            for cycle in slot.board_cycles:
                cycle_dir = mech_dir / f"slot_{slot.slot_id}" / cycle.dir_name
                cpu_procs: dict[str, list] = {}
                for proc in cycle.processes:
                    cpu_id = proc.logs[0].cpu_id if proc.logs else None
                    key = cpu_id or ""
                    cpu_procs.setdefault(key, []).append(proc)

                for cpu_key, procs in cpu_procs.items():
                    out_dir = cycle_dir
                    if cpu_key:
                        out_dir = cycle_dir / f"cpu_{cpu_key}"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    for proc in procs:
                        fname = f"{proc.process_name}-{proc.pid}.log"
                        out_path = out_dir / fname
                        with open(out_path, "w", encoding="utf-8") as fh:
                            for log in proc.logs:
                                seq = f"[{log.sequence:04d}]" if log.sequence else "[....]"
                                fh.write(
                                    f"{seq} [{log.source}|{log.source_file}] {log.raw}\n"
                                )
        return mech_dir
