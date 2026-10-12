from __future__ import annotations


from datetime import datetime

from pathlib import Path

import click


from backend.extensions.products.current.models import ParseResult


def _cycle_process_total(cycle) -> tuple[int, int]:
    processes = list(cycle.processes)
    for cpu_cycle in cycle.cpu_cycles:
        processes.extend(cpu_cycle.processes)
    return len(processes), sum(process.total_count for process in processes)


def _cycle_process_total_dict(cycle: dict) -> tuple[int, int]:
    processes = list(cycle.get("processes", []))
    for cpu_cycle in cycle.get("cpu_cycles", []):
        processes.extend(cpu_cycle.get("processes", []))
    return len(processes), sum(process.get("total_count", 0) for process in processes)


def _print_summary(
    result: ParseResult,
    output_dir: Path,
    detailed: bool = True,
) -> None:
    """展示产品摘要；正式 result.json 已由 ParseService 原子写入。"""
    click.echo("\n=== 解析结果 ===")
    click.echo(f"压缩包: {result.package_name}")
    click.echo(f"诊断日志槽位数: {len(result.diagnostic_slots)}")
    click.echo(f"私有日志槽位数: {len(result.private_slots)}")

    if not detailed:
        json_output = output_dir / result.task_id / "result.json"
        click.echo(f"机制模块结果数: {len(result.mech_results)}")
        click.echo(f"\n完整结果: {json_output}")
        return

    for slot in result.diagnostic_slots:
        diag_count = len(slot.diagnostic_logs)
        click.echo(f"  {slot.name} [角色: {slot.role.value}] {diag_count} 个诊断日志")
        if slot.active_periods:
            click.echo(f"    主控时段 ({len(slot.active_periods)} 段):")
            for period in slot.active_periods:
                dur = period.duration
                click.echo(
                    f"      {period.start.isoformat()} ~ {period.end.isoformat()} (持续 {dur})"
                )
        for log in slot.diagnostic_logs:
            dump = log.dump_time.strftime("%Y-%m-%d %H:%M:%S") if log.dump_time else "无"
            ts_count = (
                f", 时间戳: {len(log.content_timestamps)} 条" if log.content_timestamps else ""
            )
            click.echo(f"    └── {log.name} ({log.size_bytes} bytes) [转储时间: {dump}{ts_count}]")

    if result.private_slots:
        click.echo("\n--- 私有日志 (varlog) ---")
        for ps in result.private_slots:
            cpu_info = f" [CPU: {ps.cpu_id}]" if ps.cpu_id else ""
            click.echo(f"  {ps.dir_name} (slot_id={ps.slot_id}{cpu_info})")
            journal_count = len(ps.journal_logs)
            if journal_count > 0:
                click.echo(f"    journal 日志: {journal_count} 个文件")
                for jl in ps.journal_logs:
                    seq_info = "当前" if jl.sequence == 0 else f"历史 #{jl.sequence}"
                    click.echo(f"      └── {jl.name} ({jl.size_bytes} bytes) [{seq_info}]")

    if result.mech_results:
        for mech_result in result.mech_results:
            click.echo(f"\n--- 机制模块 [{mech_result.module_name}] 日志 ---")
            click.echo(f"  主控信号槽位: {mech_result.active_master_slots}")
            for s in mech_result.slots:
                totals = [_cycle_process_total(c) for c in s.board_cycles]
                total_procs = sum(proc_count for proc_count, _log_count in totals)
                total_logs = sum(log_count for _proc_count, log_count in totals)
                click.echo(
                    f"  slot_{s.slot_id}: {len(s.board_cycles)} 个周期, {total_procs} 进程, {total_logs} 条日志"
                )
                for c in s.board_cycles:
                    click.echo(f"    {c.dir_name}")
                    for p in c.processes:
                        missing = f" 丢号:{p.missing_sequences}" if p.missing_sequences else ""
                        click.echo(f"      {p.process_name}-{p.pid}: {p.total_count} 条{missing}")

                    for cpu_cycle in c.cpu_cycles:
                        click.echo(f"      cpu_{cpu_cycle.cpu_id}/{cpu_cycle.dir_name}")
                        for p in cpu_cycle.processes:
                            missing = f" 丢号:{p.missing_sequences}" if p.missing_sequences else ""
                            click.echo(
                                f"        {p.process_name}-{p.pid}: {p.total_count} 条{missing}"
                            )

    json_output = output_dir / result.task_id / "result.json"
    click.echo(f"\n完整结果: {json_output}")


