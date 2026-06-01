from __future__ import annotations

import logging
from datetime import datetime, timedelta

from backend.models import MechLogEntry
from backend.parsing.lifecycle_splitter import LifecycleSplitConfig
from backend.parsing.lifecycle_splitter_v3 import LifecycleSplitterV3


def _ts(seconds: int) -> datetime:
    return datetime(2026, 1, 3, 0, 0, 0) + timedelta(seconds=seconds)


def _entry(
    process_name: str,
    pid: str,
    seconds: int,
    *,
    cpu_id: str = "",
    source: str = "diagnostic",
    sequence: int = 0,
    context: str = "",
) -> MechLogEntry:
    return MechLogEntry(
        timestamp=_ts(seconds),
        source=source,
        slot="1",
        cpu_id=cpu_id,
        process_name=process_name,
        pid=pid,
        sequence=sequence,
        context=context,
        raw=f"{process_name}-{pid} {context}",
    )


def _splitter(*, reliable: list[str] | None = None, multi: list[str] | None = None) -> LifecycleSplitterV3:
    return LifecycleSplitterV3(
        LifecycleSplitConfig.from_mapping({
            "enabled": True,
            "algorithm": "interval_v3",
            "reliable_processes": reliable or [],
            "multi_instance_processes": multi or [],
        })
    )


def _log_count(cycle) -> int:
    return sum(len(process.logs) for process in cycle.processes)


def test_gap_29_seconds_stays_in_one_candidate_segment():
    result = _splitter(reliable=["anchor"]).split([
        _entry("anchor", "100", 0),
        _entry("worker", "500", 29),
    ])

    assert result.algorithm == "interval_v3"
    assert len(result.candidate_segments) == 1
    assert len(result.lifecycles) == 1
    assert result.merge_decisions == []


def test_gap_30_seconds_initially_splits_then_merges_without_reliable_pid_conflict():
    result = _splitter(reliable=["anchor"]).split([
        _entry("anchor", "100", 0),
        _entry("worker", "500", 29),
        _entry("worker", "500", 58),
        _entry("other", "900", 88),
    ])

    assert len(result.candidate_segments) == 2
    assert len(result.lifecycles) == 1
    decision = result.merge_decisions[0]
    assert decision.decision == "merged"
    assert decision.silent_gap_seconds == 30
    assert "没有白名单进程 PID 冲突" in decision.reason_zh


def test_reliable_pid_conflict_keeps_candidate_split():
    result = _splitter(reliable=["anchor"]).split([
        _entry("anchor", "100", 0),
        _entry("anchor", "200", 30),
    ])

    assert len(result.candidate_segments) == 2
    assert len(result.lifecycles) == 2
    decision = result.merge_decisions[0]
    assert decision.decision == "kept_split"
    assert decision.blocking_reason == "reliable_pid_conflict"
    assert decision.whitelist_pid_counts[0]["process_name"] == "anchor"
    assert decision.whitelist_pid_counts[0]["pids"] == ["100", "200"]
    assert result.lifecycle_reliable is True


def test_same_reliable_process_pid_gap_30_merges_candidate_segments():
    result = _splitter(reliable=["anchor"]).split([
        _entry("anchor", "100", 0),
        _entry("anchor", "100", 30),
    ])

    assert len(result.candidate_segments) == 2
    assert len(result.lifecycles) == 1
    assert result.merge_decisions[0].decision == "merged"
    assert result.issues == []


def test_cpu_local_pid_conflict_does_not_keep_board_split():
    result = _splitter(reliable=["anchor"]).split([
        _entry("anchor", "100", 0, cpu_id="1"),
        _entry("anchor", "200", 30, cpu_id="1"),
    ])

    board_lifecycles = [item for item in result.lifecycles if item.scope == "board"]
    cpu_lifecycles = [item for item in result.lifecycles if item.scope == "cpu"]

    assert len(board_lifecycles) == 1
    assert len(cpu_lifecycles) == 2
    assert any(
        decision.scope == "board" and decision.decision == "merged"
        for decision in result.merge_decisions
    )
    assert any(
        decision.scope == "cpu" and decision.blocking_reason == "reliable_pid_conflict"
        for decision in result.merge_decisions
    )


