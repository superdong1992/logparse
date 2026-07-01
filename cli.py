#!/usr/bin/env python3
"""
日志解析维护工具 - CLI 接口

用法:
  python cli.py parse <package_path> [--config config.yaml] [--output ./output] [--verbose]
  python cli.py check-config [--config config.yaml]
  python cli.py test-pattern --module module1 --type diag "一行日志"
  python cli.py info <task_id>
  python cli.py list-slots <task_id>
  python cli.py query-diag <task_id> --slot <slot_id>
  python cli.py mech-slots <task_id>
  python cli.py mech-lifecycles <task_id> -s <slot_id>
  python cli.py mech-target-logs <task_id> --problem-time <ISO_TIME> --module <module> --slot <slot_id> --process-name <name> [--pid <pid>]
  python cli.py mech-logs <task_id> -s <slot_id> -c <cycle_dir> -p <proc> [--pid <pid>] [--cpu <cpu_id> --cpu-cycle <cpu_cycle_dir>]
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import click

from backend.config_validation import validate_config
from backend.dfx import build_dfx_output
from backend.parsing.mech_journal_pattern import (
    JournalPatternMatcher,
    passes_line_pattern2_required_substrings,
)
from backend.query import ResultQueryService
from backend.result_serializer import result_to_dict
from backend.utils import glob_to_regex
from backend.models import ParseResult
from backend.pipeline import Pipeline


def _mechanism_config(module_entry: dict) -> dict:
    return module_entry.get("config", module_entry)


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
    result_json_mode: str = "compact",
    detailed: bool = True,
) -> None:
    """打印解析结果摘要 + 落盘 result.json。"""
    click.echo(f"\n=== 解析结果 ===")
    click.echo(f"压缩包: {result.package_name}")
    click.echo(f"诊断日志槽位数: {len(result.diagnostic_slots)}")
    click.echo(f"私有日志槽位数: {len(result.private_slots)}")

    if not detailed:
        json_output = output_dir / result.task_id / "result.json"
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(
            json.dumps(
                result_to_dict(result, result_json_mode),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        click.echo(f"机制模块结果数: {len(result.mech_results)}")
        click.echo(f"\n完整结果: {json_output}")
        return

    for slot in result.diagnostic_slots:
        diag_count = len(slot.diagnostic_logs)
        click.echo(f"  {slot.name} [角色: {slot.role.value}]"
                   f" {diag_count} 个诊断日志")
        if slot.active_periods:
            click.echo(f"    主控时段 ({len(slot.active_periods)} 段):")
            for period in slot.active_periods:
                dur = period.duration
                click.echo(f"      {period.start.isoformat()} ~ {period.end.isoformat()}"
                           f" (持续 {dur})")
        for log in slot.diagnostic_logs:
            dump = log.dump_time.strftime("%Y-%m-%d %H:%M:%S") if log.dump_time else "无"
            ts_count = f", 时间戳: {len(log.content_timestamps)} 条" if log.content_timestamps else ""
            click.echo(f"    └── {log.name} ({log.size_bytes} bytes) [转储时间: {dump}{ts_count}]")

    if result.private_slots:
        click.echo(f"\n--- 私有日志 (varlog) ---")
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
                click.echo(f"  slot_{s.slot_id}: {len(s.board_cycles)} 个周期, {total_procs} 进程, {total_logs} 条日志")
                for c in s.board_cycles:
                    click.echo(f"    {c.dir_name}")
                    for p in c.processes:
                        missing = f" 丢号:{p.missing_sequences}" if p.missing_sequences else ""
                        click.echo(f"      {p.process_name}-{p.pid}: {p.total_count} 条{missing}")

                    for cpu_cycle in c.cpu_cycles:
                        click.echo(f"      cpu_{cpu_cycle.cpu_id}/{cpu_cycle.dir_name}")
                        for p in cpu_cycle.processes:
                            missing = f" 丢号:{p.missing_sequences}" if p.missing_sequences else ""
                            click.echo(f"        {p.process_name}-{p.pid}: {p.total_count} 条{missing}")

    json_output = output_dir / result.task_id / "result.json"
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(
            result_to_dict(result, result_json_mode),
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    click.echo(f"\n完整结果: {json_output}")



@click.group()
@click.option("--config", "-c", default="config.yaml", help="配置文件路径")
@click.pass_context
def cli(ctx, config):
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
@cli.command()
@click.argument("package_path", type=click.Path(exists=True))
@click.option("--config", "-c", default=None, help="配置文件路径")
@click.option("--output", "-o", default="./output", help="输出目录")
@click.option("--verbose", "-v", is_flag=True, help="通用详细输出；生命周期 DFX 请用 --lifecycle-dfx")
@click.option("--product", "-p", default="default", help="产品名（default/compact）")
@click.option("--debug-expand-gz", is_flag=True, default=False, help="强制在解析过程中将 .gz 日志就地展开")
@click.option("--profile", is_flag=True, default=False, help="生成 performance.json 并打印性能摘要")
@click.option(
    "--lifecycle-dfx",
    type=click.Choice(["off", "errors", "summary", "decisions", "full"]),
    default="errors",
    show_default=True,
    help="生命周期聚合/切分中文说明输出级别",
)
@click.pass_context
def parse(ctx, package_path, config, output, verbose, product, debug_expand_gz, profile, lifecycle_dfx):
    """解析日志压缩包。"""
    if verbose:
        import logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
            stream=sys.stdout,
            force=True,
        )

    config_path = config or ctx.obj["config_path"]
    source = Path(package_path)
    output_dir = Path(output)

    raw_config = {}
    if Path(config_path).exists():
        import yaml
        raw_config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    else:
        click.echo(f"✗ 配置文件不存在: {config_path}", err=True)
        raise click.exceptions.Exit(1)
    if debug_expand_gz:
        raw_config.setdefault("pipeline", {})
        raw_config["pipeline"]["debug_expand_gz"] = True
    config_errors = validate_config(raw_config)
    if config_errors:
        click.echo(f"✗ 配置检查失败: {len(config_errors)} 个错误", err=True)
        for error in config_errors:
            click.echo(f"  - {error}", err=True)
        raise click.exceptions.Exit(1)
    pipeline = Pipeline(raw_config)
    run_kwargs = {"product": product, "verbose": verbose}
    if profile:
        run_kwargs["profile"] = True
    result = pipeline.run(source, output_dir, **run_kwargs)
    _print_parse_errors(result, verbose=verbose, lifecycle_dfx=lifecycle_dfx)

    # 输出摘要
    result_json_mode = raw_config.get("pipeline", {}).get("result_json_mode", "compact")
    summary_t0 = time.perf_counter()
    _print_summary(result, output_dir, result_json_mode=result_json_mode, detailed=not profile)
    if profile:
        if hasattr(pipeline.performance, "record_stage"):
            pipeline.performance.record_stage(
                "cli.result_json",
                elapsed_seconds=time.perf_counter() - summary_t0,
                result_json_mode=result_json_mode,
            )
        if hasattr(pipeline.performance, "write"):
            pipeline.performance.write(output_dir / result.task_id)
        click.echo("\n=== 性能摘要 ===")
        for line in pipeline.performance.summary_lines():
            click.echo(line)


@cli.command()
@click.argument("task_id")
@click.option("--output", "-o", default="./output", help="输出目录")
def info(task_id, output):
    """查看任务的元数据。"""
    svc = ResultQueryService(Path(output))
    metadata = svc.read_metadata(task_id)
    if not metadata:
        click.echo(f"任务 {task_id} 的元数据不存在", err=True)
        sys.exit(1)
    click.echo(json.dumps(metadata, ensure_ascii=False, indent=2))


@cli.command()
@click.argument("task_id")
@click.option("--output", "-o", default="./output", help="输出目录")
def list_slots(task_id, output):
    """列出任务中识别到的所有槽位。"""
    svc = ResultQueryService(Path(output))
    slots = svc.list_slots(task_id)
    if not slots:
        metadata = svc.read_metadata(task_id)
        if not metadata:
            click.echo(f"任务 {task_id} 的元数据不存在", err=True)
            sys.exit(1)
    for slot in slots:
        diag_count = len(slot.get("diagnostic_logs", []))
        periods = slot.get("active_periods", [])
        period_str = ""
        if periods:
            period_str = f", 主控时段: {len(periods)} 段"
        click.echo(f"slot_{slot['slot_id']} [{slot['role']}] 诊断日志: {diag_count}{period_str}")


@cli.command()
@click.argument("task_id")
@click.option("--slot", "-s", required=True, help="槽位 ID")
@click.option("--output", "-o", default="./output", help="输出目录")
def query_diag(task_id, slot, output):
    """查询特定槽位的诊断日志列表。"""
    svc = ResultQueryService(Path(output))
    result = svc.query_diag(task_id, slot)
    if result is None:
        metadata = svc.read_metadata(task_id)
        if not metadata:
            click.echo(f"任务 {task_id} 的元数据不存在", err=True)
            sys.exit(1)
        click.echo(f"未找到 slot_{slot}", err=True)
        return
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@cli.command()
@click.argument("task_id")
@click.option("--module", "-m", "module_name", default=None, help="机制模块名，默认展示全部模块")
@click.option("--output", "-o", default="./output", help="输出目录")
def mech_slots(task_id, module_name, output):
    """列出机制模块各 slot 概况。"""
    svc = ResultQueryService(Path(output))
    result_data = svc.read_result(task_id)
    if not result_data:
        metadata = svc.read_metadata(task_id)
        if not metadata:
            click.echo(f"任务 {task_id} 的元数据不存在", err=True)
            sys.exit(1)
        click.echo("result.json 不存在", err=True)
        sys.exit(1)
    slots = svc.mech_slots(task_id, module_name=module_name)
    if not slots:
        click.echo("无机制模块解析结果")
        return
    for s in slots:
        totals = [_cycle_process_total_dict(c) for c in s["board_cycles"]]
        total_procs = sum(proc_count for proc_count, _log_count in totals)
        total_logs = sum(log_count for _proc_count, log_count in totals)
        mod = s.get("_module_name", "")
        click.echo(
            f"[{mod}] slot_{s['slot_id']}: "
            f"{len(s['board_cycles'])} 周期, {total_procs} 进程, {total_logs} 条日志"
        )


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
        "  lifecycle_split: legacy output is unsupported; "
        f"expected interval_v3, got {algorithm}"
    )


def _print_lifecycle_issue_compact(issue, *, indent: str = "    ") -> None:
    issue_type = _issue_get(issue, "type") or "-"
    severity = _severity_key(_issue_get(issue, "severity"))
    scope = _format_v2_scope(issue)
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
        scope = _format_v2_scope(evidence)
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
    error_count = sum(1 for issue in issues if _severity_key(_issue_get(issue, "severity")) == "ERROR")
    if error_count:
        return f"发现 {error_count} 个 ERROR；V3 不自动补切，只标记问题并保留证据。"
    kept_reasons = [item.get("blocking_reason") for item in decisions if item.get("decision") == "kept_split"]
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


def _format_v2_scope(item, *, scope_field: str = "scope") -> str:
    scope = _issue_get(item, scope_field) or _issue_get(item, "scope") or "-"
    cpu_id = _issue_get(item, "cpu_id")
    if scope == "cpu" and cpu_id:
        return f"cpu_{cpu_id}"
    return str(scope)


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


@cli.command()
@click.argument("task_id")
@click.option("--slot", "-s", required=True, help="槽位 ID")
@click.option("--module", "-m", "module_name", default=None, help="机制模块名，默认展示全部模块")
@click.option("--show-boundaries", is_flag=True, help="显示生命周期切分诊断")
@click.option(
    "--lifecycle-dfx",
    type=click.Choice(["off", "errors", "summary", "decisions", "full"]),
    default="summary",
    show_default=True,
    help="生命周期聚合/切分中文说明输出级别",
)
@click.option("--output", "-o", default="./output", help="输出目录")
def mech_lifecycles(
    task_id,
    slot,
    module_name,
    show_boundaries,
    lifecycle_dfx,
    output,
):
    """列出某 slot 的机制模块周期和进程。"""
    svc = ResultQueryService(Path(output))
    result_data = svc.read_result(task_id)
    if not result_data:
        click.echo("result.json 不存在", err=True)
        sys.exit(1)
    groups = svc.mech_lifecycles(task_id, slot, module_name=module_name)
    if not groups:
        click.echo(f"未找到 slot_{slot}", err=True)
        return
    for group in groups:
        click.echo(f"[{group['module_name']}] slot_{slot}")
        if show_boundaries:
            detail = _mech_lifecycle_dfx_detail(lifecycle_dfx)
            _print_lifecycle_group_dfx(group, detail=detail)
        for c in group["board_cycles"]:
            click.echo(f"  {c['dir_name']}")
            for p in c["processes"]:
                missing = f" 丢号:{p['missing_sequences']}" if p.get("missing_sequences") else ""
                click.echo(f"    {p['process_name']}-{p['pid']}: {p['total_count']} 条{missing}")
            for cpu_cycle in c.get("cpu_cycles", []):
                click.echo(f"    cpu_{cpu_cycle['cpu_id']}/{cpu_cycle['dir_name']}")
                for p in cpu_cycle.get("processes", []):
                    missing = f" 丢号:{p['missing_sequences']}" if p.get("missing_sequences") else ""
                    click.echo(f"      {p['process_name']}-{p['pid']}: {p['total_count']} 条{missing}")


@cli.command()
@click.argument("task_id")
@click.option("--problem-time", required=True, help="问题发生时间，ISO 格式")
@click.option("--module", "module", required=True, help="机制模块 key 或输出模块名")
@click.option("--slot", required=True, help="槽位 ID，例如 1 或 slot_1")
@click.option("--process-name", required=True, help="目标进程名")
@click.option("--pid", default=None, help="目标 PID，提供时严格匹配")
@click.option("--label", default=None, help="目标标签，例如 client/server")
@click.option("--output", "-o", default="./output", help="输出目录")
@click.option("--explain", is_flag=True, default=False, help="附加 deterministic target 选择 DFX")
def mech_target_logs(task_id, problem_time, module, slot, process_name, pid, label, output, explain):
    """按目标进程和问题时间确定性输出 target_logs JSON。"""
    svc = ResultQueryService(Path(output))
    payload = svc.resolve_target_logs(
        task_id,
        problem_time=problem_time,
        module=module,
        slot=slot,
        process_name=process_name,
        pid=pid,
        label=label,
        explain=explain,
    )
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@cli.command("dfx-output")
@click.argument("output_task_dir")
@click.option("--deep", is_flag=True, default=False, help="局域网内读取目标日志短窗口")
@click.option("--targets-json", default=None, help="目标 anchors JSON；可为列表或包含 targets/problem_time 的对象")
@click.option("--problem-time", default=None, help="target 级 DFX 的问题时间；优先级高于 targets-json 内 problem_time")
@click.option("--summary-path", default=None, help="额外写出一份单行摘要到指定路径")
def dfx_output(output_task_dir, deep, targets_json, problem_time, summary_path):
    """对一个 output/{task_id} 目录生成 deterministic DFX。"""
    try:
        report = build_dfx_output(
            Path(output_task_dir),
            targets_json=targets_json,
            problem_time=problem_time,
            deep=deep,
            summary_path=Path(summary_path) if summary_path else None,
        )
    except Exception as exc:  # noqa: BLE001 - CLI must expose deterministic failure.
        click.echo(f"dfx-output failed: {exc}", err=True)
        sys.exit(1)

    click.echo(report["summary"])
    click.echo(f"dfx_report: {Path(output_task_dir) / 'dfx_report.json'}")


@cli.command()
@click.argument("task_id")
@click.option("--slot", "-s", required=True, help="槽位 ID")
@click.option("--cycle", "-c", required=True, help="周期目录名")
@click.option("--proc", "-p", required=True, help="进程名；PID 请使用 --pid")
@click.option("--module", "-m", "module_name", default=None, help="机制模块名，默认取第一个")
@click.option("--pid", default=None, help="PID; when provided, --proc is treated as the process name")
@click.option("--cpu", "cpu_id", default=None, help="CPU ID")
@click.option("--cpu-cycle", default=None, help="CPU cycle directory")
@click.option("--output", "-o", default="./output", help="输出目录")
def mech_logs(task_id, slot, cycle, proc, module_name, pid, cpu_id, cpu_cycle, output):
    """查看指定进程批次的机制模块日志。"""
    svc = ResultQueryService(Path(output))
    log_file = svc.mech_log_path(
        task_id=task_id,
        slot_id=slot,
        cycle=cycle,
        proc=proc,
        module_name=module_name,
        cpu_id=cpu_id,
        cpu_cycle=cpu_cycle,
        pid=pid,
    )
    if not log_file.exists():
        click.echo(f"文件不存在: {log_file}", err=True)
        sys.exit(1)
    click.echo(log_file.read_text(encoding="utf-8", errors="replace").rstrip())


@cli.command()
@click.option("--config", "-c", default="config.yaml", help="配置文件路径")
def check_config(config):
    """检查配置文件的有效性。"""
    config_path = Path(config)
    if not config_path.exists():
        click.echo(f"✗ 配置文件不存在: {config_path}")
        sys.exit(1)

    try:
        import yaml
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        click.echo("✓ 配置加载成功")
    except Exception as e:
        click.echo(f"✗ 配置加载失败: {e}")
        sys.exit(1)

    errors = validate_config(raw)

    if errors:
        click.echo(f"\n✗ {len(errors)} 个错误:")
        for e in errors:
            click.echo(f"  - {e}")
        sys.exit(1)
    else:
        click.echo("\n✓ 配置检查通过")


@cli.command()
@click.option("--config", "-c", default="config.yaml", help="配置文件路径")
@click.option("--module", "-m", required=True, help="机制模块 key")
@click.option("--type", "-t", "log_type", type=click.Choice(["diag", "journal"]), required=True)
@click.argument("line")
def test_pattern(config, module, log_type, line):
    """用配置的正则测试一条日志行。"""
    import yaml
    config_path = Path(config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {} if config_path.exists() else {}

    # Find module config from first product that has it
    mod_cfg = None
    for prod_name, prod_cfg in raw.get("products", {}).items():
        parser_cfg = prod_cfg.get("log_parser", {}).get("config", {})
        modules = parser_cfg.get("mechanism_modules", {})
        if module in modules:
            module_entry = modules[module]
            mod_cfg = _mechanism_config(module_entry)
            break

    if mod_cfg is None:
        click.echo(f"✗ 模块 '{module}' 未配置", err=True)
        sys.exit(1)

    if log_type == "diag":
        if not mod_cfg.get("diag_pattern"):
            click.echo("✗ diag_pattern 未配置", err=True)
            sys.exit(1)
        pat = re.compile(mod_cfg["diag_pattern"])
        m = pat.search(line)
        if not m:
            click.echo("✗ 不匹配 diag_pattern")
            sys.exit(1)
        click.echo("✓ 匹配 diag_pattern")
        mod_name = mod_cfg.get("module_name", "")
        click.echo(f"  模块名预过滤: {mod_name} {'✓' if mod_name in line else '✗ (Stage1 会被过滤)'}")
        for name, value in m.groupdict().items():
            click.echo(f"  {name}: {value}")
        seq_pat = mod_cfg.get("sequence_pattern", "")
        if seq_pat:
            seq_m = re.search(seq_pat, line)
            if seq_m:
                click.echo(f"  序号: {seq_m.group(1)}")
        master_kw = mod_cfg.get("active_master_keyword", "")
        if master_kw and re.search(master_kw, line):
            click.echo(f"  ✓ 命中主控关键字: {master_kw}")

    else:  # journal
        jnl = mod_cfg.get("journal", {})
        if not jnl.get("line_pattern") and not jnl.get("line_pattern2"):
            click.echo("✗ journal.line_pattern 和 line_pattern2 均未配置", err=True)
            sys.exit(1)
        journal_re = re.compile(jnl["line_pattern"]) if jnl.get("line_pattern") else None
        journal_re2 = re.compile(jnl["line_pattern2"]) if jnl.get("line_pattern2") else None
        seq_pat = mod_cfg.get("sequence_pattern", r"No\[(\d+)\]")
        matcher = JournalPatternMatcher(journal_re, journal_re2, re.compile(seq_pat))
        match = matcher.match(line)
        if not match:
            click.echo("✗ 不匹配 journal.line_pattern 及 line_pattern2")
            sys.exit(1)
        required_substrings = jnl.get("line_pattern2_required_substrings") or []
        if not passes_line_pattern2_required_substrings(
            match.pattern_name,
            line,
            required_substrings,
        ):
            click.echo(
                "✗ line_pattern2_required_substrings 未命中: "
                f"{required_substrings}"
            )
            sys.exit(1)
        click.echo(f"✓ 匹配 {match.pattern_name}")
        click.echo(f"  进程名: {match.raw_name}")
        if match.raw_pid:
            click.echo(f"  pid: {match.raw_pid}")
        if match.sequence:
            click.echo(f"  序号: {match.sequence}")
        else:
            click.echo("  序号: 无")
        click.echo(f"  Context: {match.context}")
        if required_substrings and match.pattern_name.startswith("journal.line_pattern2"):
            click.echo(
                "  line_pattern2_required_substrings: "
                f"✓ {required_substrings}"
            )
        keyword = jnl.get("identifying_keyword", "")
        if keyword:
            click.echo(f"  识别关键字 '{keyword}': {'✓' if keyword in line.lower() else '✗ (Stage1 会被过滤)'}")
        mod_name = mod_cfg.get("module_name", "")
        click.echo(f"  模块名预过滤: {mod_name} {'✓' if mod_name in line else '✗ (Stage1 会被过滤)'}")


if __name__ == "__main__":
    cli()
