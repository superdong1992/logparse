from __future__ import annotations

from datetime import datetime, timedelta

from backend.models import MechLogEntry
from backend.parsing.lifecycle_splitter import LifecycleSplitConfig, LifecycleSplitter


def _ts(minutes: int) -> datetime:
    return datetime(2026, 1, 3, 0, 0, 0) + timedelta(minutes=minutes)


def _entry(
    process_name: str,
    pid: str,
    minutes: int | None,
    *,
    cpu_id: str = "",
    context: str = "",
    source: str = "diagnostic",
    sequence: int | None = None,
) -> MechLogEntry:
    return MechLogEntry(
        timestamp=_ts(minutes) if minutes is not None else None,
        source=source,
        slot="1",
        cpu_id=cpu_id,
        process_name=process_name,
        pid=pid,
        context=context,
        sequence=minutes if sequence is None and minutes is not None else (sequence or 0),
        raw=f"{process_name}-{pid} {context}",
    )


def _journal(
    minutes: int | None,
    sequence: int,
    *,
    cpu_id: str = "",
    process_name: str = "journal",
) -> MechLogEntry:
    return _entry(
        process_name,
        "900",
        minutes,
        cpu_id=cpu_id,
        source="journal",
        sequence=sequence,
        context=f"No[{sequence}]",
    )


def test_lifecycle_split_config_rejects_falsey_non_object_fields():
    try:
        LifecycleSplitConfig.from_mapping({
            "enabled": True,
            "process_name_mapping": [],
        })
    except ValueError as exc:
        assert "process_name_mapping" in str(exc)
        assert "object" in str(exc)
    else:
        raise AssertionError("process_name_mapping should be rejected")

    try:
        LifecycleSplitConfig.from_mapping({
            "enabled": True,
            "reliable_processes": "anchor",
        })
    except ValueError as exc:
        assert "reliable_processes" in str(exc)
        assert "list" in str(exc)
    else:
        raise AssertionError("reliable_processes should reject scalar values")


def test_lifecycle_split_config_accepts_flat_and_legacy_reliable_processes():
    flat = LifecycleSplitConfig.from_mapping({
        "reliable_processes": ["board_anchor", "cpu_anchor"],
    })
    legacy = LifecycleSplitConfig.from_mapping({
        "reliable_processes": {
            "board": ["board_anchor"],
            "cpu": ["cpu_anchor", "board_anchor"],
        },
    })

    assert flat.reliable_processes == ["board_anchor", "cpu_anchor"]
    assert legacy.reliable_processes == ["board_anchor", "cpu_anchor"]


def test_lifecycle_split_config_rejects_unknown_legacy_reliable_process_keys():
    try:
        LifecycleSplitConfig.from_mapping({
            "reliable_processes": {"boad": ["board_anchor"]},
        })
    except ValueError as exc:
        assert "unsupported legacy keys" in str(exc)
        assert "boad" in str(exc)
    else:
        raise AssertionError("legacy reliable_processes should reject unknown keys")


def test_reliable_board_pid_change_creates_v2_result_and_board_cycles():
    cfg = LifecycleSplitConfig.from_mapping({
        "process_name_mapping": {"board_anchor": ["boardd"]},
        "reliable_processes": ["board_anchor"],
        "multi_instance_processes": [],
    })
    splitter = LifecycleSplitter(cfg)

    result = splitter.split([
        _entry("boardd", "100", 0, context="old"),
        _entry("ordinary", "500", 30, context="middle"),
        _entry("boardd", "200", 60, context="new"),
        _entry("ordinary", "501", 90, context="after"),
    ])
    board_cycles = splitter.build_board_cycles(result)

    assert result.lifecycle_reliable is True
    assert len(result.boundaries) == 1
    boundary = result.boundaries[0]
    assert boundary.origin_scope == "board"
    assert boundary.slot == "1"
    assert boundary.cpu_id is None
    assert boundary.timestamp == _ts(60)
    assert boundary.type == "reliable_process_pid_changed"
    assert boundary.type_label_zh == "可靠进程 PID 变化"
    assert boundary.support_evidence
    assert [cycle.cycle_index for cycle in result.cycles] == [0, 1]
    assert [cycle.next_boundary_time for cycle in result.cycles] == [_ts(60), None]
    assert [cycle.dir_name for cycle in board_cycles] == [
        "20260103000000-20260103003000",
        "20260103010000-20260103013000",
    ]
    assert board_cycles[0].processes[0].process_name == "board_anchor"