def test_no_reliable_processes_allows_candidate_segments_to_merge():
    result = _splitter().split([
        _entry("ordinary", "100", 0),
        _entry("another", "200", 30),
    ])

    assert len(result.candidate_segments) == 2
    assert len(result.lifecycles) == 1
    assert result.merge_decisions[0].decision == "merged"


def test_ordinary_unique_process_pid_change_does_not_keep_candidate_split():
    result = _splitter(reliable=["anchor"]).split([
        _entry("ordinary", "100", 0),
        _entry("ordinary", "200", 30),
    ])

    assert len(result.candidate_segments) == 2
    assert len(result.lifecycles) == 1
    assert result.merge_decisions[0].decision == "merged"
    assert result.issues == []


def test_multi_instance_processes_do_not_block_merge():
    result = _splitter(multi=["worker"]).split([
        _entry("worker", "100", 0),
        _entry("worker", "200", 30),
    ])

    assert len(result.candidate_segments) == 2
    assert len(result.lifecycles) == 1
    assert result.merge_decisions[0].decision == "merged"


def test_multi_instance_same_pid_across_kept_boundary_does_not_report_pid_reuse():
    result = _splitter(multi=["worker"]).split([
        _entry("journal", "", 0, source="journal", sequence=99),
        _entry("worker", "500", 10),
        _entry("journal", "", 40, source="journal", sequence=1),
        _entry("worker", "500", 50),
    ])

    assert len(result.lifecycles) == 2
    assert not any(issue.type == "pid_reuse_assumption_violation" for issue in result.issues)


def test_reliable_journal_wrap_across_candidate_segments_keeps_split():
    result = _splitter(reliable=["anchor"]).split([
        _entry("anchor", "", 0, source="journal", sequence=99, context="No[99] old"),
        _entry("anchor", "", 30, source="journal", sequence=1, context="No[1] new"),
    ])

    assert len(result.candidate_segments) == 2
    assert len(result.lifecycles) == 2
    assert result.merge_decisions[0].decision == "kept_split"
    assert result.merge_decisions[0].blocking_reason == "journal_wrap"
    assert result.journal_evidence[0].support_type == "boundary_support"
    assert "journal 回绕跨越相邻候选生命周期" in result.journal_evidence[0].explanation_zh


def test_reliable_pid_conflict_inside_final_lifecycle_records_error_without_auto_split():
    result = _splitter(reliable=["anchor"]).split([
        _entry("anchor", "100", 0),
        _entry("anchor", "200", 29),
    ])

    assert len(result.candidate_segments) == 1
    assert len(result.lifecycles) == 1
    assert result.lifecycle_reliable is False
    assert result.issues[0].type == "reliable_process_multiple_pid_in_lifecycle"
    assert result.issues[0].severity == "error"
    assert result.issues[0].observed_pids == ["100", "200"]


def test_process_name_mapping_is_applied_before_v3_reliable_pid_counting():
    splitter = LifecycleSplitterV3(
        LifecycleSplitConfig.from_mapping({
            "enabled": True,
            "algorithm": "interval_v3",
            "process_name_mapping": {"anchor": ["anchord"]},
            "reliable_processes": ["anchor"],
        })
    )

    result = splitter.split([
        _entry("anchord", "100", 0),
        _entry("anchor", "200", 30),
    ])

    assert len(result.lifecycles) == 2
    assert result.merge_decisions[0].blocking_reason == "reliable_pid_conflict"
    assert result.merge_decisions[0].whitelist_pid_counts[0]["process_name"] == "anchor"


def test_build_board_cycles_archives_each_log_once_and_nests_nonzero_cpu_logs():
    splitter = _splitter(reliable=["anchor"])
    result = splitter.split([
        _entry("anchor", "100", 0, cpu_id="0"),
        _entry("boardproc", "300", 10),
        _entry("cpuworker", "10", 20, cpu_id="1"),
        _entry("cpuworker", "10", 50, cpu_id="1"),
    ])

    board_cycles = splitter.build_board_cycles(result)

    assert len(board_cycles) == 1
    assert len(board_cycles[0].cpu_cycles) == 1
    assert _log_count(board_cycles[0]) == 2
    assert _log_count(board_cycles[0].cpu_cycles[0]) == 2
    assert all(
        log.cpu_id == ""
        for process in board_cycles[0].processes
        for log in process.logs
    )
    assert {
        log.raw
        for process in board_cycles[0].cpu_cycles[0].processes
        for log in process.logs
    } == {"cpuworker-10 ", "cpuworker-10 "}


