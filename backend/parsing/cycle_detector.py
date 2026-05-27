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

from backend.models import MechBoardCycle, MechCycleSplitTrace, MechLogEntry, MechProcessLifecycle

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
    ):
        self._indicator = indicator
        self._whitelist = [w.lower() for w in (whitelist or [])]
        self._module_key = module_key or ""
        self._split_traces: list[MechCycleSplitTrace] = []
        self.errors: list[str] = []

    def detect(self, entries: list[MechLogEntry]) -> list[MechBoardCycle]:
        """检测重启周期，返回按时间排列的周期列表。"""
        self._split_traces = []
        self.errors = []
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
            return self._make_cycles(all_entries) if all_entries else []

        # 收集所有 cpu_key 的切分时间点
        all_split_timestamps: list[datetime] = []

        # 板卡级（cpu_key=""）的切分时间点
        board_splits: list[datetime] = []
        if "" in by_cpu:
            board_splits = self._detect_splits_for_group(by_cpu[""])

        for cpu_key in sorted(by_cpu.keys()):
            if cpu_key == "":
                all_split_timestamps.extend(board_splits)
                continue
            # 子卡：板卡级切分 + 自身 PID 变化切分
            all_split_timestamps.extend(board_splits)
            cpu_splits = self._detect_splits_for_group(by_cpu[cpu_key])
            all_split_timestamps.extend(cpu_splits)
            if cpu_splits:
                logger.info(
                    "cpu_key=%r 切分时间点(%d): %s",
                    cpu_key, len(cpu_splits),
                    [ts.isoformat() for ts in cpu_splits],
                )

        # 去重排序，形成统一切分时间线
        cycles: list[MechBoardCycle] = []
        all_entries = [e for group in by_cpu.values() for e in group]
        unique_splits = sorted(set(ts for ts in all_split_timestamps if ts is not None))
        logger.info(
            "统一切分时间点(%d): %s",
            len(unique_splits),
            [ts.isoformat() for ts in unique_splits],
        )

        if unique_splits:
            unique_splits = self._refine_split_timestamps(all_entries, unique_splits)
            unique_splits = self._enforce_protected_pid_boundaries(all_entries, unique_splits)

        if not unique_splits:
            if all_entries:
                cycles = self._make_cycles(all_entries)
        else:
            segments = self._segment_by_timestamps(all_entries, unique_splits)
            for seg in segments:
                if seg:
                    cycles.extend(self._make_cycles(seg))

        # 将 split traces 分配到对应周期
        self._assign_split_traces(cycles)

        logger.info("最终切分结果: %d 个周期", len(cycles))
        for i, c in enumerate(cycles):
            logger.info("  周期[%d]: %s, %d 个进程组", i, c.dir_name, len(c.processes))

        return cycles

    # ── 单组切分检测（核心算法）───────────────────────────────

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
            if idx > 0:
                prev_change_idx = indicator_splits[idx - 1][2]
                # 搜索起点从上一次变化点开始（包含旧生命周期尾部）
                search_start = prev_change_idx

            split_ts = self._compute_split_timestamp(
                group, change_idx, search_start, old_pid,
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

        for i, e in enumerate(group):
            if e.process_name.lower() != indicator_lower:
                continue
            if not e.pid:
                continue
            if prev_pid and e.pid != prev_pid:
                changes.append((prev_pid, e.pid, i))
                logger.info(
                    "indicator PID 变化: %s → %s at index=%d, ts=%s",
                    prev_pid, e.pid, i,
                    e.timestamp.isoformat() if e.timestamp else None,
                )
            prev_pid = e.pid

        return changes

    # ── 安全切分点计算 ──────────────────────────────────────

    def _compute_split_timestamp(
        self,
        group: list[MechLogEntry],
        change_idx: int,
        search_start: int,
        indicator_old_pid: str,
    ) -> datetime | None:
        """计算单次重启的精确切分时间戳。

        Args:
            group: 按 (slot, cpu_key) 分组并排序后的全部条目
            change_idx: indicator PID 变化发生的条目索引
            search_start: 搜索范围起始索引（上一个切分点）
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

        for proc_name_lower in whitelist_set:
            old_last_ts, new_first_ts, change_at = self._find_pid_boundary(
                group, proc_name_lower, search_start,
            )
            if change_at is not None:
                proc_change_indices[proc_name_lower] = change_at
            if old_last_ts:
                old_pid_ends.append(old_last_ts)
                logger.debug(
                    "白名单进程 %r: 旧 PID 最后一条 ts=%s",
                    proc_name_lower, old_last_ts.isoformat(),
                )
            if new_first_ts:
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
    ) -> tuple[datetime | None, datetime | None, int | None]:
        """找到指定进程的旧 PID 最后一条、新 PID 第一条及 PID 变化索引。

        通过检测 PID 变化来区分旧生命周期和新生命周期。
        仅考虑有 PID 的条目（journal 无 PID 条目不参与 PID 边界判定）。

        Returns:
            (旧 PID 最后一条时间戳, 新 PID 第一条时间戳, PID 变化索引)
        """
        prev_pid: str | None = None
        old_pid: str | None = None
        new_first_ts: datetime | None = None
        change_at: int | None = None
        found_change = False

        for i in range(search_start, len(group)):
            e = group[i]
            if e.process_name.lower() != proc_name_lower:
                continue
            if not e.pid:
                continue

            if prev_pid and e.pid != prev_pid and not found_change:
                found_change = True
                old_pid = prev_pid
                change_at = i
                if e.timestamp:
                    new_first_ts = e.timestamp
            prev_pid = e.pid

        if not found_change:
            logger.debug(
                "进程 %r 在搜索范围内未检测到 PID 变化", proc_name_lower,
            )
            return None, None, None

        # 从 PID 变化点向前扫描，只取旧 PID 条目的最后一条
        old_last_ts: datetime | None = None
        if old_pid:
            for i in range(change_at - 1, search_start - 1, -1):
                e = group[i]
                if e.process_name.lower() != proc_name_lower:
                    continue
                if e.pid != old_pid:
                    continue
                if e.timestamp:
                    old_last_ts = e.timestamp
                    break
            if not old_last_ts:
                logger.warning(
                    "进程 %r 旧 PID=%s 无时间戳条目，切分点可能偏移",
                    proc_name_lower, old_pid,
                )

        return old_last_ts, new_first_ts, change_at

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
            forced: list[datetime] = []
            for segment in self._segment_by_timestamps(entries, refined):
                forced.extend(self._protected_pid_change_timestamps(segment))

            new_splits = sorted({ts for ts in forced if ts not in refined})
            if not new_splits:
                return refined

            refined = self._refine_split_timestamps(entries, sorted(set(refined + new_splits)))

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
                prev_pid = e.pid

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
    ) -> None:
        slots = sorted({slot or "-" for slot, *_rest in conflicts})
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
        logger.error(message)

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

    def _assign_split_traces(self, cycles: list[MechBoardCycle]) -> None:
        if not cycles:
            return

        ordered_cycles = sorted(cycles, key=lambda c: (
            0 if c.start_time else 1,
            c.start_time.timestamp() if c.start_time else 0,
            c.dir_name,
        ))
        for trace in self._split_traces:
            target = ordered_cycles[-1]
            for cycle in ordered_cycles:
                if cycle.start_time and trace.timestamp < cycle.start_time:
                    target = cycle
                    break
                if cycle.start_time and trace.timestamp >= cycle.start_time:
                    target = cycle
            target.split_traces.append(trace)

    @staticmethod
    def _fmt_optional_ts(ts: datetime | None) -> str:
        return ts.isoformat() if ts else "-"

    @staticmethod
    def _segment_by_timestamps(
        entries: list[MechLogEntry], split_timestamps: list[datetime],
    ) -> list[list[MechLogEntry]]:
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

        segments: list[list[MechLogEntry]] = []
        current: list[MechLogEntry] = []
        split_idx = 0

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
                segments.append(current)
                current = []
                split_idx += 1
            current.append(e)

        if current or pending_no_ts:
            current.extend(pending_no_ts)
            segments.append(current)

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