def test_cpu_reliable_pid_change_only_creates_cpu_local_boundary():
    cfg = LifecycleSplitConfig.from_mapping({
        "reliable_processes": ["board_anchor", "cpu_anchor"],
    })
    splitter = LifecycleSplitter(cfg)

    result = splitter.split([
        _entry("board_anchor", "100", 0),
        _entry("board_anchor", "100", 90),
        _entry("cpu_anchor", "10", 10, cpu_id="1"),
        _entry("cpu_anchor", "11", 70, cpu_id="1"),
        _entry("cpu_anchor", "20", 20, cpu_id="2"),
    ])

    assert len(result.boundaries) == 1
    boundary = result.boundaries[0]
    assert boundary.origin_scope == "cpu"
    assert boundary.slot == "1"
    assert boundary.cpu_id == "1"
    assert boundary.effective_scopes == [
        {"scope": "cpu", "slot": "1", "cpu_id": "1", "inherited": False}
    ]
    board_scope = next(scope for scope in result.scopes if scope.scope == "board")
    cpu1_scope = next(scope for scope in result.scopes if scope.scope == "cpu" and scope.cpu_id == "1")
    cpu2_scope = next(scope for scope in result.scopes if scope.scope == "cpu" and scope.cpu_id == "2")
    assert board_scope.effective_boundaries == []
    assert [b["timestamp"] for b in cpu1_scope.effective_boundaries] == [_ts(70)]
    assert cpu2_scope.effective_boundaries == []


def test_board_journal_sequence_wrap_creates_board_boundary():
    splitter = LifecycleSplitter(LifecycleSplitConfig.from_mapping({}))

    result = splitter.split([
        _journal(10, 98),
        _journal(40, 3),
    ])

    assert len(result.boundaries) == 1
    boundary = result.boundaries[0]
    assert boundary.origin_scope == "board"
    assert boundary.cpu_id is None
    assert boundary.timestamp == _ts(40)
    assert boundary.type == "journal_sequence_wrapped"
    assert result.evidence[0].type == "journal_sequence_wrapped"
    assert result.evidence[0].support_type == "tight_support"


def test_cpu_journal_sequence_wrap_only_creates_cpu_local_boundary():
    splitter = LifecycleSplitter(LifecycleSplitConfig.from_mapping({}))

    result = splitter.split([
        _journal(10, 98, cpu_id="1"),
        _journal(40, 3, cpu_id="1"),
        _journal(20, 11, cpu_id="2"),
        _journal(50, 12, cpu_id="2"),
    ])

    assert len(result.boundaries) == 1
    boundary = result.boundaries[0]
    assert boundary.origin_scope == "cpu"
    assert boundary.cpu_id == "1"
    board_scope = next(scope for scope in result.scopes if scope.scope == "board")
    cpu1_scope = next(scope for scope in result.scopes if scope.scope == "cpu" and scope.cpu_id == "1")
    cpu2_scope = next(scope for scope in result.scopes if scope.scope == "cpu" and scope.cpu_id == "2")
    assert board_scope.effective_boundaries == []
    assert [b["timestamp"] for b in cpu1_scope.effective_boundaries] == [_ts(40)]
    assert cpu2_scope.effective_boundaries == []


def test_invalid_lifecycle_evidence_marks_scope_and_result_unreliable():
    cfg = LifecycleSplitConfig.from_mapping({
        "reliable_processes": ["board_anchor", "cpu_anchor"],
    })
    splitter = LifecycleSplitter(cfg)

    result = splitter.split([
        _entry("board_anchor", "", 10),
        _entry("cpu_anchor", "100", 20),
        _journal(30, 0),
    ])

    assert result.lifecycle_reliable is False
    assert {issue.type for issue in result.issues} == {"invalid_lifecycle_evidence"}
    assert len(result.issues) == 2
    assert any("PID" in issue.explanation_zh for issue in result.issues)
    assert any("journal" in issue.explanation_zh for issue in result.issues)
    assert any(scope.lifecycle_reliable is False for scope in result.scopes)
    assert all(issue.rule_zh for issue in result.issues)
    assert all(issue.facts_zh for issue in result.issues)
    assert all(issue.current_result_zh for issue in result.issues)
    assert all(issue.conflict_reason_zh for issue in result.issues)
    assert all(issue.impact_zh for issue in result.issues)
    assert all(issue.action_zh for issue in result.issues)
    assert all(issue.observed_time is not None for issue in result.issues)
    assert all(
        issue.affected_cycles
        == [{"scope": "board", "slot": "1", "cpu_id": None, "cycle_index": 0}]
        for issue in result.issues
    )
    assert result.cycles[0].lifecycle_reliable is False


