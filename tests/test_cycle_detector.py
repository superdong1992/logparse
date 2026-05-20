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

from backend.models import MechLogEntry
from backend.parsing.cycle_detector import CycleDetector


def _ts(month: int, day: int, hour: int, minute: int = 0, sec: int = 0) -> datetime:
    tz = timezone(timedelta(hours=8))
    return datetime(2026, month, day, hour, minute, sec, tzinfo=tz)


def _entry(
    proc: str, pid: str, seq: int, ts: datetime,
    cpu_id: str = "", source: str = "diagnostic",
) -> MechLogEntry:
    return MechLogEntry(
        timestamp=ts, source=source, slot="1", cpu_id=cpu_id,
        process_name=proc, pid=pid, sequence=seq,
        raw=f"{proc}-{pid}-No[{seq}]",
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

    def test_non_whitelist_process_not_split(self):
        """非白名单进程的同 PID 段可能被切分（被动分配）。"""
        det = CycleDetector(indicator="dhcp", whitelist=["svc_a"])
        entries = [
            _entry("dhcp", "100", 1, _ts(1, 3, 0, 0)),
            _entry("dhcp", "200", 1, _ts(1, 3, 6, 0)),
            _entry("svc_a", "300", 1, _ts(1, 3, 0, 0)),
            _entry("svc_a", "300", 2, _ts(1, 3, 5, 59)),
            _entry("svc_a", "400", 1, _ts(1, 3, 6, 1)),
            # 非白名单 other: PID 500 横跨切分点
            _entry("other", "500", 1, _ts(1, 3, 5, 0)),
            _entry("other", "500", 2, _ts(1, 3, 7, 0)),
        ]
        cycles = det.detect(entries)
        assert len(cycles) == 2


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
        # CPU-1 的 PID 变化产生切分点
        assert len(cycles) == 2
        # 周期1: 板卡进程 + CPU-1 重启前
        board_procs = [p for p in cycles[0].processes if p.pid == "100"]
        cpu_procs = [p for p in cycles[0].processes if p.pid == "50"]
        assert len(board_procs) == 1
        assert len(cpu_procs) == 1
        # 周期2: CPU-1 重启后
        cpu_procs_after = [p for p in cycles[1].processes if p.pid == "60"]
        assert len(cpu_procs_after) == 1
