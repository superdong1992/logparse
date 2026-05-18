from __future__ import annotations

import gzip
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import warnings

from backend.config import ConfigLoader
from backend.models import (
    MechBoardCycle,
    MechLogEntry,
    MechProcessLifecycle,
    MechResult,
    MechSlotOutput,
    BoardRole,
    LogEntry,
    ParseResult,
    PrivateSlotInfo,
)


class MechParser:
    """机制模块日志解析器。遍历所有启用的模块配置，分别解析。"""

    def __init__(self, config_loader: ConfigLoader):
        warnings.warn(
            "MechParser is deprecated, use Pipeline with ParserPlugin instead",
            DeprecationWarning, stacklevel=2,
        )
        self.config = config_loader
        self.verbose = False
        self.debug_filter = ""  # 进程名子串过滤，空=全量

    def _dbg(self, *args):
        if self.verbose:
            line = " ".join(str(a) for a in args)
            # 如有 debug_filter 则只打印匹配行
            if self.debug_filter:
                needle = self.debug_filter.lower()
                if needle not in line.lower():
                    return
            print(line)

    def parse_all(self, result: ParseResult) -> dict[str, MechResult]:
        """解析所有启用的机制模块。返回 {module_name: MechResult}。"""
        modules = self.config.get_config().mechanism_modules
        results: dict[str, MechResult] = {}
        for module_key, cfg in modules.items():
            if not cfg.enabled or not cfg.module_name:
                continue
            mech = self._parse_one(result, cfg, module_key)
            if mech:
                results[module_key] = mech
        return results

    def _parse_one(self, result: ParseResult, cfg, module_key: str) -> MechResult | None:
        module_name = cfg.module_name
        mod_upper = module_name.upper()  # Stage 1 预过滤关键字

        diag_re = re.compile(cfg.diag_pattern) if cfg.diag_pattern else None
        if diag_re:
            required = {"Slot", "CPU_Id", "ProcessName", "Context"}
            if not required.issubset(diag_re.groupindex):
                return None
        journal_re = re.compile(cfg.journal.line_pattern) if cfg.journal.line_pattern else None
        journal_re2 = re.compile(cfg.journal.line_pattern2) if cfg.journal.line_pattern2 else None
        journal_keyword = cfg.journal.identifying_keyword.lower() if cfg.journal.identifying_keyword else None
        seq_re = re.compile(cfg.sequence_pattern)
        master_keyword = re.compile(cfg.active_master_keyword) if cfg.active_master_keyword else None
        indicator = cfg.board_restart_indicator.lower() if cfg.board_restart_indicator else None
        name_map = cfg.process_name_mapping

        all_entries: list[MechLogEntry] = []

        # 扫描诊断日志
        if diag_re:
            for slot in result.diagnostic_slots:
                for log_entry in slot.diagnostic_logs:
                    all_entries.extend(
                        self._scan_diag(log_entry, slot.slot_id, diag_re, seq_re,
                                        master_keyword, name_map, mod_upper)
                    )

        # 扫描 journal 日志（先用诊断已有的 tzinfo 做初轮对齐）
        diag_tzinfo = None
        for e in all_entries:
            if e.timestamp and e.timestamp.tzinfo:
                diag_tzinfo = e.timestamp.tzinfo
                break

        if (journal_re or journal_re2) and journal_keyword:
            for ps in result.private_slots:
                all_entries.extend(
                    self._scan_journal(ps, journal_re, journal_re2, journal_keyword, seq_re,
                                       master_keyword, name_map, indicator, mod_upper,
                                       diag_tzinfo)
                )

        if not all_entries:
            return None

        # 从全部条目中检测时区，统一归一化
        tzinfo = None
        for e in all_entries:
            if e.timestamp and e.timestamp.tzinfo:
                tzinfo = e.timestamp.tzinfo
                break
        if tzinfo:
            for e in all_entries:
                if e.timestamp and e.timestamp.tzinfo is None:
                    e.timestamp = e.timestamp.replace(tzinfo=tzinfo)

        by_slot: dict[str, list[MechLogEntry]] = defaultdict(list)
        for e in all_entries:
            by_slot[e.slot].append(e)

        mech_result = MechResult(module_name=module_name)
        for slot_id, entries in sorted(by_slot.items()):
            slot_output = MechSlotOutput(slot_id=slot_id)
            slot_output.board_cycles = self._build_cycles(entries, indicator)
            mech_result.slots.append(slot_output)

        active_slots: set[str] = set()
        for e in all_entries:
            if e.is_active_signal:
                active_slots.add(e.slot)
        mech_result.active_master_slots = sorted(active_slots)

        mech_result.diag_entry_count = sum(1 for e in all_entries if e.source == "diagnostic")
        mech_result.journal_entry_count = sum(1 for e in all_entries if e.source == "journal")

        return mech_result

    def _scan_diag(
        self, log_entry: LogEntry, slot_id: str,
        diag_re: re.Pattern, seq_re: re.Pattern,
        master_keyword: re.Pattern | None, name_map: dict[str, str],
        mod_upper: str,
    ) -> list[MechLogEntry]:
        """扫描诊断日志：Stage 1 模块名预过滤 → Stage 2 完整正则。"""
        entries: list[MechLogEntry] = []
        text = self._read_extracted(log_entry)
        if not text:
            return entries

        for line in text.splitlines():
            # Stage 1: 字符串预过滤
            if mod_upper not in line:
                continue
            # Stage 2: 完整正则
            m = diag_re.search(line)
            if not m:
                continue

            slot = m.group("Slot")
            cpu_id = m.group("CPU_Id")
            raw_proc_name = m.group("ProcessName")
            context = m.group("Context")

            proc_name, pid = self._parse_proc_name(raw_proc_name, name_map)

            sm = seq_re.search(line)
            if not sm:
                continue
            try:
                seq = int(sm.group(1))
            except ValueError:
                continue

            is_active = bool(master_keyword and master_keyword.search(context))
            ts = self._extract_ts(line)

            src_file = f"slot_{slot_id}/{log_entry.name}"
            entries.append(MechLogEntry(
                timestamp=ts, source="diagnostic",
                source_file=src_file,
                slot=slot, cpu_id=cpu_id,
                process_name=proc_name, pid=pid,
                context=context, sequence=seq,
                is_active_signal=is_active, raw=line.strip()[:500],
            ))
            self._dbg(f"[DEBUG] _scan_diag slot={slot} cpu={cpu_id} name={proc_name} pid={pid} seq={seq} ts={ts} src=diag")

        return entries

    def _scan_journal(
        self, ps: PrivateSlotInfo,
        journal_re: re.Pattern, journal_re2: re.Pattern | None, journal_keyword: str,
        seq_re: re.Pattern, master_keyword: re.Pattern | None,
        name_map: dict[str, str], indicator: str | None,
        mod_upper: str,
        tzinfo=None,
    ) -> list[MechLogEntry]:
        """扫描 journal：Stage 1 模块名 + keyword 预过滤 → Stage 2 完整正则。"""
        entries: list[MechLogEntry] = []

        for jl in ps.journal_logs:
            text = self._read_file(Path(jl.path))
            if not text:
                continue

            for line in text.splitlines():
                # Stage 1: 模块名 + identifying_keyword 预过滤
                if mod_upper not in line:
                    continue
                if journal_keyword not in line.lower():
                    continue
                # Stage 2: 正则（格式1优先，格式2兜底）
                m = journal_re.match(line)
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
                ts = self._extract_ts(line)
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
                self._dbg(f"[DEBUG] _scan_journal slot={ps.slot_id} cpu={ps.cpu_id or '0'} name={proc_name} pid={pid} seq={seq} ts={ts} src=journal")

        return entries

    # 序号回绕判定阈值：当前 seq 比历史最大 seq 小超过此值才视为重启回绕
    SEQ_ROLLBACK_THRESHOLD = 3

    def _find_seq_wrap_boundary(
        self, group: list[MechLogEntry], search_end: int, search_start: int,
        max_seq: dict[tuple[str, str], int],
    ) -> int:
        """从 search_end 向 search_start 扫描，找最早的序号回绕位置作为重启边界。"""
        boundary = search_end + 1  # 默认在 indicator 位置切分
        for j in range(search_end, search_start - 1, -1):
            e = group[j]
            if e.sequence > 0:
                key = (e.process_name.lower(), e.pid or "")
                prev_max = max_seq.get(key, 0)
                if prev_max - e.sequence > self.SEQ_ROLLBACK_THRESHOLD:
                    boundary = j
                    self._dbg(f"[DEBUG] _find_seq_wrap_boundary rollback at j={j} key={key} seq={e.sequence} prev_max={prev_max}")
        return boundary

    def _build_cycles(
        self, entries: list[MechLogEntry], indicator: str | None,
    ) -> list[MechBoardCycle]:
        if not entries:
            return []

        # 按 (slot, cpu_key) 分组，cpu_key="" 为板卡本身
        by_cpu: dict[str, list[MechLogEntry]] = defaultdict(list)
        for e in entries:
            cpu_key = e.cpu_id or ""
            by_cpu[cpu_key].append(e)

        for cpu_key in by_cpu:
            by_cpu[cpu_key].sort(key=lambda e: (
                0 if e.timestamp else 1,
                e.timestamp.timestamp() if e.timestamp else 0,
                e.sequence,
            ))

        # 板卡级 split 时间戳和索引（传播到子 cpu 组时需要时间戳对齐）
        board_splits: list[datetime] = []
        if "" in by_cpu and indicator:
            group = by_cpu[""]
            max_seq: dict[tuple[str, str], int] = {}
            seg_start = 0
            prev_pid = None
            for i, e in enumerate(group):
                if e.sequence > 0:
                    key = (e.process_name.lower(), e.pid or "")
                    max_seq[key] = max(max_seq.get(key, 0), e.sequence)
                if indicator in e.process_name.lower():
                    self._dbg(f"[DEBUG] _build_cycles board_indicator cpu= slot={e.slot} name={e.process_name} pid={e.pid} i={i} prev_pid={prev_pid}")
                    if prev_pid and e.pid and prev_pid != e.pid:
                        boundary = self._find_seq_wrap_boundary(group, i - 1, seg_start, max_seq)
                        board_splits.append(group[boundary].timestamp if group[boundary].timestamp else e.timestamp)
                        seg_start = boundary
                        self._dbg(f"[DEBUG] _build_cycles board_pid_changed prev={prev_pid} curr={e.pid} boundary={boundary}")
                    if e.pid:
                        prev_pid = e.pid

        cycles: list[MechBoardCycle] = []
        for cpu_key in sorted(by_cpu.keys()):
            group = by_cpu[cpu_key]
            max_seq: dict[tuple[str, str], int] = {}

            # cpu 级 split 索引
            local_splits: set[int] = set()
            seg_start = 0
            if indicator:
                prev_pid = None
                for i, e in enumerate(group):
                    if e.sequence > 0:
                        key = (e.process_name.lower(), e.pid or "")
                        max_seq[key] = max(max_seq.get(key, 0), e.sequence)
                    if indicator in e.process_name.lower():
                        self._dbg(f"[DEBUG] _build_cycles local_indicator cpu={cpu_key!r} name={e.process_name} pid={e.pid} i={i} prev_pid={prev_pid}")
                        if prev_pid and e.pid and prev_pid != e.pid:
                            boundary = self._find_seq_wrap_boundary(group, i - 1, seg_start, max_seq)
                            local_splits.add(boundary)
                            seg_start = boundary
                            self._dbg(f"[DEBUG] _build_cycles local_pid_changed cpu={cpu_key!r} prev={prev_pid} curr={e.pid} boundary={boundary}")
                        if e.pid:
                            prev_pid = e.pid

            # 板卡重启 → 子 cpu 组同步切分（按时间戳对齐，再做反向扫描修正）
            if cpu_key != "" and board_splits:
                for split_ts in board_splits:
                    if split_ts is None:
                        continue
                    for i, e in enumerate(group):
                        if e.timestamp and e.timestamp >= split_ts:
                            boundary = self._find_seq_wrap_boundary(group, i, seg_start, max_seq)
                            local_splits.add(boundary)
                            seg_start = boundary
                            break

            all_splits = sorted(local_splits)
            seg_start = 0
            for split_i in all_splits:
                if split_i > seg_start:
                    cycles.extend(self._make_cycles(group[seg_start:split_i], indicator))
                seg_start = split_i
            if seg_start < len(group):
                cycles.extend(self._make_cycles(group[seg_start:], indicator))

        return cycles

    def _make_cycles(self, entries: list[MechLogEntry], indicator: str | None = None) -> list[MechBoardCycle]:
        if not entries:
            return []
        procs = self._build_processes(entries)
        indicator_times = [e.timestamp for e in entries
                          if e.timestamp and (not indicator or indicator in e.process_name.lower())]
        all_times = [e.timestamp for e in entries if e.timestamp]
        times = indicator_times if indicator_times else all_times
        start = min(times) if times else None
        end = max(times) if times else None
        dir_name = self._fmt_dir(start, end)
        self._dbg(f"[DEBUG] _make_cycles indicator={indicator} indicator_times={len(indicator_times)} all_times={len(all_times)} start={start} end={end} dir={dir_name}")
        # 详细列出所有进程及其日志条数
        for p in procs:
            self._dbg(f"[DEBUG] _make_cycles proc name={p.process_name} pid={p.pid} total={p.total_count} missing={p.missing_sequences}")
        # 同名进程多实例检测
        name_groups: dict[str, list] = defaultdict(list)
        for p in procs:
            name_groups[p.process_name].append(p)
        for name, group in name_groups.items():
            if len(group) > 1:
                details = [(p.pid, p.total_count) for p in group]
                self._dbg(f"[DEBUG] _make_cycles multi_instance name={name} PIDs={details}")
        return [MechBoardCycle(dir_name=dir_name, start_time=start, end_time=end, processes=procs)]

    def _build_processes(self, entries: list[MechLogEntry]) -> list[MechProcessLifecycle]:
        by_key: dict[tuple[str, str], list[MechLogEntry]] = defaultdict(list)
        for e in entries:
            by_key[(e.process_name, e.pid)].append(e)

        lifecycles: list[MechProcessLifecycle] = []
        for (proc_name, pid), logs in sorted(by_key.items()):
            logs.sort(key=lambda e: e.sequence)
            seqs = [l.sequence for l in logs if l.sequence > 0]
            missing: list[int] = []
            if len(seqs) >= 2:
                full = set(range(min(seqs), max(seqs) + 1))
                missing = sorted(full - set(seqs))
            lifecycles.append(MechProcessLifecycle(
                process_name=proc_name, pid=pid, logs=logs,
                total_count=len(logs), missing_sequences=missing,
            ))
        return lifecycles

    def _parse_proc_name(self, raw: str, name_map: dict[str, str]) -> tuple[str, str]:
        pid = ""
        proc_name = raw
        for diag_n, jnl_name in name_map.items():
            if raw.startswith(diag_n):
                proc_name = diag_n
                rest = raw[len(diag_n):]
                if rest.startswith("-"):
                    pid = rest[1:]
                return proc_name, pid

        if "-" in raw:
            parts = raw.rsplit("-", 1)
            if parts[-1].isdigit():
                return parts[0], parts[-1]
        return raw, ""

    def _extract_ts(self, line: str) -> datetime | None:
        stamps = self.config.extract_content_timestamps(line)
        return stamps[0] if stamps else None

    def _read_extracted(self, log_entry: LogEntry) -> str:
        if log_entry.extracted_path:
            ext_dir = Path(log_entry.extracted_path)
            if ext_dir.is_dir():
                parts: list[str] = []
                for f in sorted(ext_dir.rglob("*")):
                    if f.is_file():
                        try:
                            parts.append(f.read_text(encoding="utf-8", errors="replace"))
                        except Exception:
                            try:
                                parts.append(f.read_text(encoding="gbk", errors="replace"))
                            except Exception:
                                import logging
                                logging.getLogger(__name__).warning(
                                    "无法读取文件 %s (UTF-8/GBK 均失败)", f
                                )
                return "\n".join(parts)
        return ""

    def _read_file(self, file_path: Path) -> str:
        if not file_path.exists():
            return ""
        try:
            if file_path.suffix == ".gz":
                try:
                    return gzip.open(file_path, "rt", encoding="utf-8", errors="replace").read()
                except Exception:
                    import logging
                    logging.getLogger(__name__).warning(
                        "gzip 解压失败，跳过: %s", file_path
                    )
                    return ""
            return file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            try:
                return file_path.read_text(encoding="gbk", errors="replace")
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "无法读取文件 (UTF-8/GBK 均失败): %s", file_path
                )
                return ""

    @staticmethod
    def _fmt_dir(start: datetime | None, end: datetime | None) -> str:
        if start and end:
            return f"{start.strftime('%Y%m%dT%H%M%S')}-{end.strftime('%Y%m%dT%H%M%S')}"
        if start:
            return start.strftime('%Y%m%dT%H%M%S')
        return "unknown"

    def write_output(self, mech_result: MechResult, output_dir: Path) -> Path:
        """落盘：slot/周期/cpu_N(非0时)/{进程名}-{pid}.log"""
        mech_dir = output_dir / "mech_modules" / mech_result.module_name
        mech_dir.mkdir(parents=True, exist_ok=True)
        for slot in mech_result.slots:
            for cycle in slot.board_cycles:
                cycle_dir = mech_dir / f"slot_{slot.slot_id}" / cycle.dir_name
                # 按 cpu_id 分组
                cpu_procs: dict[str, list] = {}
                for proc in cycle.processes:
                    cpu_id = proc.logs[0].cpu_id if proc.logs else None
                    key = cpu_id or ""
                    cpu_procs.setdefault(key, []).append(proc)

                for cpu_key, procs in cpu_procs.items():
                    out_dir = cycle_dir
                    if cpu_key:  # 仅 cpu_1, cpu_2, ... 建子目录
                        out_dir = cycle_dir / f"cpu_{cpu_key}"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    for proc in procs:
                        fname = f"{proc.process_name}-{proc.pid}.log"
                        out_path = out_dir / fname
                        with open(out_path, "w", encoding="utf-8") as fh:
                            for log in proc.logs:
                                seq = f"[{log.sequence:04d}]" if log.sequence else "[....]"
                                fh.write(f"{seq} [{log.source}|{log.source_file}] {log.raw}\n")
                        self._dbg(f"[DEBUG] write_output path={out_path} name={proc.process_name} pid={proc.pid} cpu={cpu_id} line_count={proc.total_count}")
        return mech_dir

    def apply_to_identifier(self, mech_result: MechResult, result: ParseResult) -> None:
        if not mech_result.active_master_slots:
            return
        for slot in result.diagnostic_slots:
            if slot.slot_id in mech_result.active_master_slots:
                slot.role = BoardRole.ACTIVE
