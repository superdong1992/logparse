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
  python cli.py mech-logs <task_id> -s <slot_id> -c <cycle_dir> -p <proc> [--cpu <cpu_id> --cpu-cycle <cpu_cycle_dir>]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import click

from backend.config_validation import validate_config
from backend.parsing.mech_journal_pattern import JournalPatternMatcher
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
) -> None:
    """打印解析结果摘要 + 落盘 result.json。"""
    click.echo(f"\n=== 解析结果 ===")
    click.echo(f"压缩包: {result.package_name}")
    click.echo(f"诊断日志槽位数: {len(result.diagnostic_slots)}")
    click.echo(f"私有日志槽位数: {len(result.private_slots)}")

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
@click.option("--output", "-o", default="./output", help="输出目录")
@click.option("--verbose", "-v", is_flag=True, help="详细输出")
@click.option("--product", "-p", default="default", help="产品名（default/compact）")
@click.option("--debug-expand-gz", is_flag=True, default=False, help="调试用：解析过程中将 .gz 文件就地展开")
@click.pass_context
def parse(ctx, package_path, output, verbose, product, debug_expand_gz):
    """解析日志压缩包。"""
    if verbose:
        import logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
            stream=sys.stdout,
            force=True,
        )

    config_path = ctx.obj["config_path"]
    source = Path(package_path)
    output_dir = Path(output)

    raw_config = {}
    if Path(config_path).exists():
        import yaml
        raw_config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    if debug_expand_gz:
        raw_config.setdefault("pipeline", {})
        raw_config["pipeline"]["debug_expand_gz"] = True
    pipeline = Pipeline(raw_config)
    result = pipeline.run(source, output_dir, product=product, verbose=verbose)
    _print_parse_errors(result)

    # 输出摘要
    result_json_mode = raw_config.get("pipeline", {}).get("result_json_mode", "compact")
    _print_summary(result, output_dir, result_json_mode=result_json_mode)


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


def _format_issue_time(value) -> str:
    return value or "-"


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


def _boundary_issue_counts(issues) -> dict[str, int]:
    counts = _empty_issue_counts()
    for issue in issues:
        counts[_severity_key(_issue_get(issue, "severity"))] += 1
    return counts


def _iter_result_boundary_issues(result: ParseResult):
    for mech_result in result.mech_results:
        for slot in mech_result.slots:
            for issue in slot.boundary_issues:
                yield issue


def _is_lifecycle_dfx_error(message: str) -> bool:
    return (
        "unsafe cycle split" in message
        or "cycle split diagnostic:" in message
        or "forced protected pid split:" in message
    )


def _lifecycle_error_severity(message: str) -> str:
    if (
        "unsafe cycle split kept" in message
        or "restart_boundary_overlap" in message
        or "same_pid_kept" in message
    ):
        return "ERROR"
    if "scoped_cpu_split" in message or "suspect_over_split" in message:
        return "INFO"
    return "WARNING"


def _print_parse_errors(result: ParseResult) -> None:
    raw_errors = list(result.errors)
    lifecycle_errors = [err for err in raw_errors if _is_lifecycle_dfx_error(err)]
    normal_errors = [err for err in raw_errors if not _is_lifecycle_dfx_error(err)]

    issues = list(_iter_result_boundary_issues(result))
    counts = _boundary_issue_counts(issues)
    if not issues:
        for err in lifecycle_errors:
            counts[_lifecycle_error_severity(err)] += 1

    if sum(counts.values()):
        click.echo(f"\n⚠ 生命周期切分诊断: {_format_issue_counts(counts)}")
        click.echo(
            "  定位: "
            f"python cli.py mech-lifecycles {result.task_id} "
            "-s <slot_id> -m <module_name> --show-boundaries"
        )

    if normal_errors:
        click.echo(f"\n⚠ {len(normal_errors)} 个错误:")
        for err in normal_errors:
            click.echo(f"  - {err}")