def _time_text(value) -> str:
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _parse_time_value(value) -> datetime | None:
    text = _time_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _time_wall_text(value) -> str:
    text = _time_text(value)
    if not text:
        return ""
    return text.replace("Z", "+00:00")[:19]


def _format_issue_time(value) -> str:
    return _time_text(value) or "-"


def _issue_get(issue, key: str, default=None):
    if isinstance(issue, dict):
        return issue.get(key, default)
    return getattr(issue, key, default)


def _severity_key(value) -> str:
    severity = str(value or "warning").upper()
    if severity not in {"ERROR", "WARNING", "INFO"}:
        return "WARNING"
    return severity


def _empty_issue_counts() -> dict[str, int]:
    return {"ERROR": 0, "WARNING": 0, "INFO": 0}


def _format_issue_counts(counts: dict[str, int]) -> str:
    return f"ERROR={counts['ERROR']} WARNING={counts['WARNING']} INFO={counts['INFO']}"


def _as_plain(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


def _iter_result_lifecycle_results(result: ParseResult):
    for mech_result in result.mech_results:
        module_name = mech_result.module_name or mech_result.module_key
        for slot in mech_result.slots:
            lifecycle_result = _as_plain(slot.lifecycle_split_result)
            if not isinstance(lifecycle_result, dict):
                continue
            yield module_name, slot.slot_id, lifecycle_result


def _iter_result_lifecycle_issues(result: ParseResult):
    for module_name, slot_id, lifecycle_result in _iter_result_lifecycle_results(result):
        for issue in lifecycle_result.get("issues") or []:
            yield module_name, slot_id, issue


def _lifecycle_algorithm(result: dict) -> str:
    return str(result.get("algorithm") or "legacy")


def _has_lifecycle_split_payload(result: dict) -> bool:
    if "algorithm" in result:
        return True
    return any(
        result.get(field)
        for field in (
            "boundaries",
            "evidence",
            "issues",
            "scopes",
            "cycles",
            "candidate_segments",
            "merge_decisions",
            "lifecycles",
            "journal_evidence",
        )
    )


def _lifecycle_dfx_detail(mode: str) -> str:
    if mode == "full":
        return "full"
    if mode == "decisions":
        return "decisions"
    return "summary"


def _print_parse_errors(
    result: ParseResult,
    *,
    verbose: bool = False,
    lifecycle_dfx: str = "errors",
) -> None:
    normal_errors = list(result.errors)
    lifecycle_issues = list(_iter_result_lifecycle_issues(result))
    counts = _empty_issue_counts()
    for _module_name, _slot_id, issue in lifecycle_issues:
        counts[_severity_key(_issue_get(issue, "severity"))] += 1

    if lifecycle_dfx != "off" and sum(counts.values()):
        click.echo(f"\nLifecycle split diagnostics: {_format_issue_counts(counts)}")
        printed_commands: set[str] = set()
        for module_name, slot_id, issue in lifecycle_issues:
            if (
                lifecycle_dfx in {"summary", "decisions", "full"}
                or _severity_key(_issue_get(issue, "severity")) == "ERROR"
            ):
                _print_lifecycle_issue_compact(issue, indent="  ")
                command = (
                    f"python cli.py mech-lifecycles {result.task_id} "
                    f"-s {slot_id} -m {module_name} --show-boundaries"
                )
                if command not in printed_commands:
                    click.echo(f"    Locate: {command}")
                    printed_commands.add(command)

    if lifecycle_dfx in {"summary", "decisions", "full"}:
        lifecycle_results = list(_iter_result_lifecycle_results(result))
        if lifecycle_results:
            click.echo("\nLifecycle split DFX:")
            for module_name, slot_id, lifecycle_result in lifecycle_results:
                click.echo(f"  module={module_name} slot={slot_id}")
                _print_lifecycle_split_result(
                    lifecycle_result,
                    detail=_lifecycle_dfx_detail(lifecycle_dfx),
                )

    if normal_errors:
        click.echo(f"\nErrors: {len(normal_errors)}")
        for err in normal_errors:
            click.echo(f"  - {err}")


def _print_lifecycle_split_result(result: dict, detail: str = "summary") -> None:
    algorithm = _lifecycle_algorithm(result)
    if algorithm == "interval_v3":
        _print_lifecycle_split_v3(result, detail=detail)
        return
    click.echo(
        f"  lifecycle_split: legacy output is unsupported; expected interval_v3, got {algorithm}"
    )


def _print_lifecycle_issue_compact(issue, *, indent: str = "    ") -> None:
    issue_type = _issue_get(issue, "type") or "-"
    severity = _severity_key(_issue_get(issue, "severity"))
    scope = _format_scope(issue)
    process = _issue_get(issue, "related_process") or ""
    observed_pids = _issue_get(issue, "observed_pids") or []
    parts = [f"{indent}[{severity}] {issue_type}", f"scope={scope}"]
    if process:
        parts.append(f"process={process}")
    if observed_pids:
        parts.append(f"pids={','.join(str(pid) for pid in observed_pids)}")
    if not process and not observed_pids:
        title = _issue_get(issue, "title_zh") or issue_type
        parts.append(f"title={title}")
    click.echo(" ".join(parts))
    reason = _issue_get(issue, "reason_zh") or _issue_get(issue, "explanation_zh")
    if reason:
        click.echo(f"{indent}  原因: {reason}")


def _print_lifecycle_split_v3(result: dict, detail: str = "summary") -> None:
    candidates = [_as_plain(item) for item in (result.get("candidate_segments") or [])]
    decisions = [_as_plain(item) for item in (result.get("merge_decisions") or [])]
    lifecycles = [_as_plain(item) for item in (result.get("lifecycles") or [])]
    journal_evidence = [_as_plain(item) for item in (result.get("journal_evidence") or [])]
    issues = [_as_plain(item) for item in (result.get("issues") or [])]
    reliable = str(result.get("lifecycle_reliable", True)).lower()
    merged = sum(1 for item in decisions if item.get("decision") == "merged")
    kept = sum(1 for item in decisions if item.get("decision") == "kept_split")
    click.echo(
        "  lifecycle_split_v3: "
        f"reliable={reliable} candidates={len(candidates)} "
        f"merged={merged} kept_splits={kept} "
        f"lifecycles={len(lifecycles)} no_wrap_evidence={len(journal_evidence)} "
        f"issues={len(issues)}"
    )
    click.echo("    [结论摘要]")
    candidate_boundary_count = _v3_candidate_boundary_count(candidates)
    click.echo(
        f"    最终切成 {len(lifecycles)} 段，可靠性={reliable}；"
        f"候选生命周期 {len(candidates)} 段，初始候选边界 {candidate_boundary_count} 条；"
        f"聚合 {merged} 次，保留切分 {kept} 次。"
    )
    main_reason = _v3_main_reason(decisions, issues)
    if main_reason:
        click.echo(f"    主要原因：{main_reason}")
    if issues:
        click.echo("    [问题处理]")
        for issue in issues:
            _print_lifecycle_issue_compact(issue, indent="    ")
            explanation = _issue_get(issue, "explanation_zh")
            if explanation:
                click.echo(f"      处理说明: {explanation}")

    if detail == "summary":
        return

    _print_lifecycle_split_v3_candidates(candidates, indent="    ")
    _print_lifecycle_split_v3_decisions(decisions, indent="    ")
    _print_lifecycle_split_v3_final_lifecycles(lifecycles, indent="    ")

    if not journal_evidence or detail != "full":
        return
    click.echo("    [边界证据汇总]")
    for evidence in journal_evidence:
        scope = _format_scope(evidence)
        old_source = evidence.get("old_source") or "-"
        new_source = evidence.get("new_source") or "-"
        click.echo(
            f"    No 回绕 scope={scope} "
            f"old_seq={evidence.get('old_sequence')} new_seq={evidence.get('new_sequence')} "
            f"old_source={old_source} new_source={new_source} "
            f"old={_format_issue_time(evidence.get('old_observed_time'))} "
            f"new={_format_issue_time(evidence.get('new_observed_time'))}"
        )
        explanation = evidence.get("explanation_zh")
        if explanation:
            click.echo(f"      说明: {explanation}")
        if detail == "full":
            old_raw = evidence.get("old_raw")
            new_raw = evidence.get("new_raw")
            if old_raw:
                click.echo(f"      old-raw {old_raw}")
            if new_raw and new_raw != old_raw:
                click.echo(f"      new-raw {new_raw}")


def _print_lifecycle_split_v3_candidates(candidates: list[dict], *, indent: str) -> None:
    click.echo(f"{indent}[候选生命周期]")
    for candidate in sorted(candidates, key=_v3_segment_sort_key):
        label = _format_candidate_indices([candidate.get("candidate_index", 0)])
        parent = candidate.get("parent_lifecycle_id") or ""
        parent_text = f" parent={parent}" if parent else ""
        click.echo(
            f"{indent}{label} {_format_v3_scope(candidate)}{parent_text} "
            f"{_format_issue_time(candidate.get('start_time'))}.."
            f"{_format_issue_time(candidate.get('end_time'))} "
            f"logs={candidate.get('log_count', 0)}"
        )
    click.echo(f"{indent}[候选切分]")
    if len(candidates) <= 1:
        click.echo(f"{indent}没有出现 >=30 秒静默间隔，初始只有一个候选生命周期。")
        return
    ordered = sorted(candidates, key=_v3_segment_sort_key)
    boundary_index = 1
    for previous, current in zip(ordered, ordered[1:]):
        if (
            previous.get("scope") != current.get("scope")
            or previous.get("slot") != current.get("slot")
            or previous.get("cpu_id") != current.get("cpu_id")
            or previous.get("parent_lifecycle_id") != current.get("parent_lifecycle_id")
        ):
            continue
        previous_label = _format_candidate_indices([previous.get("candidate_index", 0)])
        current_label = _format_candidate_indices([current.get("candidate_index", 0)])
        gap = _seconds_between(previous.get("end_time"), current.get("start_time"))
        click.echo(
            f"{indent}候选边界 #{boundary_index}："
            f"{_format_v3_scope(previous)} {previous_label} -> {current_label}"
        )
        click.echo(f"{indent}规则：相邻日志活动之间静默间隔 >=30 秒，先作为候选生命周期边界")
        click.echo(f"{indent}前一段结束：{_format_issue_time(previous.get('end_time'))}")
        click.echo(f"{indent}后一段开始：{_format_issue_time(current.get('start_time'))}")
        click.echo(f"{indent}静默间隔：{_format_seconds(gap)} 秒")
        click.echo(f"{indent}初始决策：先切成两个候选生命周期")
        boundary_index += 1


def _print_lifecycle_split_v3_decisions(decisions: list[dict], *, indent: str) -> None:
    click.echo(f"{indent}[聚合检查]")
    if not decisions:
        click.echo(f"{indent}没有相邻候选生命周期需要聚合检查。")
        return
    for decision_index, decision in enumerate(decisions, start=1):
        left = _format_candidate_indices(decision.get("left_candidate_indices") or [])
        right = _format_candidate_indices(decision.get("right_candidate_indices") or [])
        click.echo(f"{indent}聚合检查 #{decision_index}：候选生命周期 {left} + {right}")
        click.echo(f"{indent}可靠边界进程 PID 统计（白名单）：")
        pid_counts = decision.get("reliable_pid_counts") or []
        if pid_counts:
            for item in pid_counts:
                pids = item.get("pids") or []
                pid_text = ",".join(str(pid) for pid in pids) or "未出现"
                click.echo(
                    f"{indent}- {item.get('process_name') or '-'}："
                    f"PID={pid_text}，数量={item.get('count', len(pids))}"
                )
        else:
            click.echo(f"{indent}- 无白名单进程 PID 观测")
        reason = decision.get("reason_zh") or ""
        if reason:
            click.echo(f"{indent}结论：{reason}")
        if decision.get("decision") == "merged":
            click.echo(f"{indent}最终决策：聚合为同一个生命周期")
        else:
            blocking = decision.get("blocking_reason") or "-"
            click.echo(f"{indent}最终决策：保留切分")
            click.echo(f"{indent}保留原因：{_v3_blocking_reason_label(blocking)}")
        _print_v3_decision_journal_evidence(
            decision.get("journal_evidence") or [],
            indent=indent,
        )


def _print_v3_decision_journal_evidence(evidence_items: list[dict], *, indent: str) -> None:
    if not evidence_items:
        click.echo(f"{indent}No 回绕证据：未发现")
        return
    for evidence in evidence_items:
        old_source = evidence.get("old_source") or "-"
        new_source = evidence.get("new_source") or "-"
        click.echo(
            f"{indent}No 回绕证据："
            f"old_seq={evidence.get('old_sequence')} new_seq={evidence.get('new_sequence')} "
            f"old_source={old_source} new_source={new_source} "
            f"old={_format_issue_time(evidence.get('old_observed_time'))} "
            f"new={_format_issue_time(evidence.get('new_observed_time'))}"
        )
        explanation = evidence.get("explanation_zh")
        if explanation:
            click.echo(f"{indent}证据说明：{explanation}")


def _print_lifecycle_split_v3_final_lifecycles(lifecycles: list[dict], *, indent: str) -> None:
    click.echo(f"{indent}[最终生命周期]")
    if not lifecycles:
        click.echo(f"{indent}没有最终生命周期。")
        return
    for lifecycle in sorted(lifecycles, key=_v3_segment_sort_key):
        label = f"L{int(lifecycle.get('lifecycle_index') or 0) + 1}"
        candidates = _format_candidate_indices(lifecycle.get("candidate_indices") or [])
        reliable = str(lifecycle.get("lifecycle_reliable", True)).lower()
        parent = lifecycle.get("parent_lifecycle_id") or ""
        parent_text = f" parent={parent}" if parent else ""
        click.echo(
            f"{indent}{label} {_format_v3_scope(lifecycle)}{parent_text} = {candidates} "
            f"{_format_issue_time(lifecycle.get('start_time'))}.."
            f"{_format_issue_time(lifecycle.get('end_time'))} "
            f"reliable={reliable}"
        )


def _v3_blocking_reason_label(reason: str) -> str:
    if reason == "reliable_pid_conflict":
        return "合并后可靠边界进程会出现多个 PID，当前证据更支持这里是重启边界。"
    if reason == "journal_wrap":
        return "可靠进程 No 回绕前日志在前候选段、回绕后日志在后候选段；比较 No 时忽略 PID 和 source，这条边界有可靠反证支撑。"
    return reason or "-"


def _v3_main_reason(decisions: list[dict], issues: list[dict]) -> str:
    error_count = sum(
        1 for issue in issues if _severity_key(_issue_get(issue, "severity")) == "ERROR"
    )
    if error_count:
        return f"发现 {error_count} 个 ERROR；V3 不自动补切，只标记问题并保留证据。"
    kept_reasons = [
        item.get("blocking_reason") for item in decisions if item.get("decision") == "kept_split"
    ]
    if kept_reasons:
        labels = [_v3_blocking_reason_label(str(reason)) for reason in sorted(set(kept_reasons))]
        return "；".join(labels)
    if any(item.get("decision") == "merged" for item in decisions):
        return "虽然存在 >=30 秒静默候选边界，但没有可靠边界进程 PID 冲突或 No 回绕证据，因此聚合。"
    return ""


def _seconds_between(left, right) -> float:
    left_time = _parse_time_value(left)
    right_time = _parse_time_value(right)
    if left_time is None or right_time is None:
        return 0
    return (right_time - left_time).total_seconds()


def _format_seconds(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _format_candidate_indices(indices: list) -> str:
    if not indices:
        return "#?"
    return "/".join(f"#{int(index) + 1}" for index in indices)


def _v3_segment_sort_key(item: dict) -> tuple:
    return (
        item.get("scope") or "",
        item.get("slot") or "",
        item.get("cpu_id") or "",
        item.get("parent_lifecycle_id") or "",
        item.get("candidate_index", item.get("lifecycle_index", 0)) or 0,
    )


def _format_v3_scope(item: dict) -> str:
    slot = item.get("slot") or "-"
    scope = item.get("scope") or "-"
    cpu_id = item.get("cpu_id")
    if scope == "cpu" and cpu_id:
        return f"cpu_{cpu_id} slot_{slot}"
    if scope == "board":
        return f"board slot_{slot}"
    return f"{scope} slot_{slot}"


def _v3_candidate_boundary_count(candidates: list[dict]) -> int:
    count = 0
    ordered = sorted(candidates, key=_v3_segment_sort_key)
    for previous, current in zip(ordered, ordered[1:]):
        if (
            previous.get("scope") == current.get("scope")
            and previous.get("slot") == current.get("slot")
            and previous.get("cpu_id") == current.get("cpu_id")
            and previous.get("parent_lifecycle_id") == current.get("parent_lifecycle_id")
        ):
            count += 1
    return count


def _mech_lifecycle_dfx_detail(lifecycle_dfx: str) -> str:
    if lifecycle_dfx == "off":
        return "off"
    if lifecycle_dfx in {"decisions", "full"}:
        return lifecycle_dfx
    return "summary"


def _print_lifecycle_group_dfx(group: dict, detail: str = "summary") -> None:
    if detail == "off":
        return
    reliable = str(group.get("lifecycle_reliable", True)).lower()
    click.echo(f"  lifecycle_reliable: {reliable}")
    lifecycle_result = group.get("lifecycle_split_result")
    if not isinstance(lifecycle_result, dict) or not _has_lifecycle_split_payload(lifecycle_result):
        click.echo("  lifecycle_split: legacy output is unsupported or missing")
        return
    _print_lifecycle_split_result(lifecycle_result, detail=detail)


def _format_scope(item, *, scope_field: str = "scope") -> str:
    scope = _issue_get(item, scope_field) or _issue_get(item, "scope") or "-"
    cpu_id = _issue_get(item, "cpu_id")
    if scope == "cpu" and cpu_id:
        return f"cpu_{cpu_id}"
    return str(scope)
