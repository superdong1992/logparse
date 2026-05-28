"""Tests for CycleDetector.

测试覆盖新算法的三个步骤：
  Step 1: indicator PID 变化检测
  Step 2: 白名单进程安全切分点
  Step 3: Journal 序号前移
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from backend.models import MechBoardCycle, MechLogEntry, MechCycleSplitTrace, MechProcessLifecycle
from backend.parsing.cycle_detector import CycleDetector


def _ts(month: int, day: int, hour: int, minute: int = 0, sec: int = 0) -> datetime:
    tz = timezone(timedelta(hours=8))
    return datetime(2026, month, day, hour, minute, sec, tzinfo=tz)


def _ts_us(month: int, day: int, hour: int, minute: int, sec: int, micro: int) -> datetime:
    return _ts(month, day, hour, minute, sec).replace(microsecond=micro)


def _entry(
    proc: str, pid: str, seq: int, ts: datetime,
    cpu_id: str = "", source: str = "diagnostic",
) -> MechLogEntry:
    return MechLogEntry(
        timestamp=ts, source=source, slot="1", cpu_id=cpu_id,
        process_name=proc, pid=pid, sequence=seq,
        raw=f"{proc}-{pid}-No[{seq}]",
    )


def _entry_without_seq(
    proc: str,
    pid: str,
    ts: datetime,
    cpu_id: str = "",
    source: str = "diagnostic",
) -> MechLogEntry:
    return MechLogEntry(
        timestamp=ts,
        source=source,
        slot="1",
        cpu_id=cpu_id,
        process_name=proc,
        pid=pid,
        sequence=0,
        raw=f"{proc}-{pid}-no-sequence",
    )


@pytest.fixture
def detector():
    return CycleDetector(indicator="dhcp")


@pytest.fixture
def detector_with_whitelist():
    return CycleDetector(indicator="dhcp", whitelist=["svc_a", "svc_b"])


class TestBasicDetection:
    """Step 1: indicator PID 变化检测。"""

    def test_single_cycle_no_pid_change(self, detector):
        """无 PID 变化 → 整体一个周期。"""
        entries = [_entry("svc", "100", i, _ts(1, 3, 0, i)) for i in range(1, 6)]
        cycles = detector.detect(entries)
        assert len(cycles) == 1
        assert len(cycles[0].processes) == 1

    def test_pid_change_splits(self, detector):
        """indicator PID 变化 → 切分为 2 个周期。"""
        entries = [
            _entry("dhcp", "100", i, _ts(1, 3, 0, i)) for i in range(1, 4)
        ] + [
            _entry("dhcp", "200", i, _ts(1, 3, 1, i)) for i in range(1, 4)
        ]
        cycles = detector.detect(entries)
        assert len(cycles) == 2

    def test_indicator_pid_bounce_emits_diagnostic(self):
        det = CycleDetector(indicator="dhcp", module_key="m1")
        entries = [
            _entry_without_seq("dhcp", "100", _ts(1, 3, 0, 0)),
            _entry_without_seq("dhcp", "200", _ts(1, 3, 0, 1)),
            _entry_without_seq("dhcp", "100", _ts(1, 3, 0, 2)),
        ]

        det.detect(entries)

        assert any("cycle split diagnostic: suspect_pid_bounce" in error for error in det.errors)
        assert any("m=m1" in error and "proc=dhcp" in error for error in det.errors)
        assert any("pids=100>200>100" in error for error in det.errors)

    def test_adjacent_cycles_without_protected_pid_conflict_emit_over_split_diagnostic(self):
        det = CycleDetector(indicator="dhcp", module_key="m1")
        left_log = _entry_without_seq("dhcp", "100", _ts(1, 3, 0, 0))
        right_log = _entry_without_seq("dhcp", "100", _ts(1, 3, 0, 1))
        cycles = [
            MechBoardCycle(
                dir_name="left",
                start_time=left_log.timestamp,
                end_time=left_log.timestamp,
                processes=[
                    MechProcessLifecycle(
                        process_name="dhcp",
                        pid="100",
                        logs=[left_log],
                        total_count=1,
                    ),
                ],
            ),
            MechBoardCycle(
                dir_name="right",
                start_time=right_log.timestamp,
                end_time=right_log.timestamp,
                processes=[
                    MechProcessLifecycle(
                        process_name="dhcp",
                        pid="100",
                        logs=[right_log],
                        total_count=1,
                    ),
                ],
            ),
        ]

        det._record_over_split_diagnostics(cycles)

        assert any("cycle split diagnostic: suspect_over_split" in error for error in det.errors)
        assert any("reason=protected_merge_has_no_pid_conflict" in error for error in det.errors)

    def test_no_indicator(self):
        """无 indicator → 不切分。"""
        detector = CycleDetector(indicator=None)
        entries = [_entry("svc", "100", i, _ts(1, 3, 0, i)) for i in range(1, 11)]
        cycles = detector.detect(entries)
        assert len(cycles) == 1

    def test_empty_entries(self, detector):
        assert detector.detect([]) == []

    def test_detect_produces_logging_output(self, detector, caplog):
        with caplog.at_level(logging.INFO, logger="backend.parsing.cycle_detector"):
            entries = [_entry("dhcp", "100", i, _ts(1, 3, 0, i)) for i in range(1, 4)]
            detector.detect(entries)
        assert "共 3 条日志" in caplog.text
        assert "最终切分结果" in caplog.text


class TestWhitelistSafeSplit:
    """Step 2: 白名单进程安全切分点。"""

    def test_whitelist_moves_split_earlier(self):
        """白名单进程旧 PID 结束更晚 → 切分点后移以保证完整性。"""
        det = CycleDetector(indicator="dhcp", whitelist=["svc_a"])
        entries = [
            # indicator: PID 100, 最后一条 06:05
            _entry("dhcp", "100", 1, _ts(1, 3, 0, 0)),
            _entry("dhcp", "100", 2, _ts(1, 3, 6, 5)),
            # indicator: PID 200, 第一条 06:10
            _entry("dhcp", "200", 1, _ts(1, 3, 6, 10)),
            _entry("dhcp", "200", 2, _ts(1, 3, 6, 15)),
            # 白名单 svc_a: PID 300, 最后一条 06:08
            _entry("svc_a", "300", 1, _ts(1, 3, 0, 0)),
            _entry("svc_a", "300", 2, _ts(1, 3, 6, 8)),
            # 白名单 svc_a: PID 400, 第一条 06:20
            _entry("svc_a", "400", 1, _ts(1, 3, 6, 20)),
        ]
        cycles = det.detect(entries)
        assert len(cycles) == 2
        # 切分点应该在 06:08（svc_a 旧 PID 最后一条）
        # 而不是 06:10（indicator 新 PID 第一条）
        # 验证 svc_a PID=300 全部在周期1
        old_svc_a = [p for p in cycles[0].processes if p.pid == "300"]
        assert len(old_svc_a) == 1
        assert old_svc_a[0].total_count == 2
        # 验证 svc_a PID=400 全部在周期2
        new_svc_a = [p for p in cycles[1].processes if p.pid == "400"]
        assert len(new_svc_a) == 1

    def test_no_sequence_overlap_keeps_indicator_pid_generation_boundary(self):
        det = CycleDetector(indicator="dhcp", whitelist=["svc_a"])
        entries = [
            _entry_without_seq("dhcp", "100", _ts(1, 3, 0, 0)),
            _entry_without_seq("dhcp", "200", _ts(1, 3, 6, 0)),
            _entry_without_seq("svc_a", "300", _ts(1, 3, 0, 0)),
            _entry_without_seq("svc_a", "300", _ts(1, 3, 6, 8)),
            _entry_without_seq("svc_a", "400", _ts(1, 3, 6, 20)),
        ]

        cycles = det.detect(entries)

        assert len(cycles) == 3
        for cycle in cycles:
            pids_by_name: dict[str, set[str]] = {}
            for proc in cycle.processes:
                if proc.process_name in {"dhcp", "svc_a"}:
                    pids_by_name.setdefault(proc.process_name, set()).add(proc.pid)
            assert len(pids_by_name.get("dhcp", set())) <= 1
            assert len(pids_by_name.get("svc_a", set())) <= 1
        assert any("forced protected pid split" in error for error in det.errors)
        assert any("cycle split diagnostic: protected_forced_split" in error for error in det.errors)

    def test_non_whitelist_process_is_split_when_protected_pid_boundary_would_be_merged(self):
        """Keep the protected PID boundary even if a non-whitelist PID is split."""
        det = CycleDetector(indicator="dhcp", whitelist=["svc_a"])
        entries = [
            _entry("dhcp", "100", 1, _ts(1, 3, 0, 0)),
            _entry("dhcp", "200", 1, _ts(1, 3, 6, 0)),
            _entry("svc_a", "300", 1, _ts(1, 3, 0, 0)),
            _entry("svc_a", "300", 2, _ts(1, 3, 5, 59)),
            _entry("svc_a", "400", 1, _ts(1, 3, 6, 1)),
            _entry("other", "500", 1, _ts(1, 3, 5, 0)),
            _entry("other", "500", 2, _ts(1, 3, 7, 0)),
            _entry("late", "900", 1, _ts(1, 3, 7, 1)),
        ]

        cycles = det.detect(entries)

        assert len(cycles) == 2
        old_other = [p for p in cycles[0].processes if p.process_name == "other" and p.pid == "500"]
        assert len(old_other) == 1
        assert [log.timestamp for log in old_other[0].logs] == [_ts(1, 3, 5, 0)]
        new_other = [p for p in cycles[1].processes if p.process_name == "other" and p.pid == "500"]
        assert len(new_other) == 1
        assert [log.timestamp for log in new_other[0].logs] == [_ts(1, 3, 7, 0)]
        assert any("unsafe cycle split kept" in error for error in det.errors)
        assert any("cycle split diagnostic: same_pid_kept" in error for error in det.errors)
        assert any("same_pid_conflicts=other-500@board" in error for error in det.errors)
        assert any("protected_boundaries=dhcp@board role=indicator" in error for error in det.errors)
        assert any("svc_a@board role=whitelist" in error for error in det.errors)
        all_traces: list[MechCycleSplitTrace] = []
        for cycle in cycles:
            all_traces.extend(cycle.split_traces)
        assert len(all_traces) == 1
        assert all_traces[0].old_pid == "100"
        assert all_traces[0].new_pid == "200"

    def test_adjusted_split_does_not_merge_indicator_or_whitelist_pid_generations(self):
        det = CycleDetector(indicator="dhcp", whitelist=["svc_a"])
        entries = [
            _entry("dhcp", "100", 1, _ts(1, 3, 0, 0)),
            _entry("dhcp", "200", 1, _ts(1, 3, 6, 0)),
            _entry("svc_a", "300", 1, _ts(1, 3, 5, 59)),
            _entry("svc_a", "400", 1, _ts(1, 3, 6, 1)),
            _entry("other", "500", 1, _ts(1, 3, 5, 0)),
            _entry("other", "500", 2, _ts(1, 3, 7, 0)),
        ]

        cycles = det.detect(entries)

        assert len(cycles) == 2
        for cycle in cycles:
            pids_by_name: dict[str, set[str]] = {}
            for proc in cycle.processes:
                if proc.process_name in {"dhcp", "svc_a"}:
                    pids_by_name.setdefault(proc.process_name, set()).add(proc.pid)
            assert pids_by_name.get("dhcp", set()) != {"100", "200"}
            assert pids_by_name.get("svc_a", set()) != {"300", "400"}
        assert any("unsafe cycle split kept" in error for error in det.errors)

    def test_same_pid_in_protected_gap_moves_split_backward_without_sequence(self):
        det = CycleDetector(indicator="dhcp", whitelist=["aaa"])
        entries = [
            _entry_without_seq("dhcp", "10", _ts(1, 3, 12, 42, 28)),
            _entry_without_seq("dhcp", "20", _ts(1, 3, 13, 4, 18)),
            _entry_without_seq("aaa", "100", _ts(1, 3, 12, 42, 28)),
            _entry_without_seq("aaa", "100", _ts(1, 3, 12, 59, 3)),
            _entry_without_seq("aaa", "200", _ts(1, 3, 13, 4, 18)),
            _entry_without_seq("aaa", "200", _ts(1, 3, 13, 7, 16)),
            _entry_without_seq("other", "500", _ts(1, 3, 13, 4, 12)),
            _entry_without_seq("other", "500", _ts(1, 3, 13, 4, 18)),
            _entry_without_seq("other", "500", _ts(1, 3, 13, 4, 21)),
        ]

        cycles = det.detect(entries)

        assert len(cycles) == 2
        assert cycles[1].start_time == _ts(1, 3, 13, 4, 12)
        assert not [p for p in cycles[0].processes if p.process_name == "other"]
        new_other = [p for p in cycles[1].processes if p.process_name == "other" and p.pid == "500"]
        assert len(new_other) == 1
        assert [log.timestamp for log in new_other[0].logs] == [
            _ts(1, 3, 13, 4, 12),
            _ts(1, 3, 13, 4, 18),
            _ts(1, 3, 13, 4, 21),
        ]
        for cycle in cycles:
            aaa_pids = {p.pid for p in cycle.processes if p.process_name == "aaa"}
            assert aaa_pids != {"100", "200"}
        assert any("unsafe cycle split adjusted_backward" in error for error in det.errors)
        assert any("cycle split diagnostic: same_pid_adjusted_backward" in error for error in det.errors)
        assert any("same_pid_conflicts=other-500@board" in error for error in det.errors)
        assert any("protected_boundaries=dhcp@board role=indicator" in error for error in det.errors)
        assert any("aaa@board role=whitelist" in error for error in det.errors)
        assert any("protected_gap=(2026-01-03T12:59:03+08:00, 2026-01-03T13:04:18+08:00]" in error for error in det.errors)

    def test_same_pid_before_protected_gap_keeps_split_with_clear_reason(self):
        det = CycleDetector(indicator="dhcp", whitelist=["aaa"])
        entries = [
            _entry_without_seq("dhcp", "10", _ts(1, 3, 12, 42, 28)),
            _entry_without_seq("dhcp", "20", _ts(1, 3, 13, 4, 18)),
            _entry_without_seq("aaa", "100", _ts(1, 3, 12, 42, 28)),
            _entry_without_seq("aaa", "100", _ts(1, 3, 12, 59, 3)),
            _entry_without_seq("aaa", "200", _ts(1, 3, 13, 4, 18)),
            _entry_without_seq("other", "500", _ts(1, 3, 12, 58, 0)),
            _entry_without_seq("other", "500", _ts(1, 3, 13, 4, 18)),
        ]

        cycles = det.detect(entries)

        assert len(cycles) == 2
        assert [p for p in cycles[0].processes if p.process_name == "other" and p.pid == "500"]
        assert [p for p in cycles[1].processes if p.process_name == "other" and p.pid == "500"]
        assert any("unsafe cycle split kept" in error for error in det.errors)
        assert any("cycle split diagnostic: same_pid_kept" in error for error in det.errors)
        assert any("reason=no_safe_gap_candidate" in error for error in det.errors)

    def test_backward_adjustment_expands_for_cascading_same_pid_conflicts(self):
        det = CycleDetector(indicator="dhcp", whitelist=["aaa"])
        entries = [
            _entry_without_seq("dhcp", "10", _ts(1, 3, 12, 42, 28)),
            _entry_without_seq("dhcp", "20", _ts(1, 3, 13, 4, 18)),
            _entry_without_seq("aaa", "100", _ts(1, 3, 12, 42, 28)),
            _entry_without_seq("aaa", "100", _ts(1, 3, 12, 59, 3)),
            _entry_without_seq("aaa", "200", _ts(1, 3, 13, 4, 18)),
            _entry_without_seq("other", "500", _ts(1, 3, 13, 4, 12)),
            _entry_without_seq("other", "500", _ts(1, 3, 13, 4, 18)),
            _entry_without_seq("chain", "700", _ts(1, 3, 13, 4, 10)),
            _entry_without_seq("chain", "700", _ts(1, 3, 13, 4, 15)),
        ]

        cycles = det.detect(entries)

        assert len(cycles) == 2
        assert cycles[1].start_time == _ts(1, 3, 13, 4, 10)
        assert not [p for p in cycles[0].processes if p.process_name in {"other", "chain"}]
        assert any("unsafe cycle split adjusted_backward" in error for error in det.errors)

    def test_protected_pid_changes_are_forced_split_boundaries_without_sequence(self):
        det = CycleDetector(indicator="dhcp", whitelist=["svc_a"])
        entries = [
            _entry_without_seq("dhcp", "100", _ts(1, 3, 0, 0)),
            _entry_without_seq("dhcp", "200", _ts(1, 3, 6, 0)),
            _entry_without_seq("svc_a", "300", _ts(1, 3, 5, 59)),
            _entry_without_seq("svc_a", "400", _ts(1, 3, 6, 1)),
            _entry_without_seq("svc_a", "300", _ts(1, 3, 7, 0)),
        ]

        cycles = det.detect(entries)

        assert len(cycles) >= 3
        for cycle in cycles:
            pids_by_name: dict[str, set[str]] = {}
            for proc in cycle.processes:
                if proc.process_name in {"dhcp", "svc_a"}:
                    pids_by_name.setdefault(proc.process_name, set()).add(proc.pid)
            assert len(pids_by_name.get("dhcp", set())) <= 1
            assert len(pids_by_name.get("svc_a", set())) <= 1
        assert any("forced protected pid split" in error for error in det.errors)
        assert any("cycle split diagnostic: protected_forced_split" in error for error in det.errors)

    def test_redundant_split_between_same_protected_generation_is_pruned(self):
        det = CycleDetector(indicator="dhcp")
        entries = [
            _entry_without_seq("dhcp", "100", _ts(1, 3, 0, 0)),
            _entry_without_seq("dhcp", "200", _ts(1, 3, 6, 0)),
            _entry_without_seq("dhcp", "200", _ts(1, 3, 7, 0)),
            _entry_without_seq("dhcp", "300", _ts(1, 3, 12, 0)),
        ]

        refined = det._prune_redundant_splits(
            entries,
            [_ts(1, 3, 6, 0), _ts(1, 3, 7, 0), _ts(1, 3, 12, 0)],
        )

        assert refined == [_ts(1, 3, 6, 0), _ts(1, 3, 12, 0)]
        assert any("cycle split diagnostic: suspect_over_split" in error for error in det.errors)
        assert any("reason=pruned_redundant_split" in error for error in det.errors)

    def test_split_that_would_merge_protected_pid_generations_is_not_pruned(self):
        det = CycleDetector(indicator="dhcp")
        entries = [
            _entry_without_seq("dhcp", "100", _ts(1, 3, 0, 0)),
            _entry_without_seq("dhcp", "200", _ts(1, 3, 6, 0)),
            _entry_without_seq("dhcp", "200", _ts(1, 3, 7, 0)),
        ]

        refined = det._prune_redundant_splits(
            entries,
            [_ts(1, 3, 6, 0)],
        )

        assert refined == [_ts(1, 3, 6, 0)]

    def test_same_name_pid_on_different_cpus_does_not_block_split(self):
        det = CycleDetector(indicator="dhcp")
        entries = [
            _entry("dhcp", "100", 1, _ts(1, 3, 0, 0)),
            _entry("dhcp", "200", 1, _ts(1, 3, 6, 0)),
            _entry("other", "500", 1, _ts(1, 3, 5, 0), cpu_id="1"),
            _entry("other", "500", 1, _ts(1, 3, 7, 0), cpu_id="2"),
        ]

        cycles = det.detect(entries)

        assert len(cycles) == 2
        assert det.errors == []
        assert [p for p in cycles[0].processes if p.process_name == "other" and p.pid == "500"]
        assert [p for p in cycles[1].processes if p.process_name == "other" and p.pid == "500"]

    def test_conflicting_split_moves_backward_when_protected_gap_is_safe(self):
        det = CycleDetector(indicator="dhcp")
        entries = [
            _entry("dhcp", "100", 1, _ts(1, 3, 0, 0)),
            _entry("dhcp", "200", 1, _ts(1, 3, 6, 0)),
            _entry("dhcp", "300", 1, _ts(1, 3, 12, 0)),
            _entry("blocker", "700", 1, _ts(1, 3, 5, 59, 59)),
            _entry("blocker", "700", 2, _ts_us(1, 3, 11, 59, 59, 999999)),
        ]

        cycles = det.detect(entries)

        assert len(cycles) == 3
        assert any("unsafe cycle split adjusted_backward" in error for error in det.errors)
        assert any("cycle split diagnostic: same_pid_adjusted_backward" in error for error in det.errors)
        assert any("protected_boundaries=dhcp@board role=indicator" in error for error in det.errors)
        all_traces: list[MechCycleSplitTrace] = []
        for cycle in cycles:
            all_traces.extend(cycle.split_traces)
        assert [trace.old_pid for trace in all_traces] == ["100", "200"]

    def test_pid_reuse_across_multiple_restarts_is_not_globally_protected(self):
        det = CycleDetector(indicator="dhcp")
        entries = [
            _entry("dhcp", "100", 1, _ts(1, 3, 0, 0)),
            _entry("dhcp", "200", 1, _ts(1, 3, 6, 0)),
            _entry("dhcp", "300", 1, _ts(1, 3, 12, 0)),
            _entry("dhcp", "400", 1, _ts(1, 3, 18, 0)),
            _entry("blocker", "700", 1, _ts(1, 3, 5, 59, 59)),
            _entry("blocker", "700", 2, _ts_us(1, 3, 11, 59, 59, 999999)),
            _entry("reused", "500", 1, _ts(1, 3, 5, 0)),
            _entry("reused", "500", 1, _ts(1, 3, 13, 0)),
        ]

        cycles = det.detect(entries)

        assert len(cycles) == 4
        assert sum(
            1
            for cycle in cycles
            if any(p.process_name == "reused" and p.pid == "500" for p in cycle.processes)
        ) == 2
        assert len([error for error in det.errors if "unsafe cycle split adjusted_backward" in error]) == 1

    def test_errors_do_not_leak_between_detect_calls(self):
        det = CycleDetector(indicator="dhcp")
        conflict_entries = [
            _entry("dhcp", "100", 1, _ts(1, 3, 0, 0)),
            _entry("dhcp", "200", 1, _ts(1, 3, 6, 0)),
            _entry("other", "500", 1, _ts(1, 3, 5, 0)),
            _entry("other", "500", 2, _ts(1, 3, 7, 0)),
            _entry("late", "900", 1, _ts(1, 3, 8, 0)),
        ]
        det.detect(conflict_entries)
        assert det.errors

        det.detect([_entry("dhcp", "100", 1, _ts(1, 3, 0, 0))])

        assert det.errors == []


class TestJournalForwardAdjust:
    """Step 3: Journal 序号前移。"""

    def test_journal_seq_jump_moves_split(self):
        """journal 中序号跳变比诊断日志更早 → 前移切分点。

        场景：svc_a 在 journal 中从 No[99] 跳到 No[1]（无 PID），
        跳变时间 06:06 比诊断日志中旧 PID 最后一条 06:05 更晚但比
        新 PID 第一条 06:20 更早。journal 前移将切分点从 06:05+1us
        前移到 06:06（因为 06:06 > 06:05，安全约束满足）。
        """
        det = CycleDetector(indicator="dhcp", whitelist=["svc_a"])
        entries = [
            # indicator 诊断: PID 100→200
            _entry("dhcp", "100", 1, _ts(1, 3, 0, 0)),
            _entry("dhcp", "100", 50, _ts(1, 3, 6, 5)),
            _entry("dhcp", "200", 1, _ts(1, 3, 6, 10)),
            # svc_a 诊断: PID 300（旧）
            _entry("svc_a", "300", 1, _ts(1, 3, 0, 0)),
            _entry("svc_a", "300", 100, _ts(1, 3, 6, 3)),
            # svc_a 诊断: PID 400（新）at 06:20
            _entry("svc_a", "400", 1, _ts(1, 3, 6, 20)),
            # svc_a journal（无 PID）: 序号从 99→1 跳变
            _entry("svc_a", "", 99, _ts(1, 3, 6, 4), source="journal"),
            _entry("svc_a", "", 1, _ts(1, 3, 6, 6), source="journal"),
        ]
        cycles = det.detect(entries)
        assert len(cycles) == 2
        # 验证 svc_a PID=300 全部在周期1（不被拆断）
        # 无 PID journal seq=99 也被合并到此分组
        old_svc_a = [p for p in cycles[0].processes if p.pid == "300"]
        assert len(old_svc_a) == 1
        assert old_svc_a[0].total_count == 3  # diag*2 + journal*1
        # 验证 journal seq=1 在周期2，合并到 svc_a PID=400 分组
        new_svc_a = [p for p in cycles[1].processes if p.pid == "400"]
        assert len(new_svc_a) == 1
        assert new_svc_a[0].total_count == 2  # diag*1 + journal*1


class TestCpuSubcardIsolation:
    """CPU 子卡切分隔离。"""

    def test_cpu_local_restarts_do_not_fragment_board_process_without_pid_change(self):
        det = CycleDetector(indicator="dhcp", whitelist=["aaa"])
        board = [
            _entry_without_seq("aaa", "100", _ts(1, 3, 0, 0, 30)),
            _entry_without_seq("aaa", "100", _ts(1, 3, 0, 1, 30)),
            _entry_without_seq("aaa", "100", _ts(1, 3, 0, 2, 30)),
            _entry_without_seq("aaa", "100", _ts(1, 3, 0, 3, 30)),
            _entry_without_seq("aaa", "100", _ts(1, 3, 0, 4, 30)),
        ]
        cpu = [
            _entry_without_seq("dhcp", "10", _ts(1, 3, 0, 0, 0), cpu_id="1"),
            _entry_without_seq("dhcp", "20", _ts(1, 3, 0, 1, 0), cpu_id="1"),
            _entry_without_seq("dhcp", "30", _ts(1, 3, 0, 2, 0), cpu_id="1"),
            _entry_without_seq("dhcp", "40", _ts(1, 3, 0, 3, 0), cpu_id="1"),
            _entry_without_seq("dhcp", "50", _ts(1, 3, 0, 4, 0), cpu_id="1"),
        ]

        cycles = det.detect(board + cpu)

        aaa_cycles = [
            cycle
            for cycle in cycles
            if any(p.process_name == "aaa" and p.pid == "100" for p in cycle.processes)
        ]
        assert len(aaa_cycles) == 1
        aaa = [p for p in aaa_cycles[0].processes if p.process_name == "aaa" and p.pid == "100"][0]
        assert [log.timestamp for log in aaa.logs] == [entry.timestamp for entry in board]
        assert any("cycle split diagnostic: scoped_cpu_split" in error for error in det.errors)

    def test_cpu_subcard_isolation(self, detector):
        board = [
            _entry("svc", "100", i, _ts(1, 3, 0, i)) for i in range(1, 6)
        ]
        cpu = [
            _entry("dhcp", "50", i, _ts(1, 3, 0, i), cpu_id="1") for i in range(1, 4)
        ] + [
            _entry("dhcp", "60", i, _ts(1, 3, 1, i), cpu_id="1") for i in range(1, 4)
        ]
        cycles = detector.detect(board + cpu)

        assert len(cycles) == 3
        board_cycles = [
            cycle
            for cycle in cycles
            if any(p.process_name == "svc" and p.pid == "100" for p in cycle.processes)
        ]
        assert len(board_cycles) == 1
        board_proc = [p for p in board_cycles[0].processes if p.process_name == "svc" and p.pid == "100"][0]
        assert [log.timestamp for log in board_proc.logs] == [entry.timestamp for entry in board]
        assert any(p.process_name == "dhcp" and p.pid == "50" for cycle in cycles for p in cycle.processes)
        assert any(p.process_name == "dhcp" and p.pid == "60" for cycle in cycles for p in cycle.processes)
        assert any("cycle split diagnostic: scoped_cpu_split" in error for error in detector.errors)


class TestSplitTrace:
    """切分原因追踪。"""

    def test_split_trace_on_pid_change(self, detector):
        """indicator PID 变化 → 结果中出现 split_traces。"""
        entries = [
            _entry("dhcp", "100", i, _ts(1, 3, 0, i)) for i in range(1, 4)
        ] + [
            _entry("dhcp", "200", i, _ts(1, 3, 1, i)) for i in range(1, 4)
        ]
        cycles = detector.detect(entries)
        assert len(cycles) == 2

        # 至少有一个 cycle 包含 split trace
        all_traces: list[MechCycleSplitTrace] = []
        for c in cycles:
            all_traces.extend(c.split_traces)
        assert len(all_traces) == 1

        trace = all_traces[0]
        assert trace.old_pid == "100"
        assert trace.new_pid == "200"
        assert trace.reason == "indicator_pid_changed"
        assert trace.indicator == "dhcp"

    def test_no_split_trace_without_pid_change(self, detector):
        """无 PID 变化 → 无 split_traces。"""
        entries = [_entry("svc", "100", i, _ts(1, 3, 0, i)) for i in range(1, 6)]
        cycles = detector.detect(entries)
        assert len(cycles) == 1
        assert cycles[0].split_traces == []


class TestSequenceModeSelection:
    def test_cycle_without_sequences_orders_process_logs_by_timestamp(self):
        det = CycleDetector(indicator=None)
        entries = [
            _entry_without_seq("svc", "100", _ts(1, 3, 0, 3)),
            _entry_without_seq("svc", "100", _ts(1, 3, 0, 1)),
            _entry_without_seq("svc", "100", _ts(1, 3, 0, 2), cpu_id="1"),
        ]

        cycles = det.detect(entries)

        assert len(cycles) == 1
        proc = [p for p in cycles[0].processes if p.pid == "100" and p.logs[0].cpu_id == ""][0]
        assert [log.timestamp for log in proc.logs] == [
            _ts(1, 3, 0, 1),
            _ts(1, 3, 0, 3),
        ]
        assert proc.missing_sequences == []

    def test_cycle_with_sequences_orders_process_logs_by_sequence(self):
        det = CycleDetector(indicator=None)
        entries = [
            _entry("svc", "100", 3, _ts(1, 3, 0, 1)),
            _entry("svc", "100", 1, _ts(1, 3, 0, 3)),
            _entry("svc", "100", 2, _ts(1, 3, 0, 2)),
        ]

        cycles = det.detect(entries)

        proc = cycles[0].processes[0]
        assert [log.sequence for log in proc.logs] == [1, 2, 3]
        assert proc.missing_sequences == []

    def test_mixed_sequence_availability_warns_and_uses_timestamp(self, caplog):
        det = CycleDetector(indicator=None)
        entries = [
            _entry("svc", "100", 3, _ts(1, 3, 0, 1)),
            _entry_without_seq("svc", "100", _ts(1, 3, 0, 2)),
        ]

        with caplog.at_level(logging.WARNING, logger="backend.parsing.cycle_detector"):
            cycles = det.detect(entries)

        proc = cycles[0].processes[0]
        assert [log.timestamp for log in proc.logs] == [
            _ts(1, 3, 0, 1),
            _ts(1, 3, 0, 2),
        ]
        assert proc.missing_sequences == []
        assert "mixed sequence availability" in caplog.text

    def test_board_and_cpu_mixed_sequence_warns_for_slot_family(self, caplog):
        det = CycleDetector(indicator=None)
        entries = [
            _entry("board_svc", "100", 2, _ts(1, 3, 0, 1)),
            _entry("board_svc", "100", 1, _ts(1, 3, 0, 3)),
            _entry_without_seq("cpu_svc", "200", _ts(1, 3, 0, 2), cpu_id="1"),
        ]

        with caplog.at_level(logging.WARNING, logger="backend.parsing.cycle_detector"):
            cycles = det.detect(entries)

        board_proc = [p for p in cycles[0].processes if p.process_name == "board_svc"][0]
        assert [log.sequence for log in board_proc.logs] == [2, 1]
        assert board_proc.missing_sequences == []
        assert "mixed sequence availability" in caplog.text