def _print_boundary_evidence(evidence: dict, indent: str = "    ") -> None:
    if not evidence:
        return
    source = evidence.get("source") or "-"
    source_file = evidence.get("source_file") or "-"
    sequence = evidence.get("sequence", 0)
    raw = evidence.get("raw_excerpt") or ""
    click.echo(f"{indent}evidence {source}|{source_file} seq={sequence} raw={raw}")


def _print_boundary_log(label: str, log: dict, indent: str = "    ") -> None:
    if not log:
        return
    source = log.get("source") or "-"
    source_file = log.get("source_file") or "-"
    sequence = log.get("sequence", 0)
    raw = log.get("raw_excerpt") or ""
    click.echo(f"{indent}{label} {source}|{source_file} seq={sequence} raw={raw}")


def _proc_pid(process_name: str, pid: str) -> str:
    return f"{process_name}-{pid}" if pid else process_name


def _cpu_scope(cpu_id: str | None) -> str:
    return cpu_id or "board"


def _first_hint(commands: list[str], task_id: str) -> str | None:
    if not commands:
        return None
    command = next((cmd for cmd in commands if "mech-logs" in cmd), commands[0])
    return command.replace("<task_id>", task_id)


def _print_issue_header(issue: dict) -> None:
    severity = (issue.get("severity") or "warning").upper()
    kind = issue.get("kind") or "-"
    action = issue.get("action") or ""
    reason = issue.get("reason") or "-"
    scope = issue.get("scope") or "board"
    split = _format_issue_time(issue.get("split_time"))
    adjusted = _format_issue_time(issue.get("adjusted_time"))
    line = f"  [{severity}] {kind}"
    if action:
        line += f" action={action}"
    line += f" reason={reason} scope={scope} split={split}"
    if adjusted != "-":
        line += f" adjusted={adjusted}"
    click.echo(line)


def _boundary_endpoint(boundaries: list[dict], key: str, value: str | None) -> dict | None:
    if value:
        for boundary in boundaries:
            if boundary.get(key) == value:
                return boundary
    return None


def _print_restart_overlap_compact(issue: dict) -> None:
    old_end = _format_issue_time(issue.get("old_pid_end"))
    new_start = _format_issue_time(issue.get("new_pid_start"))
    click.echo(f"    overlap new_start={new_start} <= old_end={old_end}")

    boundaries = issue.get("protected_boundaries") or []
    old_boundary = _boundary_endpoint(boundaries, "old_end", issue.get("old_pid_end"))
    new_boundary = _boundary_endpoint(boundaries, "new_start", issue.get("new_pid_start"))

    if old_boundary and new_boundary and (
        old_boundary.get("process_name") == new_boundary.get("process_name")
        and (old_boundary.get("cpu_id") or "") == (new_boundary.get("cpu_id") or "")
    ):
        proc = old_boundary.get("process_name") or "-"
        old_pid = ",".join(old_boundary.get("old_pids") or [])
        new_pid = old_boundary.get("new_pid") or "-"
        cpu = _cpu_scope(old_boundary.get("cpu_id"))
        role = old_boundary.get("role") or "-"
        old_raw = (old_boundary.get("old_log") or {}).get("raw_excerpt") or ""
        new_raw = (new_boundary.get("new_log") or {}).get("raw_excerpt") or ""
        click.echo(
            f"    boundary {proc}@{cpu} role={role} {old_pid}->{new_pid} "
            f"old_end={old_end} new_start={new_start} "
            f"old_raw={old_raw} new_raw={new_raw}"
        )
        return

    if old_boundary:
        proc = old_boundary.get("process_name") or "-"
        pid = ",".join(old_boundary.get("old_pids") or [])
        cpu = _cpu_scope(old_boundary.get("cpu_id"))
        role = old_boundary.get("role") or "-"
        raw = (old_boundary.get("old_log") or {}).get("raw_excerpt") or ""
        click.echo(f"    old-side {_proc_pid(proc, pid)}@{cpu} role={role} old_end={old_end} raw={raw}")
    if new_boundary:
        proc = new_boundary.get("process_name") or "-"
        pid = new_boundary.get("new_pid") or ""
        cpu = _cpu_scope(new_boundary.get("cpu_id"))
        role = new_boundary.get("role") or "-"
        raw = (new_boundary.get("new_log") or {}).get("raw_excerpt") or ""
        click.echo(f"    new-side {_proc_pid(proc, pid)}@{cpu} role={role} new_start={new_start} raw={raw}")