def test_same_timestamp_journal_sequence_wrap_is_invalid_evidence():
    cfg = LifecycleSplitConfig.from_mapping({})
    splitter = LifecycleSplitter(cfg)

    result = splitter.split([
        _journal(10, 98),
        _journal(10, 3),
    ])

    assert result.lifecycle_reliable is False
    assert result.boundaries == []
    issues = [issue for issue in result.issues if issue.type == "invalid_lifecycle_evidence"]
    assert len(issues) == 1
    assert "时间顺序" in issues[0].explanation_zh
    assert issues[0].affected_cycles == [
        {"scope": "board", "slot": "1", "cpu_id": None, "cycle_index": 0}
    ]


def test_reliable_pid_change_support_keeps_sequence_numbers():
    cfg = LifecycleSplitConfig.from_mapping({
        "reliable_processes": ["anchor"],
    })
    splitter = LifecycleSplitter(cfg)

    result = splitter.split([
        _entry("anchor", "10", 10, sequence=100),
        _entry("anchor", "11", 70, sequence=101),
    ])

    support = result.boundaries[0].support_evidence[0]
    assert support["old_sequence"] == 100
    assert support["new_sequence"] == 101


def test_flat_reliable_process_without_cpu_id_uses_board_scope():
    cfg = LifecycleSplitConfig.from_mapping({
        "reliable_processes": ["cpu_anchor"],
    })
    splitter = LifecycleSplitter(cfg)

    result = splitter.split([
        _entry("cpu_anchor", "100", 10),
        _entry("worker", "500", 20, cpu_id="1"),
    ])

    assert result.lifecycle_reliable is True
    assert not result.issues
    assert any(scope.scope == "board" for scope in result.scopes)


def test_cpu_evidence_satisfied_by_inherited_board_boundary_is_tight_support():
    cfg = LifecycleSplitConfig.from_mapping({
        "reliable_processes": ["board_anchor", "cpu_anchor"],
    })
    splitter = LifecycleSplitter(cfg)

    result = splitter.split([
        _entry("board_anchor", "1", 0),
        _entry("board_anchor", "2", 60),
        _entry("cpu_anchor", "10", 10, cpu_id="1"),
        _entry("cpu_anchor", "11", 70, cpu_id="1"),
    ])

    assert len(result.boundaries) == 1
    boundary = result.boundaries[0]
    assert boundary.origin_scope == "board"
    assert any(item["evidence_scope"] == "cpu" for item in boundary.support_evidence)
    cpu_scope = next(item for item in result.scopes if item.scope == "cpu")
    assert cpu_scope.effective_boundaries[0]["scope"] == "cpu"
    assert cpu_scope.effective_boundaries[0]["cpu_id"] == "1"
    cpu_evidence = next(item for item in result.evidence if item.scope == "cpu")
    assert cpu_evidence.support_type == "tight_support"
    assert cpu_evidence.covered_boundaries[0]["inherited"] is True
    assert cpu_evidence.covered_boundaries[0]["scope"] == "cpu"
    assert cpu_evidence.covered_boundaries[0]["cpu_id"] == "1"


def test_wide_support_is_not_attached_to_single_boundary():
    cfg = LifecycleSplitConfig.from_mapping({
        "reliable_processes": ["board_anchor", "cpu_anchor", "cpu_aux"],
    })
    splitter = LifecycleSplitter(cfg)

    result = splitter.split([
        _entry("board_anchor", "1", 0),
        _entry("board_anchor", "2", 60),
        _entry("cpu_anchor", "10", 10, cpu_id="1"),
        _entry("cpu_anchor", "11", 120, cpu_id="1"),
        _entry("cpu_aux", "20", 80, cpu_id="1"),
        _entry("cpu_aux", "21", 100, cpu_id="1"),
    ])

    wide = next(item for item in result.evidence if item.process_name == "cpu_anchor")
    assert wide.support_type == "wide_support"
    assert len(wide.covered_boundaries) == 2
    assert all(
        item["process_name"] != "cpu_anchor"
        for boundary in result.boundaries
        for item in boundary.support_evidence
    )


def test_multi_instance_process_is_excluded_from_same_pid_check():
    cfg = LifecycleSplitConfig.from_mapping({
        "reliable_processes": ["board_anchor"],
        "multi_instance_processes": ["worker"],
    })
    splitter = LifecycleSplitter(cfg)

    result = splitter.split([
        _entry("board_anchor", "1", 0),
        _entry("worker", "500", 10),
        _entry("board_anchor", "2", 60),
        _entry("worker", "500", 70),
    ])

    assert result.lifecycle_reliable is True
    assert not result.issues


