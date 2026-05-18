"""重启周期检测：PID 变化 + 序号回绕反向扫描。"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from backend.models import MechBoardCycle, MechLogEntry, MechProcessLifecycle

SEQ_ROLLBACK_THRESHOLD = 3


class CycleDetector:
    def __init__(self, indicator: str | None = None):
        self._indicator = indicator

    def detect(self, entries: list[MechLogEntry]) -> list[MechBoardCycle]:
        return self._build_cycles(entries, self._indicator)

    def _build_cycles(
        self, entries: list[MechLogEntry], indicator: str | None,
    ) -> list[MechBoardCycle]:
        if not entries:
            return []

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
                    if prev_pid and e.pid and prev_pid != e.pid:
                        boundary = self._find_seq_wrap_boundary(
                            group, i - 1, seg_start, max_seq,
                        )
                        board_splits.append(
                            group[boundary].timestamp if group[boundary].timestamp else e.timestamp
                        )
                        seg_start = boundary
                    if e.pid:
                        prev_pid = e.pid

        cycles: list[MechBoardCycle] = []
        for cpu_key in sorted(by_cpu.keys()):
            group = by_cpu[cpu_key]
            max_seq: dict[tuple[str, str], int] = {}

            local_splits: set[int] = set()
            seg_start = 0
            if indicator:
                prev_pid = None
                for i, e in enumerate(group):
                    if e.sequence > 0:
                        key = (e.process_name.lower(), e.pid or "")
                        max_seq[key] = max(max_seq.get(key, 0), e.sequence)
                    if indicator in e.process_name.lower():
                        if prev_pid and e.pid and prev_pid != e.pid:
                            boundary = self._find_seq_wrap_boundary(
                                group, i - 1, seg_start, max_seq,
                            )
                            local_splits.add(boundary)
                            seg_start = boundary
                        if e.pid:
                            prev_pid = e.pid

            if cpu_key != "" and board_splits:
                for split_ts in board_splits:
                    if split_ts is None:
                        continue
                    for i, e in enumerate(group):
                        if e.timestamp and e.timestamp >= split_ts:
                            boundary = self._find_seq_wrap_boundary(
                                group, i, seg_start, max_seq,
                            )
                            local_splits.add(boundary)
                            seg_start = boundary
                            break

            all_splits = sorted(local_splits)
            seg_start = 0
            for split_i in all_splits:
                if split_i > seg_start:
                    cycles.extend(self._make_cycles(group[seg_start:split_i]))
                seg_start = split_i
            if seg_start < len(group):
                cycles.extend(self._make_cycles(group[seg_start:]))

        return cycles

    @staticmethod
    def _find_seq_wrap_boundary(
        group: list[MechLogEntry], search_end: int, search_start: int,
        max_seq: dict[tuple[str, str], int],
    ) -> int:
        boundary = search_end + 1
        for j in range(search_end, search_start - 1, -1):
            e = group[j]
            if e.sequence > 0:
                key = (e.process_name.lower(), e.pid or "")
                prev_max = max_seq.get(key, 0)
                if prev_max - e.sequence > SEQ_ROLLBACK_THRESHOLD:
                    boundary = j
        return boundary

    @staticmethod
    def _make_cycles(entries: list[MechLogEntry]) -> list[MechBoardCycle]:
        if not entries:
            return []
        procs = CycleDetector._build_processes(entries)
        times = [e.timestamp for e in entries if e.timestamp]
        start = min(times) if times else None
        end = max(times) if times else None
        dir_name = CycleDetector._fmt_dir(start, end)
        return [MechBoardCycle(
            dir_name=dir_name, start_time=start, end_time=end,
            processes=procs,
        )]

    @staticmethod
    def _build_processes(
        entries: list[MechLogEntry],
    ) -> list[MechProcessLifecycle]:
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

    @staticmethod
    def _fmt_dir(start: datetime | None, end: datetime | None) -> str:
        if start and end:
            return f"{start.strftime('%Y%m%dT%H%M%S')}-{end.strftime('%Y%m%dT%H%M%S')}"
        if start:
            return start.strftime('%Y%m%dT%H%M%S')
        return "unknown"