def test_build_board_cycles_merges_pidless_journal_after_process_name_mapping():
    splitter = LifecycleSplitterV3(
        LifecycleSplitConfig.from_mapping({
            "enabled": True,
            "algorithm": "interval_v3",
            "process_name_mapping": {"svc": ["svc_journal"]},
        })
    )
    result = splitter.split([
        _entry("svc", "100", 0, source="diagnostic", context="diag"),
        _entry("svc_journal", "", 5, source="journal", sequence=1, context="journal"),
    ])

    board_cycles = splitter.build_board_cycles(result)

    assert len(board_cycles) == 1
    assert [(process.process_name, process.pid) for process in board_cycles[0].processes] == [
        ("svc", "100")
    ]
    process = board_cycles[0].processes[0]
    assert [log.source for log in process.logs] == ["diagnostic", "journal"]
    assert [log.process_name for log in process.logs] == ["svc", "svc"]


def test_build_board_cycles_keeps_pidless_journal_separate_when_multiple_pid_targets():
    splitter = _splitter()
    result = splitter.split([
        _entry("svc", "100", 0, source="diagnostic", context="diag old"),
        _entry("svc", "", 5, source="journal", sequence=1, context="journal"),
        _entry("svc", "200", 10, source="diagnostic", context="diag new"),
    ])

    board_cycles = splitter.build_board_cycles(result)

    assert len(board_cycles) == 1
    assert [(process.process_name, process.pid) for process in board_cycles[0].processes] == [
        ("svc", ""),
        ("svc", "100"),
        ("svc", "200"),
    ]
    pidless = board_cycles[0].processes[0]
    assert [log.source for log in pidless.logs] == ["journal"]


def test_build_board_cycles_logs_lifecycle_process_group_counts(caplog):
    splitter = _splitter(reliable=["anchor"])
    result = splitter.split([
        _entry("anchor", "100", 0),
        _entry("boardproc", "300", 10),
        _entry("cpuworker", "10", 20, cpu_id="1"),
        _entry("cpuworker", "10", 80, cpu_id="1"),
    ])

    caplog.set_level(logging.INFO, logger="backend.parsing.lifecycle_splitter_v3")
    splitter.build_board_cycles(result)

    assert "最终切分结果: 1 个周期" in caplog.text
    assert "周期[0]:" in caplog.text
    assert "2 个进程组" in caplog.text
    assert "CPU周期[1/0]:" in caplog.text
    assert "1 个进程组" in caplog.text


def test_cpu_lifecycle_ids_are_unique_across_board_lifecycles():
    result = _splitter(reliable=["anchor"]).split([
        _entry("anchor", "100", 0),
        _entry("cpuworker", "10", 5, cpu_id="1"),
        _entry("anchor", "200", 40),
        _entry("cpuworker", "10", 45, cpu_id="1"),
    ])

    ids = [lifecycle.id for lifecycle in result.lifecycles]
    cpu_parent_ids = [
        lifecycle.parent_lifecycle_id
        for lifecycle in result.lifecycles
        if lifecycle.scope == "cpu"
    ]

    assert len(ids) == len(set(ids))
    assert len(cpu_parent_ids) == 2
    assert len(set(cpu_parent_ids)) == 2


def test_kept_boundary_with_same_pid_records_pid_reuse_assumption_violation():
    result = _splitter().split([
        _entry("journal", "", 0, source="journal", sequence=99),
        _entry("ordinary", "500", 10),
        _entry("journal", "", 40, source="journal", sequence=1),
        _entry("ordinary", "500", 50),
    ])

    assert len(result.lifecycles) == 2
    assert any(issue.type == "pid_reuse_assumption_violation" for issue in result.issues)