def test_same_pid_reuse_in_non_adjacent_cycles_is_allowed():
    cfg = LifecycleSplitConfig.from_mapping({
        "reliable_processes": ["board_anchor"],
    })
    splitter = LifecycleSplitter(cfg)

    result = splitter.split([
        _entry("board_anchor", "1", 0),
        _entry("worker", "500", 10),
        _entry("board_anchor", "2", 30),
        _entry("worker", "600", 40),
        _entry("board_anchor", "3", 60),
        _entry("worker", "500", 70),
    ])

    assert result.lifecycle_reliable is True
    assert not result.issues


def test_same_pid_adjacent_pairs_are_merged_into_one_issue():
    cfg = LifecycleSplitConfig.from_mapping({
        "reliable_processes": ["board_anchor"],
    })
    splitter = LifecycleSplitter(cfg)

    result = splitter.split([
        _entry("board_anchor", "1", 0),
        _entry("worker", "500", 10),
        _entry("board_anchor", "2", 30),
        _entry("worker", "500", 40),
        _entry("board_anchor", "3", 60),
        _entry("worker", "500", 70),
        _entry("board_anchor", "4", 90),
        _entry("worker", "500", 100),
    ])

    issues = [issue for issue in result.issues if issue.type == "same_pid_single_boundary_conflict"]
    assert len(issues) == 1
    assert issues[0].related_process == "worker-500"
    assert len(issues[0].conflicting_cycle_pairs) == 3
    assert result.lifecycle_reliable is False


def test_cpu_same_pid_conflict_only_marks_that_cpu_scope_unreliable():
    cfg = LifecycleSplitConfig.from_mapping({
        "reliable_processes": ["board_anchor"],
    })
    splitter = LifecycleSplitter(cfg)

    result = splitter.split([
        _entry("board_anchor", "1", 0),
        _entry("worker", "500", 10, cpu_id="1"),
        _entry("worker", "900", 10, cpu_id="2"),
        _entry("board_anchor", "2", 60),
        _entry("worker", "500", 70, cpu_id="1"),
        _entry("worker", "901", 70, cpu_id="2"),
    ])

    board_scope = next(scope for scope in result.scopes if scope.scope == "board")
    cpu1_scope = next(scope for scope in result.scopes if scope.scope == "cpu" and scope.cpu_id == "1")
    cpu2_scope = next(scope for scope in result.scopes if scope.scope == "cpu" and scope.cpu_id == "2")
    assert result.lifecycle_reliable is False
    assert board_scope.lifecycle_reliable is True
    assert cpu1_scope.lifecycle_reliable is False
    assert cpu2_scope.lifecycle_reliable is True


def test_flat_reliable_process_uses_actual_cpu_scope_for_pid_boundary():
    cfg = LifecycleSplitConfig.from_mapping({
        "reliable_processes": ["anchor"],
    })
    splitter = LifecycleSplitter(cfg)

    result = splitter.split([
        _entry("anchor", "10", 10, cpu_id="1"),
        _entry("anchor", "11", 70, cpu_id="1"),
    ])

    assert result.lifecycle_reliable is True
    assert len(result.boundaries) == 1
    boundary = result.boundaries[0]
    assert boundary.origin_scope == "cpu"
    assert boundary.cpu_id == "1"
    assert boundary.timestamp == _ts(70)


def test_reliable_process_multiple_pid_in_same_cycle_is_error_with_dfx_payload():
    cfg = LifecycleSplitConfig.from_mapping({
        "reliable_processes": ["anchor"],
    })
    splitter = LifecycleSplitter(cfg)

    result = splitter.split([
        _entry("anchor", "10", 10, context="old"),
        _entry("anchor", "11", 10, context="new"),
    ])

    issues = [
        issue
        for issue in result.issues
        if issue.type == "reliable_process_multiple_pid_in_cycle"
    ]
    assert len(issues) == 1
    issue = issues[0]
    assert result.lifecycle_reliable is False
    assert issue.related_process == "anchor"
    assert issue.affected_cycles == [
        {"scope": "board", "slot": "1", "cpu_id": None, "cycle_index": 0}
    ]
    assert issue.observed_pids == ["10", "11"]
    assert issue.pid_runs == [
        {
            "pid": "10",
            "first_seen": _ts(10),
            "last_seen": _ts(10),
            "first_raw": "anchor-10 old",
            "last_raw": "anchor-10 old",
        },
        {
            "pid": "11",
            "first_seen": _ts(10),
            "last_seen": _ts(10),
            "first_raw": "anchor-11 new",
            "last_raw": "anchor-11 new",
        },
    ]
    assert issue.expected_boundary_intervals[0]["old_pid"] == "10"
    assert issue.expected_boundary_intervals[0]["new_pid"] == "11"
    assert issue.covered_boundaries == []
    assert "同一个生命周期" in issue.explanation_zh
