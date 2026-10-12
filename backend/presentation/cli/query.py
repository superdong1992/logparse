from __future__ import annotations

import json


import sys


from pathlib import Path

import click


from backend.extensions.products.current.query import ResultQueryService


from backend.presentation.cli.rendering import (
    _cycle_process_total_dict,
    _mech_lifecycle_dfx_detail,
    _print_lifecycle_group_dfx,
)


@click.command()
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


@click.command()
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


@click.command()
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


@click.command()
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


@click.command()
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
                    missing = (
                        f" 丢号:{p['missing_sequences']}" if p.get("missing_sequences") else ""
                    )
                    click.echo(
                        f"      {p['process_name']}-{p['pid']}: {p['total_count']} 条{missing}"
                    )


@click.command()
@click.argument("task_id")
@click.option("--problem-time", required=True, help="问题发生时间，ISO 格式")
@click.option("--module", "module", required=True, help="机制模块 key 或输出模块名")
@click.option("--slot", required=True, help="槽位 ID，例如 1 或 slot_1")
@click.option("--process-name", required=True, help="目标进程名")
@click.option("--pid", default=None, help="目标 PID，提供时严格匹配")
@click.option("--label", default=None, help="目标标签，例如 client/server")
@click.option("--output", "-o", default="./output", help="输出目录")
@click.option("--explain", is_flag=True, default=False, help="附加 deterministic target 选择 DFX")
def mech_target_logs(
    task_id, problem_time, module, slot, process_name, pid, label, output, explain
):
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


@click.command()
@click.argument("task_id")
@click.option("--slot", "-s", required=True, help="槽位 ID")
@click.option("--cycle", "-c", required=True, help="周期目录名")
@click.option("--proc", "-p", required=True, help="进程名；PID 请使用 --pid")
@click.option("--module", "-m", "module_name", default=None, help="机制模块名，默认取第一个")
@click.option(
    "--pid", default=None, help="PID; when provided, --proc is treated as the process name"
)
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
