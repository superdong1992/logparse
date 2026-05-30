"""重启周期检测：基于 indicator PID 变化 + 白名单进程安全切分。

算法概述（三步切分）：

  Step 1 - 检测板卡重启
    indicator 进程 PID 变化 → 判定为板卡重启。
    indicator 是配置选定的非独立重启进程，PID 变化仅发生在板卡重启时。

  Step 2 - 确定安全切分点
    仅参考白名单进程（不重名、不支持独立重启）的 PID 信息：
    - old_pid_end = 白名单内所有进程旧 PID 最后一条时间戳的最大值
    - new_pid_start = 白名单内所有进程新 PID 第一条时间戳的最小值
    - 初始切分点 = old_pid_end（保证旧 PID 段不被拆断）
    若 old_pid_end > new_pid_start（进程拉起时间重叠），优先保证同 PID 完整性。

  Step 3 - Journal 序号前移
    对白名单内每个进程：
    a) 从诊断日志获取旧 PID 的最后一个 No（如 No[500]）
    b) 在该进程的全部条目（诊断 + journal）中找序号跳变（从 ~500 跳到小号）
    c) 跳变后第一条的时间戳 = 该进程的候选前移点
    d) journal_earliest = 所有进程候选前移点的最小值
    前移约束：最终切分点 = max(journal_earliest, old_pid_end)

  非白名单进程不参与切分点计算，按最终切分点被动分配。

  详见: docs/superpowers/specs/2026-05-22-cycle-split-algorithm-design.md
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from backend.models import (
    MechBoardCycle,
    MechBoundaryIssue,
    MechCpuCycle,
    MechCycleSplitTrace,
    MechLogEntry,
    MechProcessLifecycle,
)

logger = logging.getLogger(__name__)

SEQ_JUMP_THRESHOLD = 3


class CycleDetector:
    """重启周期检测器。

    Args:
        indicator: 板卡重启标识进程名（小写），PID 变化时触发切分。
        whitelist: 参与切分点计算的白名单进程名列表（小写），
                   这些进程不重名且不支持独立重启。
    """

    def __init__(
        self,
        indicator: str | None = None,
        whitelist: list[str] | None = None,
        module_key: str | None = None,
        module_name: str | None = None,
    ):
        self._indicator = indicator
        self._whitelist = [w.lower() for w in (whitelist or [])]
        self._module_key = module_key or ""
        self._module_name = module_name or ""
        self._split_traces: list[MechCycleSplitTrace] = []
        self.errors: list[str] = []
        self._diagnostics_seen: set[str] = set()
        self.lifecycle_reliable: bool = True
        self.boundary_issues: list[MechBoundaryIssue] = []

    def detect(self, entries: list[MechLogEntry]) -> list[MechBoardCycle]:
        """检测重启周期，返回按时间排列的周期列表。"""
        self._split_traces = []
        self.errors = []
        self._diagnostics_seen = set()
        self.lifecycle_reliable = True
        self.boundary_issues = []
        logger.info(
            "CycleDetector.detect: 共 %d 条日志, indicator=%r, whitelist=%s",
            len(entries), self._indicator, self._whitelist,
        )
        return self._build_cycles(entries)

    # ── 主流程 ──────────────────────────────────────────────

    def _build_cycles(self, entries: list[MechLogEntry]) -> list[MechBoardCycle]:
        if not entries:
            return []

        # 按 (slot, cpu_key) 分组
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

        logger.info("按 cpu_key 分组: %s", {k: len(v) for k, v in by_cpu.items()})

        if not self._indicator:
            logger.warning("indicator 为空，不做 PID 切分，整体作为一个周期")
            all_entries = [e for group in by_cpu.values() for e in group]
            flat_cycles = self._make_cycles(all_entries) if all_entries else []
            return self._nest_cpu_cycles(flat_cycles, all_entries)

        segments_by_bounds: dict[tuple[datetime | None, datetime | None], list[MechLogEntry]] = defaultdict(list)

        # 板卡级（cpu_key=""）的切分时间点会作用于所有 CPU。
        board_splits: list[datetime] = []
        if "" in by_cpu:
            board_splits = self._detect_splits_for_group(by_cpu[""])

        for cpu_key in sorted(by_cpu.keys()):
            if cpu_key == "":
                group_splits = board_splits
            else:
                # 子卡：板卡级切分 + 自身 PID 变化切分。子卡自身切分不应反向切碎板卡日志或其他 CPU。
                cpu_splits = self._detect_splits_for_group(by_cpu[cpu_key])
                group_splits = board_splits + cpu_splits
                if cpu_splits:
                    diag_slot = by_cpu[cpu_key][0].slot if by_cpu[cpu_key] else "-"
                    for split_ts in cpu_splits:
                        evidence = self._context_evidence(by_cpu[cpu_key], split_ts)
                        self._record_split_diagnostic(
                            "scoped_cpu_split",
                            slot=diag_slot,
                            scope=f"cpu:{cpu_key}",
                            split=split_ts,
                            reason="cpu_local_split",
                            evidence=evidence,
                            suggested_commands=self._suggested_commands(
                                evidence=evidence,
                                slot=diag_slot,
                            ),
                        )
                if cpu_splits:
                    logger.info(
                        "cpu_key=%r 切分时间点(%d): %s",
                        cpu_key, len(cpu_splits),
                        [ts.isoformat() for ts in cpu_splits],
                    )

            unique_splits = sorted(set(ts for ts in group_splits if ts is not None))
            logger.info(
                "cpu_key=%r 作用域切分时间点(%d): %s",
                cpu_key, len(unique_splits),
                [ts.isoformat() for ts in unique_splits],
            )

            group_entries = by_cpu[cpu_key]
            if unique_splits:
                unique_splits = self._refine_split_timestamps(group_entries, unique_splits)
                unique_splits = self._enforce_protected_pid_boundaries(group_entries, unique_splits)

            if not unique_splits:
                if group_entries:
                    segments_by_bounds[(None, None)].extend(group_entries)
                continue

            segments = self._segment_by_timestamps_with_bounds(group_entries, unique_splits)
            for lower_bound, upper_bound, seg in segments:
                if seg:
                    segments_by_bounds[(lower_bound, upper_bound)].extend(seg)

        cycles: list[MechBoardCycle] = []
        for _bounds, segment_entries in sorted(
            segments_by_bounds.items(),
            key=lambda item: (
                0 if item[0][0] is None else 1,
                item[0][0].timestamp() if item[0][0] else 0,
                0 if item[0][1] is None else 1,
                item[0][1].timestamp() if item[0][1] else 0,
            ),
        ):
            cycles.extend(self._make_cycles(segment_entries))

        cycles.sort(key=lambda c: (
            0 if c.start_time else 1,
            c.start_time.timestamp() if c.start_time else 0,
            c.dir_name,
        ))
        cycles = self._nest_cpu_cycles(cycles, entries)

        # 将 split traces 分配到对应周期
        self._record_over_split_diagnostics(cycles)
        self._assign_split_traces(cycles)

        logger.info("最终切分结果: %d 个周期", len(cycles))
        for i, c in enumerate(cycles):
            logger.info("  周期[%d]: %s, %d 个进程组", i, c.dir_name, len(c.processes))

        return cycles

    # ── 单组切分检测（核心算法）───────────────────────────────

    def _nest_cpu_cycles(
        self,
        flat_cycles: list[MechBoardCycle],
        all_entries: list[MechLogEntry],
    ) -> list[MechBoardCycle]:
        board_cycles: list[MechBoardCycle] = []
        pending_cpu_cycles: list[MechCpuCycle] = []

        for cycle in flat_cycles:
            board_processes: list[MechProcessLifecycle] = []
            cpu_processes: dict[str, list[MechProcessLifecycle]] = defaultdict(list)
            for process in cycle.processes:
                cpu_id = self._process_cpu_id(process)
                if cpu_id:
                    cpu_processes[cpu_id].append(process)
                else:
                    board_processes.append(process)

            if board_processes:
                board_cycle = cycle.model_copy(update={
                    "processes": board_processes,
                    "cpu_cycles": [],
                })
                board_cycles.append(board_cycle)
                for cpu_id, processes in sorted(cpu_processes.items()):
                    board_cycle.cpu_cycles.append(
                        self._make_cpu_cycle_from_processes(cpu_id, cycle, processes)
                    )
            else:
                for cpu_id, processes in sorted(cpu_processes.items()):
                    pending_cpu_cycles.append(
                        self._make_cpu_cycle_from_processes(cpu_id, cycle, processes)
                    )

        if not board_cycles and all_entries:
            board_entries = [entry for entry in all_entries if not entry.cpu_id]
            times = [entry.timestamp for entry in all_entries if entry.timestamp]
            start = min(times) if times else None
            end = max(times) if times else None
            sequence_mode = self._sequence_mode(board_entries)
            board_cycles = [
                MechBoardCycle(
                    dir_name=self._fmt_dir(start, end),
                    start_time=start,
                    end_time=end,
                    processes=self._build_processes(board_entries, sequence_mode) if board_entries else [],
                )
            ]

        for cpu_cycle in pending_cpu_cycles:
            parent = self._select_parent_board_cycle(board_cycles, cpu_cycle)
            if parent is None:
                board_cycles.append(MechBoardCycle(
                    dir_name=cpu_cycle.dir_name,
                    start_time=cpu_cycle.start_time,
                    end_time=cpu_cycle.end_time,
                    cpu_cycles=[cpu_cycle],
                ))
            else:
                parent.cpu_cycles.append(cpu_cycle)

        for cycle in board_cycles:
            cycle.cpu_cycles.sort(key=lambda c: (
                c.cpu_id,
                0 if c.start_time else 1,
                c.start_time.timestamp() if c.start_time else 0,
                c.dir_name,
            ))

        board_cycles.sort(key=lambda c: (
            0 if c.start_time else 1,
            c.start_time.timestamp() if c.start_time else 0,
            c.dir_name,
        ))
        return board_cycles

    @staticmethod
    def _process_cpu_id(process: MechProcessLifecycle) -> str:
        for log in process.logs:
            if log.cpu_id:
                return log.cpu_id
        return ""

    @staticmethod
    def _make_cpu_cycle_from_processes(
        cpu_id: str,
        source_cycle: MechBoardCycle,
        processes: list[MechProcessLifecycle],
    ) -> MechCpuCycle:
        return MechCpuCycle(
            cpu_id=cpu_id,
            dir_name=source_cycle.dir_name,
            start_time=source_cycle.start_time,
            end_time=source_cycle.end_time,
            processes=processes,
        )

    @staticmethod
    def _select_parent_board_cycle(
        board_cycles: list[MechBoardCycle],
        cpu_cycle: MechCpuCycle,
    ) -> MechBoardCycle | None:
        if not board_cycles:
            return None
        ref = cpu_cycle.start_time or cpu_cycle.end_time
        if ref is None:
            return board_cycles[0]

        for board_cycle in board_cycles:
            if board_cycle.start_time and board_cycle.end_time:
                if board_cycle.start_time <= ref <= board_cycle.end_time:
                    return board_cycle

        best_cycle: MechBoardCycle | None = None
        best_overlap = timedelta.min
        if cpu_cycle.start_time and cpu_cycle.end_time:
            for board_cycle in board_cycles:
                if not board_cycle.start_time or not board_cycle.end_time:
                    continue
                overlap_start = max(cpu_cycle.start_time, board_cycle.start_time)
                overlap_end = min(cpu_cycle.end_time, board_cycle.end_time)
                overlap = overlap_end - overlap_start
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_cycle = board_cycle
            if best_cycle is not None and best_overlap >= timedelta(0):
                return best_cycle

        return max(
            (cycle for cycle in board_cycles if cycle.start_time and cycle.start_time <= ref),
            key=lambda cycle: cycle.start_time,
            default=board_cycles[0],
        )

    def _detect_splits_for_group(
        self, group: list[MechLogEntry],
    ) -> list[datetime]:
        """检测单 (slot, cpu_key) 组内的板卡重启切分点。

        算法：
          1. 在 indicator 进程中检测 PID 变化，定位重启事件
          2. 对每个重启事件，用白名单进程计算安全切分点
          3. 尝试通过 journal 序号跳变前移切分点
        """
        if not group:
            return []

        # Step 1: 找到 indicator 的所有 PID 变化点
        indicator_splits = self._find_indicator_pid_changes(group)
        if not indicator_splits:
            logger.info("indicator=%r 无 PID 变化", self._indicator)
            return []

        logger.info(
            "indicator=%r 检测到 %d 次 PID 变化",
            self._indicator, len(indicator_splits),
        )

        # Step 2 + 3: 对每个 PID 变化点，计算精确切分时间
        split_timestamps: list[datetime] = []
        for idx, (old_pid, new_pid, change_idx) in enumerate(indicator_splits):
            # 确定搜索范围：从上一个切分点到当前切分点之后
            search_start = 0
            search_end = len(group)
            if idx > 0:
                prev_change_idx = indicator_splits[idx - 1][2]
                # 搜索起点从上一次变化点开始（包含旧生命周期尾部）
                search_start = prev_change_idx
            if idx + 1 < len(indicator_splits):
                search_end = indicator_splits[idx + 1][2]

            split_ts = self._compute_split_timestamp(
                group, change_idx, search_start, search_end, old_pid,
            )
            if split_ts:
                split_timestamps.append(split_ts)
                self._split_traces.append(MechCycleSplitTrace(
                    timestamp=split_ts,
                    reason="indicator_pid_changed",
                    cpu_id=group[change_idx].cpu_id,
                    indicator=self._indicator or "",
                    old_pid=old_pid,
                    new_pid=new_pid,
                    detail=f"indicator pid changed from {old_pid} to {new_pid}",
                ))
                logger.info(
                    "PID 变化 #%d: indicator old_pid=%s → 切分时间=%s",
                    idx + 1, old_pid, split_ts.isoformat(),
                )

        return split_timestamps

    def _find_indicator_pid_changes(
        self, group: list[MechLogEntry],
    ) -> list[tuple[str, str, int]]:
        """找到 indicator 进程的所有 PID 变化点。

        Returns:
            列表，每项为 (旧PID, 新PID, 变化发生的条目索引)。
        """
        indicator_lower = self._indicator.lower()
        changes: list[tuple[str, str, int]] = []
        prev_pid: str | None = None
        pid_states: list[str] = []

        for i, e in enumerate(group):
            if e.process_name.lower() != indicator_lower:
                continue
            if not e.pid:
                continue
            if prev_pid and e.pid != prev_pid:
                changes.append((prev_pid, e.pid, i))
                pid_states.append(e.pid)
                if len(pid_states) >= 3:
                    a, b, c = pid_states[-3:]
                    if a == c and a != b:
                        evidence = self._pid_bounce_evidence(group, indicator_lower, i)
                        self._record_split_diagnostic(
                            "suspect_pid_bounce",
                            slot=e.slot,
                            scope=f"cpu:{e.cpu_id}" if e.cpu_id else "board",
                            split=e.timestamp,
                            reason="indicator_pid_bounce",
                            evidence=evidence,
                            suggested_commands=self._suggested_commands(
                                evidence=evidence,
                                slot=e.slot,
                            ),
                            proc=indicator_lower,
                            pids=f"{a}>{b}>{c}",
                        )
                logger.info(
                    "indicator PID 变化: %s → %s at index=%d, ts=%s",
                    prev_pid, e.pid, i,
                    e.timestamp.isoformat() if e.timestamp else None,
                )
            elif prev_pid is None:
                pid_states.append(e.pid)
            prev_pid = e.pid

        return changes

    # ── 安全切分点计算 ──────────────────────────────────────

    def _compute_split_timestamp(
        self,
        group: list[MechLogEntry],
        change_idx: int,
        search_start: int,
        search_end: int,
        indicator_old_pid: str,
    ) -> datetime | None:
        """计算单次重启的精确切分时间戳。

        Args:
            group: 按 (slot, cpu_key) 分组并排序后的全部条目
            change_idx: indicator PID 变化发生的条目索引
            search_start: 搜索范围起始索引（上一个切分点）
            search_end: 搜索范围结束索引（下一次 indicator PID 变化点）
            indicator_old_pid: indicator 的旧 PID
        """
        # 确定 indicator 新 PID 的第一条时间戳，作为 fallback
        indicator_new_ts = group[change_idx].timestamp

        # ── Step 2: 白名单进程安全切分点 ──

        # 找到白名单进程的旧 PID 和新 PID 边界
        old_pid_ends: list[datetime] = []
        new_pid_starts: list[datetime] = []

        # 始终包含 indicator 进程
        whitelist_set = set(self._whitelist)
        indicator_name = self._indicator.lower()
        whitelist_set.add(indicator_name)

        # 记录每个白名单进程的 PID 变化索引，供后续序号跳变检测使用
        proc_change_indices: dict[str, int] = {}
        protected_boundaries: list[tuple[str, str, str, tuple[str, ...], str, datetime, datetime]] = []

        ordered_protected_names = [indicator_name] + sorted(whitelist_set - {indicator_name})
        for proc_name_lower in ordered_protected_names:
            old_last_ts, new_first_ts, change_at, old_pid, new_pid = self._find_pid_boundary(
                group, proc_name_lower, search_start, change_idx, search_end,
            )
            has_old_side = old_last_ts is not None
            if change_at is not None and has_old_side:
                proc_change_indices[proc_name_lower] = change_at
            if old_last_ts and new_first_ts and old_pid and new_pid and has_old_side:
                role = "indicator" if proc_name_lower == indicator_name else "whitelist"
                cpu_key = group[change_at].cpu_id if change_at is not None else ""
                protected_boundaries.append((
                    proc_name_lower,
                    new_pid,
                    cpu_key or "",
                    (old_pid,),
                    role,
                    old_last_ts,
                    new_first_ts,
                ))
            if old_last_ts and has_old_side:
                old_pid_ends.append(old_last_ts)
                logger.debug(
                    "白名单进程 %r: 旧 PID 最后一条 ts=%s",
                    proc_name_lower, old_last_ts.isoformat(),
                )
            if new_first_ts and has_old_side:
                new_pid_starts.append(new_first_ts)
                logger.debug(
                    "白名单进程 %r: 新 PID 第一条 ts=%s",
                    proc_name_lower, new_first_ts.isoformat(),
                )

        if not old_pid_ends and not new_pid_starts:
            # 退化为 indicator 自身的时间戳
            return indicator_new_ts

        # old_pid_end = 白名单内旧 PID 最后一条的最大值
        # 保证切分点之后不会有任何旧 PID 条目（同 PID 不被拆断）
        old_pid_end = max(old_pid_ends) if old_pid_ends else None

        # new_pid_start = 白名单内新 PID 第一条的最小值
        new_pid_start = min(new_pid_starts) if new_pid_starts else None

        logger.info(
            "安全区间: old_pid_end=%s, new_pid_start=%s",
            old_pid_end.isoformat() if old_pid_end else None,
            new_pid_start.isoformat() if new_pid_start else None,
        )

        # 初始切分点：优先取 old_pid_end（保证旧 PID 完整性）
        if old_pid_end and new_pid_start and new_pid_start <= old_pid_end:
            scope = f"cpu:{group[change_idx].cpu_id}" if group[change_idx].cpu_id else "board"
            protected_payload = self._protected_boundary_summaries(group, protected_boundaries)
            evidence_payload = self._issue_evidence([], protected_payload)
            self._record_split_diagnostic(
                "restart_boundary_overlap",
                slot=group[change_idx].slot,
                scope=scope,
                split=old_pid_end + timedelta(microseconds=1),
                reason="new_pid_start_le_old_pid_end",
                old_pid_end_time=old_pid_end,
                new_pid_start_time=new_pid_start,
                protected_boundaries=protected_payload,
                evidence=evidence_payload,
                suggested_commands=self._suggested_commands(
                    protected_boundaries=protected_payload,
                    slot=group[change_idx].slot,
                ),
                old_pid_end=old_pid_end.isoformat(),
                new_pid_start=new_pid_start.isoformat(),
            )

        if old_pid_end:
            initial_split = old_pid_end
        elif new_pid_start:
            initial_split = new_pid_start
        else:
            initial_split = indicator_new_ts

        # ── Step 3: Journal 序号前移 ──

        journal_earliest = self._find_journal_earliest(
            group, whitelist_set, search_start, proc_change_indices,
        )

        if journal_earliest:
            logger.info(
                "Journal 序号前移候选: %s (初始切分点: %s)",
                journal_earliest.isoformat(), initial_split.isoformat(),
            )

        # 最终切分点：从安全候选中取最早值
        # 板卡重启时所有进程先停再起，old_pid_end < new_pid_start 一定成立
        # 约束：切分点必须 > old_pid_end（旧 PID 不被拆断）
        # 使用 >= 比较，条目 >= 切分点 → 新周期
        lower_bound = old_pid_end

        candidates: list[datetime] = []
        if new_pid_start and (not lower_bound or new_pid_start > lower_bound):
            candidates.append(new_pid_start)
        if journal_earliest and (not lower_bound or journal_earliest > lower_bound):
            candidates.append(journal_earliest)

        if candidates:
            final_split = min(candidates)
        elif lower_bound:
            final_split = lower_bound + timedelta(microseconds=1)
        else:
            final_split = initial_split

        return final_split

    def _find_pid_boundary(
        self,
        group: list[MechLogEntry],
        proc_name_lower: str,
        search_start: int,
        anchor_idx: int,
        search_end: int | None = None,
    ) -> tuple[datetime | None, datetime | None, int | None, str | None, str | None]:
        """找到指定进程的旧 PID 最后一条、新 PID 第一条及 PID 变化索引。

        通过检测 PID 变化来区分旧生命周期和新生命周期。
        仅考虑有 PID 的条目（journal 无 PID 条目不参与 PID 边界判定）。
        搜索范围限制在相邻 indicator PID 变化之间，避免把未来启动的
        白名单 PID 变化当成本次重启边界。
        对一次重启，旧 PID 以 indicator PID 变化前或同时间戳最后观察到的
        PID 为准，新 PID 取之后第一个不同 PID，避免拿同一旧生命周期内更早的
        白名单 PID 自变化当成本次重启边界。

        Returns:
            (旧 PID 最后一条时间戳, 新 PID 第一条时间戳, PID 变化索引)
        """
        if search_end is None:
            search_end = len(group)
        old_pid: str | None = None
        old_last_ts: datetime | None = None
        new_first_ts: datetime | None = None
        new_pid: str | None = None
        change_at: int | None = None

        anchor_ts = group[anchor_idx].timestamp if anchor_idx < len(group) else None

        proc_entries = [
            (i, group[i])
            for i in range(search_start, search_end)
            if group[i].process_name.lower() == proc_name_lower and group[i].pid
        ]

        # Whitelist processes may start earlier than the indicator in the same
        # board lifecycle. If the PID observed immediately before the indicator
        # change continues after that change, treat that PID as the new side and
        # use its previous PID run as the old side.
        if proc_name_lower != (self._indicator or "").lower():
            before_anchor_pos: int | None = None
            for pos, (entry_idx, _entry) in enumerate(proc_entries):
                if entry_idx < anchor_idx:
                    before_anchor_pos = pos
                    continue
                break

            if before_anchor_pos is not None:
                current_pid = proc_entries[before_anchor_pos][1].pid
                run_start = before_anchor_pos
                while run_start > 0 and proc_entries[run_start - 1][1].pid == current_pid:
                    run_start -= 1
                run_end = before_anchor_pos
                while (
                    run_end + 1 < len(proc_entries)
                    and proc_entries[run_end + 1][1].pid == current_pid
                ):
                    run_end += 1

                spans_anchor = any(
                    entry_idx >= anchor_idx
                    for entry_idx, _entry in proc_entries[run_start:run_end + 1]
                )
                if spans_anchor and run_start > 0:
                    previous_pid = proc_entries[run_start - 1][1].pid
                    previous_start = run_start - 1
                    while (
                        previous_start > 0
                        and proc_entries[previous_start - 1][1].pid == previous_pid
                    ):
                        previous_start -= 1

                    previous_run = proc_entries[previous_start:run_start]
                    current_run = proc_entries[run_start:run_end + 1]
                    new_first_ts = next(
                        (entry.timestamp for _idx, entry in current_run if entry.timestamp),
                        None,
                    )
                    old_times = [
                        entry.timestamp
                        for _idx, entry in previous_run
                        if entry.timestamp is not None
                        and new_first_ts is not None
                        and entry.timestamp <= new_first_ts
                    ]
                    old_last_ts = max(old_times) if old_times else None
                    if old_last_ts and new_first_ts:
                        logger.debug(
                            "进程 %r PID=%s 横跨 indicator 变化，使用前一 PID=%s 作为旧侧",
                            proc_name_lower, current_pid, previous_pid,
                        )
                        return (
                            old_last_ts,
                            new_first_ts,
                            proc_entries[run_start][0],
                            previous_pid,
                            current_pid,
                        )

        for i in range(search_start, min(anchor_idx, search_end)):
            e = group[i]
            if e.process_name.lower() != proc_name_lower:
                continue
            if not e.pid:
                continue
            old_pid = e.pid
            if e.timestamp:
                old_last_ts = e.timestamp

        if not old_pid and anchor_ts is not None:
            for i in range(max(anchor_idx, search_start), search_end):
                e = group[i]
                if e.timestamp and e.timestamp > anchor_ts:
                    break
                if e.timestamp != anchor_ts:
                    continue
                if e.process_name.lower() != proc_name_lower:
                    continue
                if not e.pid:
                    continue
                old_pid = e.pid
                old_last_ts = e.timestamp
                break

        if not old_pid:
            logger.debug(
                "进程 %r 在 indicator 变化前或同时间戳未观察到旧 PID", proc_name_lower,
            )
            return None, None, None, None, None

        for i in range(max(anchor_idx, search_start), search_end):
            e = group[i]
            if e.process_name.lower() != proc_name_lower:
                continue
            if not e.pid:
                continue
            if e.pid != old_pid:
                new_pid = e.pid
                change_at = i
                if e.timestamp:
                    new_first_ts = e.timestamp
                break

        if change_at is None:
            logger.debug(
                "进程 %r 在搜索范围内未检测到 PID 变化", proc_name_lower,
            )
            return None, None, None, None, None

        # 同时间戳日志排序不可靠：new PID 可能排在 old PID 尾部之前。
        # 因此 old_end 按时间在相邻窗口内取 old PID 的最后一条，而不是按下标回扫。
        old_times = [
            e.timestamp
            for e in group[search_start:search_end]
            if e.process_name.lower() == proc_name_lower
            and e.pid == old_pid
            and e.timestamp is not None
            and new_first_ts is not None
            and e.timestamp <= new_first_ts
        ]
        old_last_ts = max(old_times) if old_times else old_last_ts
        if not old_last_ts:
            logger.warning(
                "进程 %r 旧 PID=%s 无时间戳条目，切分点可能偏移",
                proc_name_lower, old_pid,
            )

        return old_last_ts, new_first_ts, change_at, old_pid, new_pid
    # ── Journal 序号前移 ────────────────────────────────────

    def _find_journal_earliest(
        self,
        group: list[MechLogEntry],
        whitelist_set: set[str],
        search_start: int,
        proc_change_indices: dict[str, int],
    ) -> datetime | None:
        """在白名单进程的全部条目中找序号跳变，尝试前移切分点。

        使用每个进程自身的 PID 变化索引，而非 indicator 的 change_idx + 50。
        """
        earliest: datetime | None = None

        for proc_name_lower in whitelist_set:
            change_at = proc_change_indices.get(proc_name_lower)
            if change_at is None:
                continue
            candidate = self._find_seq_jump_for_process(
                group, proc_name_lower, search_start, change_at,
            )
            if candidate:
                logger.debug(
                    "进程 %r journal 序号前移候选: %s",
                    proc_name_lower, candidate.isoformat(),
                )
                if earliest is None or candidate < earliest:
                    earliest = candidate

        return earliest

    def _find_seq_jump_for_process(
        self,
        group: list[MechLogEntry],
        proc_name_lower: str,
        search_start: int,
        change_at: int,
    ) -> datetime | None:
        """找单个进程的序号跳变点。

        使用该进程自身的 PID 变化索引 change_at 作为扫描上界。

        1. 从诊断日志获取旧 PID 阶段的最后一个 No
        2. 在全部条目中找序号从旧 No 附近跳到小号的第一条
        3. 跳变后第一条的时间戳即为候选前移点
        """
        # 获取旧 PID 阶段的最后一个序号
        old_max_seq = self._get_old_pid_max_seq(
            group, proc_name_lower, search_start, change_at,
        )
        if old_max_seq <= 0:
            return None

        # 收集该进程在 search_start..change_at（不含）范围内有序号的条目
        # 不含 change_at：该位置是新 PID 第一条，其小序号会与旧 PID 末尾产生伪跳变
        proc_entries: list[tuple[int, MechLogEntry]] = []
        for i in range(search_start, change_at):
            e = group[i]
            if e.process_name.lower() != proc_name_lower:
                continue
            if e.sequence > 0:
                proc_entries.append((i, e))

        if len(proc_entries) < 2:
            return None

        # 找序号跳变：从高跳到低（差值 > SEQ_JUMP_THRESHOLD）
        for k in range(len(proc_entries) - 1):
            _, prev_e = proc_entries[k]
            idx, curr_e = proc_entries[k + 1]
            if prev_e.sequence - curr_e.sequence > SEQ_JUMP_THRESHOLD:
                logger.debug(
                    "进程 %r 序号跳变: No[%d] → No[%d] at index=%d",
                    proc_name_lower, prev_e.sequence, curr_e.sequence, idx,
                )
                return curr_e.timestamp

        return None

    @staticmethod
    def _get_old_pid_max_seq(
        group: list[MechLogEntry],
        proc_name_lower: str,
        search_start: int,
        change_at: int,
    ) -> int:
        """获取指定进程在旧 PID 阶段（PID 变化点之前）的最大序号。"""
        max_seq = 0
        for i in range(search_start, change_at):
            e = group[i]
            if e.process_name.lower() != proc_name_lower:
                continue
            if e.sequence > max_seq:
                max_seq = e.sequence

        return max_seq

    # ── 分段与构建 ──────────────────────────────────────────

    def _refine_split_timestamps(
        self,
        entries: list[MechLogEntry],
        raw_splits: list[datetime],
    ) -> list[datetime]:
        """Move or drop raw splits that would split a same-process PID segment."""
        refined: list[datetime] = []
        trace_updates: dict[datetime, datetime | None] = {}

        for i, raw_split in enumerate(raw_splits):
            window_start = raw_splits[i - 1] if i > 0 else None
            window_end = raw_splits[i + 1] if i + 1 < len(raw_splits) else None
            current_split = raw_split
            protected_blockers = self._protected_new_pid_blockers(
                entries,
                raw_split,
                window_start,
                window_end,
            )
            hard_upper_bound = (
                min(blocker[-1] for blocker in protected_blockers)
                if protected_blockers
                else None
            )
            seen_conflicts: list[tuple[str, str, str, str, datetime, datetime, datetime]] = []
            dropped = False
            recorded_conflict = False

            while True:
                conflicts = self._find_split_conflicts(
                    entries,
                    current_split,
                    window_start,
                    window_end,
                )
                if not conflicts:
                    break

                seen_conflicts.extend(conflicts)
                current_split = max(
                    last_ts + timedelta(microseconds=1)
                    for *_prefix, last_ts in conflicts
                )
                if hard_upper_bound is not None and current_split > hard_upper_bound:
                    backward_adjustment = self._find_backward_split_adjustment(
                        entries,
                        raw_split,
                        window_start,
                        window_end,
                        seen_conflicts,
                        protected_blockers,
                    )
                    if backward_adjustment:
                        current_split, gap_start, gap_end = backward_adjustment
                        self._record_unsafe_split(
                            "adjusted_backward",
                            raw_split,
                            current_split,
                            window_start,
                            window_end,
                            seen_conflicts,
                            protected_blockers,
                            protected_gap=(gap_start, gap_end),
                            entries=entries,
                        )
                    else:
                        self._record_unsafe_split(
                            "kept",
                            raw_split,
                            current_split,
                            window_start,
                            window_end,
                            seen_conflicts,
                            protected_blockers,
                            reason="no_safe_gap_candidate",
                            entries=entries,
                        )
                        current_split = raw_split
                    recorded_conflict = True
                    break
                if window_end is not None and current_split >= window_end:
                    self._record_unsafe_split(
                        "dropped",
                        raw_split,
                        current_split,
                        window_start,
                        window_end,
                        seen_conflicts,
                        [],
                        entries=entries,
                    )
                    trace_updates[raw_split] = None
                    dropped = True
                    break

            if dropped:
                continue

            if seen_conflicts and not recorded_conflict:
                self._record_unsafe_split(
                    "adjusted",
                    raw_split,
                    current_split,
                    window_start,
                    window_end,
                    seen_conflicts,
                    [],
                    entries=entries,
                )
            refined.append(current_split)
            trace_updates[raw_split] = current_split

        self._apply_split_trace_updates(trace_updates)
        return sorted(set(refined))

    def _find_backward_split_adjustment(
        self,
        entries: list[MechLogEntry],
        raw_split: datetime,
        window_start: datetime | None,
        window_end: datetime | None,
        conflicts: list[tuple[str, str, str, str, datetime, datetime, datetime]],
        protected_boundaries: list[tuple[str, str, str, tuple[str, ...], str, datetime, datetime]],
    ) -> tuple[datetime, datetime, datetime] | None:
        if not protected_boundaries:
            return None

        gap_start = max(boundary[-2] for boundary in protected_boundaries)
        gap_end = min(boundary[-1] for boundary in protected_boundaries)
        if gap_start >= gap_end:
            return None

        candidate = self._earliest_conflict_timestamp_in_gap(
            entries,
            conflicts,
            gap_start,
            raw_split,
            window_start,
            window_end,
        )
        if candidate is None or candidate <= gap_start or candidate >= raw_split:
            return None

        while True:
            next_conflicts = self._find_split_conflicts(
                entries,
                candidate,
                window_start,
                window_end,
            )
            if not next_conflicts:
                return candidate, gap_start, gap_end

            next_candidate = self._earliest_conflict_timestamp_in_gap(
                entries,
                next_conflicts,
                gap_start,
                candidate,
                window_start,
                window_end,
            )
            if next_candidate is None or next_candidate <= gap_start or next_candidate >= candidate:
                return None
            candidate = next_candidate

    @staticmethod
    def _earliest_conflict_timestamp_in_gap(
        entries: list[MechLogEntry],
        conflicts: list[tuple[str, str, str, str, datetime, datetime, datetime]],
        gap_start: datetime,
        upper_exclusive: datetime,
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> datetime | None:
        conflict_keys = {
            (slot, proc, pid, cpu)
            for slot, proc, pid, cpu, *_times in conflicts
        }
        candidates: list[datetime] = []
        for e in entries:
            if not e.timestamp or not e.pid:
                continue
            if window_start is not None and e.timestamp < window_start:
                continue
            if window_end is not None and e.timestamp >= window_end:
                continue
            key = (e.slot, e.process_name.lower(), e.pid, e.cpu_id or "")
            if key not in conflict_keys:
                continue
            if gap_start < e.timestamp < upper_exclusive:
                candidates.append(e.timestamp)

        return min(candidates) if candidates else None

    def _enforce_protected_pid_boundaries(
        self,
        entries: list[MechLogEntry],
        split_timestamps: list[datetime],
    ) -> list[datetime]:
        refined = sorted(set(split_timestamps))
        while True:
            pruned = self._prune_redundant_splits(entries, refined)
            if pruned != refined:
                refined = pruned
                continue

            forced: list[datetime] = []
            for segment in self._segment_by_timestamps(entries, refined):
                forced.extend(self._protected_pid_change_timestamps(segment))

            new_splits = sorted({ts for ts in forced if ts not in refined})
            if not new_splits:
                return refined

            next_refined = self._refine_split_timestamps(entries, sorted(set(refined + new_splits)))
            if next_refined == refined:
                return refined
            refined = next_refined

    def _prune_redundant_splits(
        self,
        entries: list[MechLogEntry],
        split_timestamps: list[datetime],
    ) -> list[datetime]:
        protected_names = set(self._whitelist)
        if self._indicator:
            protected_names.add(self._indicator.lower())
        if not protected_names:
            return sorted(set(split_timestamps))

        refined = sorted(set(split_timestamps))
        removed: dict[datetime, datetime | None] = {}

        changed = True
        while changed:
            changed = False
            for idx, split in enumerate(list(refined)):
                window_start = refined[idx - 1] if idx > 0 else None
                window_end = refined[idx + 1] if idx + 1 < len(refined) else None
                if not self._can_remove_redundant_split(
                    entries,
                    split,
                    window_start,
                    window_end,
                    protected_names,
                ):
                    continue

                refined.remove(split)
                removed[split] = None
                evidence = self._context_evidence(entries, split)
                self._record_split_diagnostic(
                    "suspect_over_split",
                    slot=self._split_window_slot(entries, window_start, window_end),
                    scope=self._split_window_scope(entries, window_start, window_end),
                    split=split,
                    reason="pruned_redundant_split",
                    evidence=evidence,
                    suggested_commands=self._suggested_commands(
                        evidence=evidence,
                        slot=self._split_window_slot(entries, window_start, window_end),
                    ),
                )
                changed = True
                break

        self._apply_split_trace_updates(removed)
        return refined

    def _can_remove_redundant_split(
        self,
        entries: list[MechLogEntry],
        split: datetime,
        window_start: datetime | None,
        window_end: datetime | None,
        protected_names: set[str],
    ) -> bool:
        protected_pids: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        has_before = False
        has_after = False

        for e in entries:
            if not e.timestamp or not e.pid:
                continue
            if window_start is not None and e.timestamp < window_start:
                continue
            if window_end is not None and e.timestamp >= window_end:
                continue
            proc_name = e.process_name.lower()
            if proc_name not in protected_names:
                continue
            protected_pids[(e.slot, e.cpu_id or "", proc_name)].add(e.pid)
            if e.timestamp < split:
                has_before = True
            else:
                has_after = True

        if not has_before or not has_after:
            return False
        return all(len(pids) <= 1 for pids in protected_pids.values())

    @staticmethod
    def _split_window_slot(
        entries: list[MechLogEntry],
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> str:
        slots = sorted({
            e.slot or "-"
            for e in entries
            if e.timestamp
            and (window_start is None or e.timestamp >= window_start)
            and (window_end is None or e.timestamp < window_end)
        })
        return ",".join(slots) if slots else "-"

    @staticmethod
    def _split_window_scope(
        entries: list[MechLogEntry],
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> str:
        scopes = sorted({
            e.cpu_id or ""
            for e in entries
            if e.timestamp
            and (window_start is None or e.timestamp >= window_start)
            and (window_end is None or e.timestamp < window_end)
        })
        if len(scopes) == 1:
            return f"cpu:{scopes[0]}" if scopes[0] else "board"
        return "mixed" if scopes else "board"

    def _protected_pid_change_timestamps(self, entries: list[MechLogEntry]) -> list[datetime]:
        protected_names = set(self._whitelist)
        if self._indicator:
            protected_names.add(self._indicator.lower())
        if not protected_names:
            return []

        by_key: dict[tuple[str, str, str], list[MechLogEntry]] = defaultdict(list)
        for e in entries:
            if not e.timestamp or not e.pid:
                continue
            proc_name = e.process_name.lower()
            if proc_name not in protected_names:
                continue
            by_key[(e.slot, e.cpu_id or "", proc_name)].append(e)

        splits: list[datetime] = []
        for (_slot, _cpu, proc_name), logs in by_key.items():
            logs.sort(key=self._timestamp_sort_key)
            prev_pid: str | None = None
            prev_log: MechLogEntry | None = None
            for e in logs:
                if prev_pid and e.pid != prev_pid:
                    splits.append(e.timestamp)
                    message = (
                        "forced protected pid split: "
                        f"module={self._module_key or '-'} "
                        f"slot={e.slot or '-'} "
                        f"process={proc_name} "
                        f"cpu={e.cpu_id or 'board'} "
                        f"old_pid={prev_pid} new_pid={e.pid} "
                        f"split={e.timestamp.isoformat()}"
                    )
                    self.errors.append(message)
                    logger.error(message)
                    role = "indicator" if proc_name == (self._indicator or "").lower() else "whitelist"
                    protected_payload = self._protected_boundary_summaries(
                        entries,
                        [(
                            proc_name,
                            e.pid,
                            e.cpu_id or "",
                            (prev_pid,),
                            role,
                            prev_log.timestamp if prev_log and prev_log.timestamp else e.timestamp,
                            e.timestamp,
                        )],
                    )
                    evidence_payload = self._issue_evidence([], protected_payload)
                    self._record_split_diagnostic(
                        "protected_forced_split",
                        slot=e.slot,
                        scope=f"cpu:{e.cpu_id}" if e.cpu_id else "board",
                        split=e.timestamp,
                        reason="protected_pid_change",
                        protected_boundaries=protected_payload,
                        evidence=evidence_payload,
                        suggested_commands=self._suggested_commands(
                            protected_boundaries=protected_payload,
                            slot=e.slot,
                        ),
                        proc=proc_name,
                        pids=f"{prev_pid}>{e.pid}",
                    )
                prev_pid = e.pid
                prev_log = e

        return splits

    def _protected_new_pid_blockers(
        self,
        entries: list[MechLogEntry],
        split: datetime,
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> list[tuple[str, str, str, tuple[str, ...], str, datetime, datetime]]:
        protected_names = set(self._whitelist)
        if self._indicator:
            protected_names.add(self._indicator.lower())
        if not protected_names:
            return []

        by_name: dict[tuple[str, str], list[MechLogEntry]] = defaultdict(list)
        for e in entries:
            if not e.timestamp or not e.pid:
                continue
            if window_start is not None and e.timestamp < window_start:
                continue
            if window_end is not None and e.timestamp >= window_end:
                continue
            proc_name = e.process_name.lower()
            if proc_name not in protected_names:
                continue
            by_name[(proc_name, e.cpu_id or "")].append(e)

        blockers: list[tuple[str, str, str, tuple[str, ...], str, datetime, datetime]] = []
        for (proc_name, cpu_key), logs in by_name.items():
            ordered_logs = sorted(logs, key=self._timestamp_sort_key)
            before = [e for e in ordered_logs if e.timestamp and e.timestamp < split]
            after = [e for e in ordered_logs if e.timestamp and e.timestamp >= split]
            if not before or not after:
                continue
            previous_pid = before[-1].pid
            previous_ts = before[-1].timestamp
            first_after = after[0]
            if first_after.pid == previous_pid:
                continue
            role = "indicator" if proc_name == (self._indicator or "").lower() else "whitelist"
            blockers.append((
                proc_name,
                first_after.pid,
                cpu_key,
                (previous_pid,),
                role,
                previous_ts,
                first_after.timestamp,
            ))

        return blockers

    @staticmethod
    def _find_split_conflicts(
        entries: list[MechLogEntry],
        split: datetime,
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> list[tuple[str, str, str, str, datetime, datetime, datetime]]:
        by_key: dict[tuple[str, str, str, str], list[MechLogEntry]] = defaultdict(list)
        for e in entries:
            if not e.timestamp or not e.pid:
                continue
            if window_start is not None and e.timestamp < window_start:
                continue
            if window_end is not None and e.timestamp >= window_end:
                continue
            by_key[(e.slot, e.process_name.lower(), e.pid, e.cpu_id or "")].append(e)

        conflicts: list[tuple[str, str, str, str, datetime, datetime, datetime]] = []
        for (slot, proc_name, pid, cpu_key), logs in by_key.items():
            before = [e.timestamp for e in logs if e.timestamp and e.timestamp < split]
            after = [e.timestamp for e in logs if e.timestamp and e.timestamp >= split]
            if before and after:
                all_times = before + after
                conflicts.append((
                    slot,
                    proc_name,
                    pid,
                    cpu_key,
                    max(before),
                    min(after),
                    max(all_times),
                ))
        return conflicts

    @staticmethod
    def _conflict_scope(
        conflicts: list[tuple[str, str, str, str, datetime, datetime, datetime]],
    ) -> str:
        cpu_keys = sorted({cpu for _slot, _proc, _pid, cpu, *_rest in conflicts})
        if len(cpu_keys) == 1:
            return f"cpu:{cpu_keys[0]}" if cpu_keys[0] else "board"
        return "mixed" if cpu_keys else "board"

    @staticmethod
    def _raw_excerpt(raw: str, limit: int = 240) -> str:
        compact = " ".join((raw or "").replace("\r", " ").replace("\n", " ").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3] + "..."

    def _entry_summary(self, entry: MechLogEntry | None, role: str) -> dict:
        if entry is None:
            return {}
        return {
            "role": role,
            "timestamp": self._fmt_optional_ts(entry.timestamp),
            "source": entry.source,
            "source_file": entry.source_file,
            "slot": entry.slot,
            "cpu_id": entry.cpu_id or "",
            "process_name": entry.process_name,
            "pid": entry.pid,
            "sequence": entry.sequence,
            "raw_excerpt": self._raw_excerpt(entry.raw or entry.context),
        }

    def _context_evidence(self, entries: list[MechLogEntry], split: datetime | None) -> list[dict]:
        if split is None:
            return []
        ordered = sorted(
            [entry for entry in entries if entry.timestamp],
            key=self._timestamp_sort_key,
        )
        before = [entry for entry in ordered if entry.timestamp and entry.timestamp < split]
        after = [entry for entry in ordered if entry.timestamp and entry.timestamp >= split]
        evidence: list[dict] = []
        if before:
            evidence.append(self._entry_summary(before[-1], "context_before"))
        if after:
            evidence.append(self._entry_summary(after[0], "context_after"))
        return evidence

    def _pid_bounce_evidence(
        self,
        entries: list[MechLogEntry],
        process_name: str,
        current_idx: int,
    ) -> list[dict]:
        relevant = [
            entry
            for entry in entries[:current_idx + 1]
            if entry.process_name.lower() == process_name and entry.pid
        ][-3:]
        return [
            self._entry_summary(entry, f"pid_bounce_{idx}")
            for idx, entry in enumerate(relevant, start=1)
        ]

    @staticmethod
    def _find_evidence_entry(
        entries: list[MechLogEntry],
        *,
        slot: str | None = None,
        process_name: str,
        pid: str,
        cpu_id: str,
        timestamp: datetime,
    ) -> MechLogEntry | None:
        proc_lower = process_name.lower()
        for entry in entries:
            if entry.timestamp != timestamp:
                continue
            if slot is not None and entry.slot != slot:
                continue
            if entry.process_name.lower() != proc_lower:
                continue
            if entry.pid != pid:
                continue
            if (entry.cpu_id or "") != cpu_id:
                continue
            return entry
        return None

    def _conflict_summaries(
        self,
        entries: list[MechLogEntry],
        conflicts: list[tuple[str, str, str, str, datetime, datetime, datetime]],
    ) -> list[dict]:
        summaries: list[dict] = []
        for slot, proc, pid, cpu, before, after, last in conflicts:
            before_log = self._find_evidence_entry(
                entries,
                slot=slot,
                process_name=proc,
                pid=pid,
                cpu_id=cpu,
                timestamp=before,
            )
            after_log = self._find_evidence_entry(
                entries,
                slot=slot,
                process_name=proc,
                pid=pid,
                cpu_id=cpu,
                timestamp=after,
            )
            last_log = self._find_evidence_entry(
                entries,
                slot=slot,
                process_name=proc,
                pid=pid,
                cpu_id=cpu,
                timestamp=last,
            )
            summaries.append({
                "slot": slot,
                "process_name": proc,
                "pid": pid,
                "cpu_id": cpu,
                "before_time": before.isoformat(),
                "after_time": after.isoformat(),
                "last_time": last.isoformat(),
                "before_log": self._entry_summary(before_log, "conflict_before"),
                "after_log": self._entry_summary(after_log, "conflict_after"),
                "last_log": self._entry_summary(last_log, "conflict_last"),
            })
        return summaries

    def _protected_boundary_summaries(
        self,
        entries: list[MechLogEntry],
        protected_boundaries: list[tuple[str, str, str, tuple[str, ...], str, datetime, datetime]],
    ) -> list[dict]:
        summaries: list[dict] = []
        for proc, new_pid, cpu, old_pids, role, old_end, new_start in protected_boundaries:
            old_log: MechLogEntry | None = None
            for old_pid in old_pids:
                old_log = self._find_evidence_entry(
                    entries,
                    process_name=proc,
                    pid=old_pid,
                    cpu_id=cpu,
                    timestamp=old_end,
                )
                if old_log is not None:
                    break
            new_log = self._find_evidence_entry(
                entries,
                process_name=proc,
                pid=new_pid,
                cpu_id=cpu,
                timestamp=new_start,
            )
            summaries.append({
                "slot": (old_log or new_log).slot if (old_log or new_log) else "",
                "process_name": proc,
                "cpu_id": cpu,
                "role": role,
                "old_pids": list(old_pids),
                "old_end": old_end.isoformat(),
                "new_pid": new_pid,
                "new_start": new_start.isoformat(),
                "old_log": self._entry_summary(old_log, "protected_old"),
                "new_log": self._entry_summary(new_log, "protected_new"),
            })
        return summaries

    @staticmethod
    def _issue_evidence(conflicts: list[dict], protected_boundaries: list[dict]) -> list[dict]:
        evidence: list[dict] = []
        seen: set[tuple[str, str, str, str, str, int]] = set()

        def add(item: dict) -> None:
            if not item:
                return
            key = (
                item.get("timestamp", ""),
                item.get("process_name", ""),
                item.get("pid", ""),
                item.get("cpu_id", ""),
                item.get("source_file", ""),
                item.get("sequence", 0),
            )
            if key in seen:
                return
            seen.add(key)
            evidence.append(item)

        for conflict in conflicts:
            add(conflict.get("before_log", {}))
            add(conflict.get("after_log", {}))
            add(conflict.get("last_log", {}))
        for boundary in protected_boundaries:
            add(boundary.get("old_log", {}))
            add(boundary.get("new_log", {}))
        return evidence

    def _suggested_commands(
        self,
        conflicts: list[tuple[str, str, str, str, datetime, datetime, datetime]] | None = None,
        protected_boundaries: list[dict] | None = None,
        evidence: list[dict] | None = None,
        slot: str | None = None,
    ) -> list[str]:
        conflicts = conflicts or []
        protected_boundaries = protected_boundaries or []
        evidence = evidence or []
        if not conflicts and not protected_boundaries and not evidence and not slot:
            return []
        module_name = self._module_name or self._module_key or "<module>"
        slots = sorted({
            item
            for item in (
                [slot] if slot else []
            )
            + [conflict_slot or "-" for conflict_slot, *_rest in conflicts]
            + [boundary.get("slot") or "-" for boundary in protected_boundaries]
            + [item.get("slot") or "-" for item in evidence]
            if item
        })
        if not slots:
            slots = ["-"]
        commands = [
            f"python cli.py mech-lifecycles <task_id> -s {slots[0]} -m {module_name} --show-boundaries"
        ]
        seen_proc: set[tuple[str, str, str, str]] = set()
        for slot, proc, pid, cpu, *_rest in conflicts:
            key = (slot, proc, pid, cpu)
            if key in seen_proc:
                continue
            seen_proc.add(key)
            proc_arg = f"{proc}-{pid}" if pid else proc
            cmd = (
                f"python cli.py mech-logs <task_id> -s {slot or '-'} "
                f"-c <board_cycle> -p {proc_arg} -m {module_name}"
            )
            if cpu:
                cmd += f" --cpu {cpu} --cpu-cycle <cpu_cycle>"
            commands.append(cmd)
        for boundary in protected_boundaries:
            proc = boundary.get("process_name") or ""
            cpu = boundary.get("cpu_id") or ""
            boundary_slot = boundary.get("slot") or slots[0]
            endpoint_pids = [
                pid for pid in list(boundary.get("old_pids") or []) + [boundary.get("new_pid") or ""]
                if pid
            ]
            for pid in endpoint_pids:
                key = (boundary_slot, proc, pid, cpu)
                if not proc or key in seen_proc:
                    continue
                seen_proc.add(key)
                proc_arg = f"{proc}-{pid}" if pid else proc
                cmd = (
                    f"python cli.py mech-logs <task_id> -s {boundary_slot} "
                    f"-c <board_cycle> -p {proc_arg} -m {module_name}"
                )
                if cpu:
                    cmd += f" --cpu {cpu} --cpu-cycle <cpu_cycle>"
                commands.append(cmd)
        for item in evidence:
            proc = item.get("process_name") or ""
            pid = item.get("pid") or ""
            cpu = item.get("cpu_id") or ""
            evidence_slot = item.get("slot") or slots[0]
            key = (evidence_slot, proc, pid, cpu)
            if not proc or key in seen_proc:
                continue
            seen_proc.add(key)
            proc_arg = f"{proc}-{pid}" if pid else proc
            cmd = (
                f"python cli.py mech-logs <task_id> -s {evidence_slot} "
                f"-c <board_cycle> -p {proc_arg} -m {module_name}"
            )
            if cpu:
                cmd += f" --cpu {cpu} --cpu-cycle <cpu_cycle>"
            commands.append(cmd)
        return commands

    def _record_unsafe_split(
        self,
        action: str,
        raw_split: datetime,
        adjusted_split: datetime,
        window_start: datetime | None,
        window_end: datetime | None,
        conflicts: list[tuple[str, str, str, str, datetime, datetime, datetime]],
        protected_blockers: list[tuple[str, str, str, tuple[str, ...], str, datetime, datetime]] | None = None,
        reason: str | None = None,
        protected_gap: tuple[datetime, datetime] | None = None,
        entries: list[MechLogEntry] | None = None,
    ) -> None:
        slots = sorted({slot or "-" for slot, *_rest in conflicts})
        conflict_payload = self._conflict_summaries(entries or [], conflicts)
        protected_payload = self._protected_boundary_summaries(entries or [], protected_blockers or [])
        evidence_payload = self._issue_evidence(conflict_payload, protected_payload)
        suggested_commands = self._suggested_commands(conflicts)
        severity = "error" if action == "kept" else "warning"
        conflict_text = "; ".join(
            f"{proc}-{pid}@{cpu or 'board'} before={before.isoformat()} "
            f"after={after.isoformat()} last={last.isoformat()}"
            for _slot, proc, pid, cpu, before, after, last in conflicts
        )
        blocker_text = "; ".join(
            f"{proc}@{cpu or 'board'} role={role} old_pids={','.join(old_pids)} "
            f"old_end={old_end.isoformat()} new_pid={new_pid} new_start={new_start.isoformat()}"
            for proc, new_pid, cpu, old_pids, role, old_end, new_start in (protected_blockers or [])
        )
        message = (
            f"unsafe cycle split {action}: "
            f"module={self._module_key or '-'} "
            f"slot={','.join(slots) if slots else '-'} "
            f"split={raw_split.isoformat()} "
            f"adjusted={adjusted_split.isoformat()} "
            f"window=[{self._fmt_optional_ts(window_start)}, {self._fmt_optional_ts(window_end)}) "
            f"same_pid_conflicts={conflict_text}"
        )
        if protected_gap:
            message += (
                f" protected_gap=({protected_gap[0].isoformat()}, "
                f"{protected_gap[1].isoformat()}]"
            )
        if blocker_text:
            message += f" protected_boundaries={blocker_text}"
        if reason:
            message += f" reason={reason}"
        self.errors.append(message)
        if action == "kept":
            self.lifecycle_reliable = False
            self._append_boundary_issue(
                "unsafe_cycle_split",
                slot=",".join(slots) if slots else "-",
                scope=self._conflict_scope(conflicts),
                split=raw_split,
                adjusted=adjusted_split,
                window_start=window_start,
                window_end=window_end,
                action=action,
                reason=reason or action,
                module_key=self._module_key,
                detail=message,
                conflicts=conflict_payload,
                protected_boundaries=protected_payload,
                evidence=evidence_payload,
                suggested_commands=suggested_commands,
                severity=severity,
            )
        logger.error(message)
        diagnostic_kind = {
            "adjusted": "same_pid_adjusted",
            "adjusted_backward": "same_pid_adjusted_backward",
            "kept": "same_pid_kept",
            "dropped": "same_pid_dropped",
        }.get(action)
        if diagnostic_kind:
            cpu_keys = sorted({cpu for _slot, _proc, _pid, cpu, *_rest in conflicts})
            if len(cpu_keys) == 1:
                scope = f"cpu:{cpu_keys[0]}" if cpu_keys[0] else "board"
            else:
                scope = "mixed"
            self._record_split_diagnostic(
                diagnostic_kind,
                slot=",".join(slots) if slots else "-",
                scope=scope,
                split=raw_split,
                adjusted=adjusted_split,
                reason=reason or action,
                action=action,
                severity=severity,
                conflicts=conflict_payload,
                protected_boundaries=protected_payload,
                evidence=evidence_payload,
                suggested_commands=suggested_commands,
            )

    def _apply_split_trace_updates(
        self,
        trace_updates: dict[datetime, datetime | None],
    ) -> None:
        if not trace_updates:
            return

        updated_traces: list[MechCycleSplitTrace] = []
        for trace in self._split_traces:
            if trace.timestamp in trace_updates:
                new_timestamp = trace_updates[trace.timestamp]
                if new_timestamp is None:
                    continue
                trace.timestamp = new_timestamp
            updated_traces.append(trace)
        self._split_traces = updated_traces

    def _record_over_split_diagnostics(self, cycles: list[MechBoardCycle]) -> None:
        protected_names = set(self._whitelist)
        if self._indicator:
            protected_names.add(self._indicator.lower())
        if not protected_names or len(cycles) < 2:
            return

        for left, right in zip(cycles, cycles[1:]):
            protected_by_key: dict[tuple[str, str], set[str]] = defaultdict(set)
            slots: set[str] = set()
            scopes: set[str] = set()
            left_has_protected = False
            right_has_protected = False
            evidence: list[dict] = []

            for cycle, side in ((left, "left"), (right, "right")):
                for proc in cycle.processes:
                    proc_name = proc.process_name.lower()
                    if proc_name not in protected_names:
                        continue
                    for log in proc.logs:
                        if not log.pid:
                            continue
                        protected_by_key[(proc_name, log.cpu_id or "")].add(log.pid)
                        slots.add(log.slot or "-")
                        scopes.add(log.cpu_id or "")
                        if side == "left":
                            left_has_protected = True
                        else:
                            right_has_protected = True
                    if proc.logs:
                        role = "over_split_left" if side == "left" else "over_split_right"
                        candidate = proc.logs[-1] if side == "left" else proc.logs[0]
                        evidence.append(self._entry_summary(candidate, role))

            if not left_has_protected or not right_has_protected:
                continue
            if any(len(pids) > 1 for pids in protected_by_key.values()):
                continue

            if len(scopes) == 1:
                only_scope = next(iter(scopes))
                scope = f"cpu:{only_scope}" if only_scope else "board"
            else:
                scope = "mixed"
            self._record_split_diagnostic(
                "suspect_over_split",
                slot=",".join(sorted(slots)) if slots else "-",
                scope=scope,
                split=right.start_time,
                reason="protected_merge_has_no_pid_conflict",
                evidence=evidence[:2],
                suggested_commands=self._suggested_commands(
                    evidence=evidence[:2],
                    slot=",".join(sorted(slots)) if slots else "-",
                ),
            )

    def _assign_split_traces(self, cycles: list[MechBoardCycle]) -> None:
        if not cycles:
            return

        ordered_cycles = sorted(cycles, key=lambda c: (
            0 if c.start_time else 1,
            c.start_time.timestamp() if c.start_time else 0,
            c.dir_name,
        ))
        for trace in self._split_traces:
            if trace.cpu_id:
                target_cpu = self._find_trace_cpu_cycle(ordered_cycles, trace)
                if target_cpu is not None:
                    target_cpu.split_traces.append(trace)
                    continue

            target = ordered_cycles[-1]
            for cycle in ordered_cycles:
                if cycle.start_time and trace.timestamp < cycle.start_time:
                    target = cycle
                    break
                if cycle.start_time and trace.timestamp >= cycle.start_time:
                    target = cycle
            target.split_traces.append(trace)

    @staticmethod
    def _find_trace_cpu_cycle(
        cycles: list[MechBoardCycle],
        trace: MechCycleSplitTrace,
    ) -> MechCpuCycle | None:
        candidates = [
            cpu_cycle
            for cycle in cycles
            for cpu_cycle in cycle.cpu_cycles
            if cpu_cycle.cpu_id == trace.cpu_id
        ]
        if not candidates:
            return None
        target = candidates[-1]
        for cpu_cycle in sorted(candidates, key=lambda c: (
            0 if c.start_time else 1,
            c.start_time.timestamp() if c.start_time else 0,
            c.dir_name,
        )):
            if cpu_cycle.start_time and trace.timestamp < cpu_cycle.start_time:
                target = cpu_cycle
                break
            if cpu_cycle.start_time and trace.timestamp >= cpu_cycle.start_time:
                target = cpu_cycle
        return target

    def _append_boundary_issue(
        self,
        kind: str,
        *,
        severity: str = "warning",
        action: str = "",
        reason: str = "",
        module_key: str = "",
        slot: str | None = None,
        scope: str | None = None,
        split: datetime | None = None,
        adjusted: datetime | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        old_pid_end: datetime | None = None,
        new_pid_start: datetime | None = None,
        process_name: str = "",
        pid: str = "",
        direction: str = "",
        log_count: int = 0,
        detail: str = "",
        conflicts: list[dict] | None = None,
        protected_boundaries: list[dict] | None = None,
        evidence: list[dict] | None = None,
        suggested_commands: list[str] | None = None,
    ) -> None:
        issue_slot = slot or ""
        issue_scope = scope or "board"
        event_id = (
            f"{module_key or self._module_key or '-'}:"
            f"{issue_slot or '-'}:{issue_scope}:{kind}:"
            f"{self._fmt_optional_ts(split)}"
        )
        self.boundary_issues.append(MechBoundaryIssue(
            kind=kind,
            severity=severity,
            action=action,
            reason=reason,
            module_key=module_key or self._module_key,
            event_id=event_id,
            slot=slot or "",
            scope=scope or "board",
            split_time=split,
            adjusted_time=adjusted,
            window_start=window_start,
            window_end=window_end,
            old_pid_end=old_pid_end,
            new_pid_start=new_pid_start,
            process_name=process_name,
            pid=pid,
            direction=direction,
            log_count=log_count,
            detail=detail,
            conflicts=conflicts or [],
            protected_boundaries=protected_boundaries or [],
            evidence=evidence or [],
            suggested_commands=suggested_commands or [],
        ))

    @staticmethod
    def _diagnostic_severity(kind: str) -> str:
        if kind in {"restart_boundary_overlap", "same_pid_kept"}:
            return "error"
        if kind in {
            "same_pid_adjusted",
            "same_pid_adjusted_backward",
            "same_pid_dropped",
            "protected_forced_split",
            "suspect_pid_bounce",
        }:
            return "warning"
        return "info"

    def _record_split_diagnostic(
        self,
        kind: str,
        *,
        slot: str | None = None,
        scope: str | None = None,
        split: datetime | None = None,
        adjusted: datetime | None = None,
        reason: str | None = None,
        old_pid_end_time: datetime | None = None,
        new_pid_start_time: datetime | None = None,
        action: str = "",
        severity: str | None = None,
        conflicts: list[dict] | None = None,
        protected_boundaries: list[dict] | None = None,
        evidence: list[dict] | None = None,
        suggested_commands: list[str] | None = None,
        **fields: str,
    ) -> None:
        parts = [
            f"cycle split diagnostic: {kind}",
            f"m={self._module_key or '-'}",
            f"s={slot or '-'}",
            f"scope={scope or 'board'}",
            f"sp={self._fmt_optional_ts(split)}",
        ]
        if adjusted is not None:
            parts.append(f"ad={self._fmt_optional_ts(adjusted)}")
        parts.append(f"reason={reason or '-'}")
        for key, value in fields.items():
            parts.append(f"{key}={value}")

        message = " ".join(parts)
        if message in self._diagnostics_seen:
            return
        self._diagnostics_seen.add(message)
        self.errors.append(message)
        if kind in {"restart_boundary_overlap", "same_pid_kept"}:
            self.lifecycle_reliable = False
        self._append_boundary_issue(
            kind,
            severity=severity or self._diagnostic_severity(kind),
            action=action,
            reason=reason or "",
            module_key=self._module_key,
            slot=slot,
            scope=scope,
            split=split,
            adjusted=adjusted,
            old_pid_end=old_pid_end_time,
            new_pid_start=new_pid_start_time,
            detail=" ".join(f"{key}={value}" for key, value in fields.items()),
            conflicts=conflicts,
            protected_boundaries=protected_boundaries,
            evidence=evidence,
            suggested_commands=suggested_commands,
        )
        logger.error(message)

    @staticmethod
    def _fmt_optional_ts(ts: datetime | None) -> str:
        return ts.isoformat() if ts else "-"

    @staticmethod
    def _segment_by_timestamps(
        entries: list[MechLogEntry], split_timestamps: list[datetime],
    ) -> list[list[MechLogEntry]]:
        return [
            segment
            for _lower_bound, _upper_bound, segment
            in CycleDetector._segment_by_timestamps_with_bounds(entries, split_timestamps)
        ]

    @staticmethod
    def _segment_by_timestamps_with_bounds(
        entries: list[MechLogEntry], split_timestamps: list[datetime],
    ) -> list[tuple[datetime | None, datetime | None, list[MechLogEntry]]]:
        """按统一切分时间线对所有条目分段。

        使用 ``>=`` 比较：条目 >= 切分点 → 新周期。
        切分点由 _compute_split_timestamp 计算，已包含 +1us 偏移（来自 old_pid_end 时）
        以保证旧 PID 最后一条不被划到新周期。
        """
        sorted_entries = sorted(entries, key=lambda e: (
            0 if e.timestamp else 1,
            e.timestamp.timestamp() if e.timestamp else 0,
            e.sequence,
        ))

        segments: list[tuple[datetime | None, datetime | None, list[MechLogEntry]]] = []
        current: list[MechLogEntry] = []
        split_idx = 0
        lower_bound: datetime | None = None

        # 先收集无时间戳条目，延迟到有时间戳条目触发切分时一起分配
        pending_no_ts: list[MechLogEntry] = []

        for e in sorted_entries:
            if not e.timestamp:
                pending_no_ts.append(e)
                continue
            while (split_idx < len(split_timestamps)
                   and e.timestamp >= split_timestamps[split_idx]):
                # 切分时，无时间戳条目归入前一段（当前段结束）
                current.extend(pending_no_ts)
                pending_no_ts = []
                split_ts = split_timestamps[split_idx]
                segments.append((lower_bound, split_ts, current))
                current = []
                lower_bound = split_ts
                split_idx += 1
            current.append(e)

        if current or pending_no_ts:
            current.extend(pending_no_ts)
            upper_bound = split_timestamps[split_idx] if split_idx < len(split_timestamps) else None
            segments.append((lower_bound, upper_bound, current))

        return segments

    @staticmethod
    def _sequence_mode(entries: list[MechLogEntry]) -> str:
        if not entries:
            return "timestamp"
        sequenced = sum(1 for e in entries if e.sequence > 0)
        if sequenced == len(entries):
            return "sequence"
        if sequenced == 0:
            return "timestamp"
        logger.warning(
            "module1 cycle has mixed sequence availability: %d/%d entries have sequence; "
            "falling back to timestamp ordering",
            sequenced,
            len(entries),
        )
        return "timestamp"

    @staticmethod
    def _make_cycles(entries: list[MechLogEntry]) -> list[MechBoardCycle]:
        if not entries:
            return []
        sequence_mode = CycleDetector._sequence_mode(entries)
        procs = CycleDetector._build_processes(entries, sequence_mode)
        times = [e.timestamp for e in entries if e.timestamp]
        start = min(times) if times else None
        end = max(times) if times else None
        dir_name = CycleDetector._fmt_dir(start, end)
        return [MechBoardCycle(
            dir_name=dir_name, start_time=start, end_time=end,
            processes=procs,
        )]

    @staticmethod
    def _timestamp_sort_key(e: MechLogEntry) -> tuple[int, float, str, str]:
        return (
            0 if e.timestamp else 1,
            e.timestamp.timestamp() if e.timestamp else 0,
            e.source_file,
            e.raw,
        )

    @staticmethod
    def _sequence_sort_key(e: MechLogEntry) -> tuple[int, int, int, float, str, str]:
        return (
            0 if e.sequence > 0 else 1,
            e.sequence if e.sequence > 0 else 0,
            0 if e.timestamp else 1,
            e.timestamp.timestamp() if e.timestamp else 0,
            e.source_file,
            e.raw,
        )

    @staticmethod
    def _build_processes(
        entries: list[MechLogEntry],
        sequence_mode: str,
    ) -> list[MechProcessLifecycle]:
        # 分组键含 cpu_id，防止不同 CPU 同名同 PID 进程日志合并
        by_key: dict[tuple[str, str, str], list[MechLogEntry]] = defaultdict(list)
        for e in entries:
            by_key[(e.process_name, e.pid, e.cpu_id or "")].append(e)

        # 将无 PID 的 journal 条目合并到同进程名+同 cpu_id 有 PID 的分组
        # 同一生命周期内同一进程只有一个 PID，journal 无 PID 条目属于该 PID
        no_pid_keys = [k for k in by_key if k[1] == ""]
        for proc_name, _empty_pid, cpu_key in no_pid_keys:
            no_pid_logs = by_key.pop((proc_name, "", cpu_key))
            # 找同进程名+同 cpu_id 有 PID 的分组
            pid_key = None
            for k in by_key:
                if k[0] == proc_name and k[1] and k[2] == cpu_key:
                    pid_key = k
                    break
            if pid_key:
                by_key[pid_key].extend(no_pid_logs)
            else:
                # 没找到有 PID 的分组，保留原样
                by_key[(proc_name, "", cpu_key)] = no_pid_logs

        lifecycles: list[MechProcessLifecycle] = []
        for (proc_name, pid, _cpu_key), logs in sorted(by_key.items()):
            if sequence_mode == "sequence":
                logs.sort(key=CycleDetector._sequence_sort_key)
                seqs = [l.sequence for l in logs if l.sequence > 0]
                missing: list[int] = []
                if len(seqs) >= 2:
                    full = set(range(min(seqs), max(seqs) + 1))
                    missing = sorted(full - set(seqs))
            else:
                logs.sort(key=CycleDetector._timestamp_sort_key)
                missing = []
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