def _print_conflict_compact(issue: dict) -> None:
    conflict = (issue.get("conflicts") or [{}])[0]
    proc = conflict.get("process_name") or "-"
    pid = conflict.get("pid") or ""
    cpu = _cpu_scope(conflict.get("cpu_id"))
    before = _format_issue_time(conflict.get("before_time"))
    after = _format_issue_time(conflict.get("after_time"))
    click.echo(f"    conflict {_proc_pid(proc, pid)}@{cpu} before={before} after={after}")
    _print_boundary_log("before", conflict.get("before_log") or {})
    _print_boundary_log("after", conflict.get("after_log") or {})
    blocker = (issue.get("protected_boundaries") or [{}])[0]
    if blocker:
        bproc = blocker.get("process_name") or "-"
        bcpu = _cpu_scope(blocker.get("cpu_id"))
        role = blocker.get("role") or "-"
        click.echo(
            f"    blocker {bproc}@{bcpu} role={role} "
            f"old_end={_format_issue_time(blocker.get('old_end'))} "
            f"new_start={_format_issue_time(blocker.get('new_start'))}"
        )


def _print_protected_forced_split_compact(issue: dict) -> None:
    boundary = (issue.get("protected_boundaries") or [{}])[0]
    if not boundary:
        return
    proc = boundary.get("process_name") or "-"
    old_pid = ",".join(boundary.get("old_pids") or [])
    new_pid = boundary.get("new_pid") or ""
    cpu = _cpu_scope(boundary.get("cpu_id"))
    role = boundary.get("role") or "-"
    click.echo(
        f"    protected {_proc_pid(proc, old_pid)}->{new_pid}@{cpu} role={role} "
        f"old_end={_format_issue_time(boundary.get('old_end'))} "
        f"new_start={_format_issue_time(boundary.get('new_start'))}"
    )
    _print_boundary_log("old", boundary.get("old_log") or {})
    _print_boundary_log("new", boundary.get("new_log") or {})


def _print_pid_bounce_compact(issue: dict) -> None:
    evidence = issue.get("evidence") or []
    pids = [item.get("pid") or "-" for item in evidence]
    if pids:
        click.echo(f"    pid-bounce {' -> '.join(pids)}")
    for item in evidence[:3]:
        _print_boundary_log(item.get("role") or "bounce", item)


def _print_boundary_issue_compact(issue: dict, task_id: str) -> None:
    if _severity_key(issue.get("severity")) == "INFO":
        return
    _print_issue_header(issue)
    kind = issue.get("kind") or ""
    if kind == "restart_boundary_overlap":
        _print_restart_overlap_compact(issue)
    elif kind in {"unsafe_cycle_split", "same_pid_kept", "same_pid_adjusted", "same_pid_adjusted_backward", "same_pid_dropped"}:
        _print_conflict_compact(issue)
    elif kind == "protected_forced_split":
        _print_protected_forced_split_compact(issue)
    elif kind == "suspect_pid_bounce":
        _print_pid_bounce_compact(issue)
    else:
        for item in (issue.get("evidence") or [])[:2]:
            _print_boundary_log(item.get("role") or "context", item)

    hint = _first_hint(issue.get("suggested_commands") or [], task_id)
    if hint:
        click.echo(f"    hint {hint}")


def _print_boundary_issue_full(issue: dict, task_id: str) -> None:
    _print_issue_header(issue)

    for conflict in issue.get("conflicts", []):
        proc = conflict.get("process_name") or "-"
        pid = conflict.get("pid") or ""
        cpu = _cpu_scope(conflict.get("cpu_id"))
        before = _format_issue_time(conflict.get("before_time"))
        after = _format_issue_time(conflict.get("after_time"))
        click.echo(f"    conflict {_proc_pid(proc, pid)}@{cpu} before={before} after={after}")
        _print_boundary_evidence(conflict.get("before_log") or {}, indent="    ")
        _print_boundary_evidence(conflict.get("after_log") or {}, indent="    ")

    for boundary in issue.get("protected_boundaries", []):
        proc = boundary.get("process_name") or "-"
        cpu = _cpu_scope(boundary.get("cpu_id"))
        role = boundary.get("role") or "-"
        old_pids = ",".join(boundary.get("old_pids") or [])
        new_pid = boundary.get("new_pid") or "-"
        click.echo(
            f"    protected {proc}@{cpu} role={role} "
            f"old_pids={old_pids} new_pid={new_pid} "
            f"old_end={_format_issue_time(boundary.get('old_end'))} "
            f"new_start={_format_issue_time(boundary.get('new_start'))}"
        )

    for evidence in issue.get("evidence", []):
        _print_boundary_evidence(evidence, indent="    ")

    for command in issue.get("suggested_commands", []):
        click.echo(f"    hint {command.replace('<task_id>', task_id)}")


def _print_boundary_issues(group: dict, task_id: str, detail: str = "compact") -> None:
    reliable = str(group.get("lifecycle_reliable", True)).lower()
    click.echo(f"  生命周期可靠性: {reliable}")
    issues = group.get("boundary_issues") or []
    if not issues:
        return

    counts = _boundary_issue_counts(issues)
    click.echo(f"  生命周期切分诊断: {_format_issue_counts(counts)}")

    if detail == "full":
        for issue in issues:
            _print_boundary_issue_full(issue, task_id)
        return

    for issue in issues:
        _print_boundary_issue_compact(issue, task_id)
    if counts["INFO"]:
        click.echo(f"  INFO 诊断 {counts['INFO']} 个，使用 --boundary-detail full 查看")


@cli.command()
@click.argument("task_id")
@click.option("--slot", "-s", required=True, help="槽位 ID")
@click.option("--module", "-m", "module_name", default=None, help="机制模块名，默认展示全部模块")
@click.option("--show-boundaries", is_flag=True, help="显示生命周期切分诊断")
@click.option(
    "--boundary-detail",
    type=click.Choice(["compact", "full"]),
    default="compact",
    show_default=True,
    help="生命周期切分诊断展示详细度",
)
@click.option("--output", "-o", default="./output", help="输出目录")
def mech_lifecycles(task_id, slot, module_name, show_boundaries, boundary_detail, output):
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
            _print_boundary_issues(group, task_id, detail=boundary_detail)
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
@click.option("--slot", "-s", required=True, help="槽位 ID")
@click.option("--cycle", "-c", required=True, help="周期目录名")
@click.option("--proc", "-p", required=True, help="进程名-pid")
@click.option("--module", "-m", "module_name", default=None, help="机制模块名，默认取第一个")
@click.option("--cpu", "cpu_id", default=None, help="CPU ID")
@click.option("--cpu-cycle", default=None, help="CPU cycle directory")
@click.option("--output", "-o", default="./output", help="输出目录")
def mech_logs(task_id, slot, cycle, proc, module_name, cpu_id, cpu_cycle, output):
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
        click.echo(f"✓ 匹配 {match.pattern_name}")
        click.echo(f"  进程名: {match.raw_name}")
        if match.raw_pid:
            click.echo(f"  pid: {match.raw_pid}")
        if match.sequence:
            click.echo(f"  序号: {match.sequence}")
        else:
            click.echo("  序号: 无")
        click.echo(f"  Context: {match.context}")
        keyword = jnl.get("identifying_keyword", "")
        if keyword:
            click.echo(f"  识别关键字 '{keyword}': {'✓' if keyword in line.lower() else '✗ (Stage1 会被过滤)'}")
        mod_name = mod_cfg.get("module_name", "")
        click.echo(f"  模块名预过滤: {mod_name} {'✓' if mod_name in line else '✗ (Stage1 会被过滤)'}")


if __name__ == "__main__":
    cli()
