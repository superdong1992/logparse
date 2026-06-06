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
from collections import Counter
from datetime import datetime
from pathlib import Path

import click

from backend.config_validation import validate_config
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


def _boundary_issue_counts(issues) -> dict[str, int]:
    counts = _empty_issue_counts()
    for issue in issues:
        counts[_severity_key(_issue_get(issue, "severity"))] += 1
    return counts


def _issue_kind_counts(issues) -> Counter[str]:
    return Counter(str(_issue_get(issue, "kind") or "-") for issue in issues)


def _format_kind_counts(counts: Counter[str]) -> str:
    return " ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))


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


def _iter_result_boundary_issues(result: ParseResult):
    for mech_result in result.mech_results:
        for slot in mech_result.slots:
            for issue in slot.boundary_issues:
                yield issue


def _iter_result_lifecycle_results(result: ParseResult):
    for mech_result in result.mech_results:
        module_name = mech_result.module_name or mech_result.module_key
        for slot in mech_result.slots:
            lifecycle_result = _as_plain(slot.lifecycle_split_result)
            if not isinstance(lifecycle_result, dict):
                continue
            yield module_name, slot.slot_id, lifecycle_result


def _iter_result_v2_results(result: ParseResult):
    for module_name, slot_id, lifecycle_result in _iter_result_lifecycle_results(result):
        if _lifecycle_algorithm(lifecycle_result) == "interval_v2":
            yield module_name, slot_id, lifecycle_result


def _iter_result_lifecycle_issues(result: ParseResult):
    for module_name, slot_id, lifecycle_result in _iter_result_lifecycle_results(result):
        for issue in lifecycle_result.get("issues") or []:
            yield module_name, slot_id, issue


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


def _lifecycle_algorithm(result: dict) -> str:
    return str(result.get("algorithm") or "interval_v2")


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
    raw_errors = list(result.errors)
    lifecycle_errors = [err for err in raw_errors if _is_lifecycle_dfx_error(err)]
    normal_errors = [err for err in raw_errors if not _is_lifecycle_dfx_error(err)]

    issues = list(_iter_result_boundary_issues(result))
    lifecycle_issues = list(_iter_result_lifecycle_issues(result))
    counts = _boundary_issue_counts(issues)
    for _module_name, _slot_id, issue in lifecycle_issues:
        counts[_severity_key(_issue_get(issue, "severity"))] += 1
    if not issues and not lifecycle_issues:
        for err in lifecycle_errors:
            counts[_lifecycle_error_severity(err)] += 1

    if lifecycle_dfx != "off" and sum(counts.values()):
        click.echo(f"\n⚠ 生命周期切分诊断: {_format_issue_counts(counts)}")
        if issues or not lifecycle_issues:
            click.echo(
                "  定位: "
                f"python cli.py mech-lifecycles {result.task_id} "
                "-s <slot_id> -m <module_name> --show-boundaries"
            )
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
                    click.echo(f"    定位: {command}")
                    printed_commands.add(command)

    if lifecycle_dfx in {"summary", "decisions", "full"}:
        lifecycle_results = list(_iter_result_lifecycle_results(result))
        if lifecycle_results:
            click.echo("\n生命周期切分 DFX:")
            for module_name, slot_id, lifecycle_result in lifecycle_results:
                click.echo(f"  module={module_name} slot={slot_id}")
                _print_lifecycle_split_result(
                    lifecycle_result,
                    detail=_lifecycle_dfx_detail(lifecycle_dfx),
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
    return _normalize_hint_command(command, task_id)


def _hint_for_boundary_endpoint(
    commands: list[str],
    boundary: dict | None,
    side: str,
    task_id: str,
) -> str | None:
    if not boundary:
        return None
    proc = boundary.get("process_name") or ""
    if not proc:
        return None
    pids = boundary.get("old_pids") or [] if side == "old" else [boundary.get("new_pid") or ""]
    for pid in pids:
        if not pid:
            continue
        for command in commands:
            if "mech-logs" in command and _command_targets_proc_pid(command, proc, pid):
                return _normalize_hint_command(command, task_id)
    return None


def _normalize_hint_command(command: str, task_id: str) -> str:
    normalized = command.replace("<task_id>", task_id)
    if "mech-logs" not in normalized or "--pid" in normalized:
        return normalized
    return re.sub(
        r"(?P<prefix>(?:^|\s)-p\s+)(?P<proc>\S+)-(?P<pid>\d+)(?P<suffix>(?=\s|$))",
        lambda match: (
            f"{match.group('prefix')}{match.group('proc')} --pid {match.group('pid')}"
            f"{match.group('suffix')}"
        ),
        normalized,
        count=1,
    )


def _command_targets_proc_pid(command: str, proc: str, pid: str) -> bool:
    proc_pattern = rf"(^|\s)-p\s+{re.escape(proc)}($|\s)"
    pid_pattern = rf"(^|\s)--pid\s+{re.escape(pid)}($|\s)"
    legacy_pattern = rf"(^|\s)-p\s+{re.escape(_proc_pid(proc, pid))}($|\s)"
    return (
        re.search(proc_pattern, command) is not None
        and re.search(pid_pattern, command) is not None
    ) or re.search(legacy_pattern, command) is not None


def _detail_fields(issue: dict) -> dict[str, str]:
    detail = str(issue.get("detail") or "")
    return {key: value for key, value in re.findall(r"(\w+)=([^\s]+)", detail)}


def _first_evidence(issue: dict, roles: set[str]) -> dict:
    for item in issue.get("evidence") or []:
        role = str(item.get("role") or "")
        if role in roles:
            return item
    return {}


def _conflict_from_evidence(issue: dict) -> dict:
    before_log = _first_evidence(issue, {"conflict_before", "context_before"})
    after_log = _first_evidence(issue, {"conflict_after", "context_after"})
    if not before_log and not after_log:
        return {}

    anchor = before_log or after_log
    return {
        "process_name": anchor.get("process_name") or issue.get("process_name") or "-",
        "pid": anchor.get("pid") or issue.get("pid") or "",
        "cpu_id": anchor.get("cpu_id") or issue.get("cpu_id") or "",
        "before_time": before_log.get("timestamp") or before_log.get("before_time"),
        "after_time": after_log.get("timestamp") or after_log.get("after_time"),
        "before_log": before_log,
        "after_log": after_log,
    }


def _complete_conflict(issue: dict, conflict: dict | None) -> dict:
    inferred = _conflict_from_evidence(issue)
    conflict = conflict or {}
    if not inferred:
        return conflict
    merged = dict(inferred)
    merged.update({key: value for key, value in conflict.items() if value not in (None, "", [])})
    if not (merged.get("before_log") or {}):
        merged["before_log"] = inferred.get("before_log") or {}
    if not (merged.get("after_log") or {}):
        merged["after_log"] = inferred.get("after_log") or {}
    return merged


def _boundary_from_evidence(issue: dict) -> dict:
    old_log = _first_evidence(issue, {"protected_old", "old"})
    new_log = _first_evidence(issue, {"protected_new", "new"})
    fields = _detail_fields(issue)
    pids = [pid for pid in fields.get("pids", "").split(">") if pid]
    if not old_log and not new_log and not pids:
        return {}

    anchor = new_log or old_log
    return {
        "process_name": anchor.get("process_name") or fields.get("proc") or issue.get("process_name") or "-",
        "cpu_id": anchor.get("cpu_id") or issue.get("cpu_id") or "",
        "role": issue.get("role") or fields.get("role") or "-",
        "old_pids": [old_log.get("pid") or (pids[0] if pids else "")],
        "old_end": old_log.get("timestamp") or issue.get("old_pid_end"),
        "new_pid": new_log.get("pid") or (pids[1] if len(pids) > 1 else ""),
        "new_start": new_log.get("timestamp") or issue.get("new_pid_start"),
        "old_log": old_log,
        "new_log": new_log,
    }


def _boundary_from_restart_evidence(issue: dict, side: str) -> dict:
    role = "protected_old" if side == "old" else "protected_new"
    key = "old_pid_end" if side == "old" else "new_pid_start"
    time_field = "old_end" if side == "old" else "new_start"
    pid_field = "old_pids" if side == "old" else "new_pid"
    log_field = "old_log" if side == "old" else "new_log"
    candidates = [
        item for item in issue.get("evidence") or []
        if item.get("role") == role and item.get("timestamp")
    ]
    if not candidates:
        return {}

    endpoint = _time_text(issue.get(key))
    endpoint_wall = _time_wall_text(issue.get(key))
    for item in candidates:
        if (
            _time_text(item.get("timestamp")) == endpoint
            or _time_wall_text(item.get("timestamp")) == endpoint_wall
        ):
            selected = item
            break
    else:
        direction = "max" if side == "old" else "min"
        selected = _boundary_extreme(
            [{"timestamp": item.get("timestamp"), "item": item} for item in candidates],
            "timestamp",
            direction,
        )["item"]

    boundary = {
        "process_name": selected.get("process_name") or "-",
        "cpu_id": selected.get("cpu_id") or "",
        "role": selected.get("role") or role,
        time_field: selected.get("timestamp"),
        log_field: selected,
    }
    if side == "old":
        boundary[pid_field] = [selected.get("pid") or ""]
    else:
        boundary[pid_field] = selected.get("pid") or ""
    return boundary


def _complete_boundary(issue: dict, boundary: dict | None) -> dict:
    inferred = _boundary_from_evidence(issue)
    boundary = boundary or {}
    if not inferred:
        return boundary
    merged = dict(inferred)
    merged.update({key: value for key, value in boundary.items() if value not in (None, "", [])})
    if not (merged.get("old_log") or {}):
        merged["old_log"] = inferred.get("old_log") or {}
    if not (merged.get("new_log") or {}):
        merged["new_log"] = inferred.get("new_log") or {}
    return merged


def _pid_bounce_from_detail(issue: dict) -> tuple[str, str, list[str]]:
    fields = _detail_fields(issue)
    pids = [pid for pid in fields.get("pids", "").split(">") if pid]
    proc = fields.get("proc") or issue.get("process_name") or "-"
    scope = str(issue.get("scope") or "")
    cpu = scope.removeprefix("cpu:") if scope.startswith("cpu:") else issue.get("cpu_id") or ""
    return proc, _cpu_scope(cpu), pids


def _issue_conflict_fingerprint(issue: dict) -> tuple[str, str, str, str, str, str] | None:
    conflict = _complete_conflict(issue, (issue.get("conflicts") or [None])[0])
    if not conflict:
        return None
    return (
        str(issue.get("split_time") or ""),
        str(conflict.get("process_name") or ""),
        str(conflict.get("pid") or ""),
        str(conflict.get("cpu_id") or ""),
        str(conflict.get("before_time") or ""),
        str(conflict.get("after_time") or ""),
    )


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
    if not value:
        return None
    value_text = _time_text(value)
    value_dt = _parse_time_value(value)
    for boundary in boundaries:
        boundary_value = boundary.get(key)
        if boundary_value == value:
            return boundary
        if value_text and _time_text(boundary_value) == value_text:
            return boundary
        if _time_wall_text(value) and _time_wall_text(boundary_value) == _time_wall_text(value):
            return boundary
        boundary_dt = _parse_time_value(boundary_value)
        if value_dt is not None and boundary_dt is not None and boundary_dt == value_dt:
            return boundary
    return None


def _boundary_extreme(boundaries: list[dict], key: str, direction: str) -> dict | None:
    candidates = [boundary for boundary in boundaries if boundary.get(key)]
    if not candidates:
        return None

    parsed = [
        (parsed_time, boundary)
        for boundary in candidates
        if (parsed_time := _parse_time_value(boundary.get(key))) is not None
    ]
    if parsed:
        return (max if direction == "max" else min)(parsed, key=lambda item: item[0].timestamp())[1]
    return (max if direction == "max" else min)(candidates, key=lambda boundary: _time_text(boundary.get(key)))


def _boundary_old_label(boundary: dict) -> str:
    proc = boundary.get("process_name") or "-"
    pid = ",".join(boundary.get("old_pids") or [])
    cpu = _cpu_scope(boundary.get("cpu_id"))
    return f"{_proc_pid(proc, pid)}@{cpu}"


def _boundary_new_label(boundary: dict) -> str:
    proc = boundary.get("process_name") or "-"
    pid = boundary.get("new_pid") or ""
    cpu = _cpu_scope(boundary.get("cpu_id"))
    return f"{_proc_pid(proc, pid)}@{cpu}"


def _print_restart_overlap_compact(issue: dict) -> None:
    boundaries = issue.get("protected_boundaries") or []
    issue_old_end = issue.get("old_pid_end")
    issue_new_start = issue.get("new_pid_start")
    old_boundary = _boundary_endpoint(boundaries, "old_end", issue_old_end)
    new_boundary = _boundary_endpoint(boundaries, "new_start", issue_new_start)
    if old_boundary is None and not issue_old_end:
        old_boundary = _boundary_extreme(boundaries, "old_end", "max")
    if new_boundary is None and not issue_new_start:
        new_boundary = _boundary_extreme(boundaries, "new_start", "min")
    if old_boundary is None:
        old_boundary = _boundary_from_restart_evidence(issue, "old")
    if new_boundary is None:
        new_boundary = _boundary_from_restart_evidence(issue, "new")

    old_end = _format_issue_time(issue_old_end or (old_boundary or {}).get("old_end"))
    new_start = _format_issue_time(issue_new_start or (new_boundary or {}).get("new_start"))
    click.echo(f"    overlap new_start={new_start} <= old_end={old_end}")

    if old_boundary and new_boundary:
        click.echo(
            f"    conflict-pair {_boundary_old_label(old_boundary)} old_end={old_end} "
            f"overlaps {_boundary_new_label(new_boundary)} new_start={new_start}"
        )

    if old_boundary and new_boundary and (
        old_boundary.get("process_name") == new_boundary.get("process_name")
        and (old_boundary.get("cpu_id") or "") == (new_boundary.get("cpu_id") or "")
    ):
        proc = old_boundary.get("process_name") or "-"
        old_pid = ",".join(old_boundary.get("old_pids") or [])
        new_pid = new_boundary.get("new_pid") or old_boundary.get("new_pid") or "-"
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
    conflict = _complete_conflict(issue, (issue.get("conflicts") or [None])[0])
    if not conflict:
        click.echo("    conflict evidence unavailable; use --boundary-detail full 查看原始结构")
        return
    proc = conflict.get("process_name") or "-"
    pid = conflict.get("pid") or ""
    cpu = _cpu_scope(conflict.get("cpu_id"))
    before = _format_issue_time(conflict.get("before_time"))
    after = _format_issue_time(conflict.get("after_time"))
    split = _format_issue_time(issue.get("split_time"))
    click.echo(f"    conflict {_proc_pid(proc, pid)}@{cpu} spans split={split} before={before} after={after}")
    _print_boundary_log("before", conflict.get("before_log") or {})
    _print_boundary_log("after", conflict.get("after_log") or {})
    blocker = _select_blocker(issue)
    if blocker:
        bproc = blocker.get("process_name") or "-"
        bcpu = _cpu_scope(blocker.get("cpu_id"))
        role = blocker.get("role") or "-"
        old_end = _format_issue_time(blocker.get("old_end"))
        new_start = _format_issue_time(blocker.get("new_start"))
        click.echo(
            f"    blocked-by {bproc}@{bcpu} role={role} "
            f"safe_gap=({old_end}, {new_start}]"
        )


def _select_blocker(issue: dict) -> dict:
    boundaries = issue.get("protected_boundaries") or []
    if not boundaries:
        return _boundary_from_evidence(issue)

    split_dt = _parse_time_value(issue.get("split_time"))
    if split_dt is not None:
        spanning = [
            boundary
            for boundary in boundaries
            if (
                (old_end := _parse_time_value(boundary.get("old_end"))) is not None
                and (new_start := _parse_time_value(boundary.get("new_start"))) is not None
                and old_end < split_dt <= new_start
            )
        ]
        if spanning:
            return min(spanning, key=lambda boundary: _parse_time_value(boundary.get("new_start")).timestamp())
    return boundaries[0]


def _print_protected_forced_split_compact(issue: dict) -> None:
    boundary = _complete_boundary(issue, (issue.get("protected_boundaries") or [None])[0])
    if not boundary:
        click.echo("    pid-change evidence unavailable; use --boundary-detail full 查看原始结构")
        return
    proc = boundary.get("process_name") or "-"
    old_pid = ",".join(boundary.get("old_pids") or [])
    new_pid = boundary.get("new_pid") or ""
    cpu = _cpu_scope(boundary.get("cpu_id"))
    role = boundary.get("role") or "-"
    split = _format_issue_time(issue.get("split_time") or boundary.get("new_start"))
    click.echo(
        f"    pid-change {proc}@{cpu} role={role} {old_pid or '-'} -> {new_pid or '-'} "
        f"split={split} "
        f"old_end={_format_issue_time(boundary.get('old_end'))} "
        f"new_start={_format_issue_time(boundary.get('new_start'))}"
    )
    _print_boundary_log("old", boundary.get("old_log") or {})
    _print_boundary_log("new", boundary.get("new_log") or {})


def _print_pid_bounce_compact(issue: dict) -> None:
    evidence = issue.get("evidence") or []
    pids = [item.get("pid") or "-" for item in evidence]
    if not pids:
        proc, cpu, pids = _pid_bounce_from_detail(issue)
    else:
        first = next((item for item in evidence if item), {})
        proc = first.get("process_name") or issue.get("process_name") or "-"
        cpu = _cpu_scope(first.get("cpu_id"))
    if pids:
        click.echo(f"    pid-bounce {proc}@{cpu} {' -> '.join(pids)}")
    else:
        click.echo("    pid-bounce evidence unavailable; use --boundary-detail full 查看原始结构")
    for item in evidence[:3]:
        _print_boundary_log(item.get("role") or "bounce", item)


def _print_info_compact(issue: dict) -> None:
    item = (issue.get("evidence") or [{}])[0]
    if item:
        proc = item.get("process_name") or "-"
        pid = item.get("pid") or ""
        cpu = _cpu_scope(item.get("cpu_id"))
        role = item.get("role") or "context"
        ts = _format_issue_time(item.get("timestamp"))
        click.echo(f"    context {_proc_pid(proc, pid)}@{cpu} role={role} time={ts}")


def _print_boundary_issue_compact_body(issue: dict) -> None:
    kind = issue.get("kind") or ""
    if kind == "restart_boundary_overlap":
        _print_restart_overlap_compact(issue)
    elif kind in {"unsafe_cycle_split", "same_pid_kept", "same_pid_adjusted", "same_pid_adjusted_backward", "same_pid_dropped"}:
        _print_conflict_compact(issue)
    elif kind == "protected_forced_split":
        _print_protected_forced_split_compact(issue)
    elif kind == "suspect_pid_bounce":
        _print_pid_bounce_compact(issue)
    elif _severity_key(issue.get("severity")) == "INFO":
        _print_info_compact(issue)
    else:
        for item in (issue.get("evidence") or [])[:2]:
            _print_boundary_log(item.get("role") or "context", item)


def _print_issue_hint(issue: dict, task_id: str) -> None:
    commands = issue.get("suggested_commands") or []
    hint = None
    if issue.get("kind") == "restart_boundary_overlap":
        boundaries = issue.get("protected_boundaries") or []
        issue_old_end = issue.get("old_pid_end")
        issue_new_start = issue.get("new_pid_start")
        old_boundary = _boundary_endpoint(boundaries, "old_end", issue_old_end)
        new_boundary = _boundary_endpoint(boundaries, "new_start", issue_new_start)
        if old_boundary is None and not issue_old_end:
            old_boundary = _boundary_extreme(boundaries, "old_end", "max")
        if new_boundary is None and not issue_new_start:
            new_boundary = _boundary_extreme(boundaries, "new_start", "min")
        if old_boundary is None:
            old_boundary = _boundary_from_restart_evidence(issue, "old")
        if new_boundary is None:
            new_boundary = _boundary_from_restart_evidence(issue, "new")
        hint = (
            _hint_for_boundary_endpoint(commands, old_boundary, "old", task_id)
            or _hint_for_boundary_endpoint(commands, new_boundary, "new", task_id)
        )
    if hint is None:
        hint = _first_hint(commands, task_id)
    if hint:
        click.echo(f"    hint {hint}")


def _print_boundary_issue_compact(issue: dict, task_id: str) -> None:
    _print_issue_header(issue)
    _print_boundary_issue_compact_body(issue)
    _print_issue_hint(issue, task_id)


def _print_boundary_issue_full(issue: dict, task_id: str) -> None:
    _print_issue_header(issue)
    _print_boundary_issue_compact_body(issue)

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
        click.echo(f"    hint {_normalize_hint_command(command, task_id)}")


def _print_lifecycle_split_v2(result: dict, detail: str = "compact") -> None:
    boundaries = [_as_plain(item) for item in (result.get("boundaries") or [])]
    evidence = [_as_plain(item) for item in (result.get("evidence") or [])]
    issues = [_as_plain(item) for item in (result.get("issues") or [])]
    reliable = str(result.get("lifecycle_reliable", True)).lower()
    click.echo(
        "  lifecycle_split_v2: "
        f"reliable={reliable} boundaries={len(boundaries)} "
        f"evidence={len(evidence)} issues={len(issues)}"
    )

    for issue in issues:
        _print_v2_issue_compact(issue, indent="    ")

    for boundary in boundaries:
        scope = _format_v2_scope(boundary, scope_field="origin_scope")
        support_count = len(boundary.get("support_evidence") or [])
        click.echo(
            f"    boundary {boundary.get('type') or '-'} "
            f"scope={scope} time={_format_issue_time(boundary.get('timestamp'))} "
            f"support={support_count}"
        )

    for item in evidence:
        scope = _format_v2_scope(item)
        covered = len(item.get("covered_boundaries") or [])
        click.echo(
            f"    evidence {item.get('type') or '-'} "
            f"scope={scope} support={item.get('support_type') or '-'} covered={covered}"
        )
        if detail == "full" and item.get("support_type") == "wide_support":
            click.echo(
                "      wide_support: 区间内至少发生过重启但无法定位到单一边界，不是天然 error"
            )

    if detail != "full":
        return

    for issue in issues:
        _print_v2_issue_full(issue, indent="    ")


def _print_lifecycle_split_result(result: dict, detail: str = "summary") -> None:
    algorithm = _lifecycle_algorithm(result)
    if algorithm == "interval_v3":
        _print_lifecycle_split_v3(result, detail=detail)
        return
    if algorithm == "interval_v2":
        v2_detail = "full" if detail == "full" else "compact"
        _print_lifecycle_split_v2(result, detail=v2_detail)
        return
    click.echo(f"  lifecycle_split: unknown algorithm={algorithm}")


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
        f"lifecycles={len(lifecycles)} journal_evidence={len(journal_evidence)} "
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
        click.echo(
            f"    journal 回绕 scope={scope} "
            f"old_seq={evidence.get('old_sequence')} new_seq={evidence.get('new_sequence')} "
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
        pid_counts = decision.get("whitelist_pid_counts") or []
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
        click.echo(f"{indent}journal 回绕证据：未发现")
        return
    for evidence in evidence_items:
        click.echo(
            f"{indent}journal 回绕证据："
            f"old_seq={evidence.get('old_sequence')} new_seq={evidence.get('new_sequence')} "
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
        return "journal 回绕前日志在前候选段、回绕后日志在后候选段，这条边界有可靠证据支撑。"
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
        return "虽然存在 >=30 秒静默候选边界，但没有可靠边界进程 PID 冲突或 journal 回绕证据，因此聚合。"
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


def _print_v2_issue_compact(issue, *, indent: str = "    ") -> None:
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

    if issue_type == "invalid_lifecycle_evidence":
        reason = _issue_get(issue, "reason_zh") or _issue_get(issue, "explanation_zh")
        if reason:
            click.echo(f"{indent}  原因: {reason}")
    elif issue_type == "same_pid_single_boundary_conflict":
        for pair in _issue_get(issue, "conflicting_cycle_pairs", []) or []:
            left = _issue_get(pair, "left_cycle_index")
            right = _issue_get(pair, "right_cycle_index")
            boundary_time = _issue_get(pair, "boundary_timestamp")
            before = _issue_get(pair, "before_seen")
            after = _issue_get(pair, "after_seen")
            click.echo(
                f"{indent}  cycle {left}->{right} "
                f"boundary={_format_issue_time(boundary_time)} "
                f"before={_format_issue_time(before)} after={_format_issue_time(after)}"
            )
    elif issue_type == "reliable_process_multiple_pid_in_cycle":
        for cycle in _issue_get(issue, "affected_cycles", []) or []:
            cycle_scope = _format_v2_scope(cycle)
            click.echo(
                f"{indent}  affected-cycle {cycle_scope} "
                f"cycle={_issue_get(cycle, 'cycle_index')}"
            )


def _print_v2_issue_full(issue, *, indent: str = "    ") -> None:
    issue_type = _issue_get(issue, "type") or "-"
    printed = False
    printed |= _print_zh_field(issue, "rule_zh", "适用规则", indent=indent)
    printed |= _print_zh_field(issue, "facts_zh", "观测事实", indent=indent)
    printed |= _print_zh_field(issue, "current_result_zh", "当前切分结果", indent=indent)
    printed |= _print_zh_field(issue, "conflict_reason_zh", "矛盾原因", indent=indent)
    printed |= _print_zh_field(issue, "impact_zh", "影响范围", indent=indent)
    printed |= _print_zh_field(issue, "action_zh", "处理结果", indent=indent)
    if not printed:
        title = _issue_get(issue, "title_zh")
        explanation = _issue_get(issue, "explanation_zh")
        if title:
            click.echo(f"{indent}标题: {title}")
        if explanation:
            click.echo(f"{indent}说明: {explanation}")

    if issue_type == "reliable_process_multiple_pid_in_cycle":
        _print_v2_reliable_multi_pid_full(issue, indent=indent)
    elif issue_type == "same_pid_single_boundary_conflict":
        _print_v2_same_pid_full(issue, indent=indent)
    elif issue_type == "invalid_lifecycle_evidence":
        _print_v2_invalid_evidence_full(issue, indent=indent)


def _print_zh_field(issue, field: str, label: str, *, indent: str) -> bool:
    value = _issue_get(issue, field)
    if value:
        click.echo(f"{indent}{label}: {value}")
        return True
    return False


def _print_v2_invalid_evidence_full(issue, *, indent: str) -> None:
    reason = _issue_get(issue, "reason_zh")
    if reason:
        click.echo(f"{indent}原因: {reason}")

    source = _issue_get(issue, "source")
    source_file = _issue_get(issue, "source_file")
    if source or source_file:
        parts = [str(item) for item in (source, source_file) if item]
        click.echo(f"{indent}来源: {' '.join(parts)}")

    raw_excerpt = _issue_get(issue, "raw_excerpt")
    if raw_excerpt:
        click.echo(f"{indent}原始日志: {raw_excerpt}")


def _print_v2_reliable_multi_pid_full(issue, *, indent: str) -> None:
    process = _issue_get(issue, "related_process") or "-"
    window = _issue_get(issue, "cycle_window", {}) or {}
    window_start = _format_issue_time(_issue_get(window, "start_time"))
    window_end = _format_issue_time(_issue_get(window, "end_time"))
    for cycle in _issue_get(issue, "affected_cycles", []) or []:
        cycle_scope = _format_v2_scope(cycle)
        click.echo(
            f"{indent}affected-cycle {cycle_scope} cycle={_issue_get(cycle, 'cycle_index')} "
            f"window={window_start}..{window_end}"
        )

    for run in _issue_get(issue, "pid_runs", []) or []:
        pid = _issue_get(run, "pid")
        click.echo(
            f"{indent}pid-run {process}-{pid} "
            f"first={_format_issue_time(_issue_get(run, 'first_seen'))} "
            f"last={_format_issue_time(_issue_get(run, 'last_seen'))}"
        )
        first_raw = _issue_get(run, "first_raw")
        last_raw = _issue_get(run, "last_raw")
        if first_raw:
            click.echo(f"{indent}  first-raw {first_raw}")
        if last_raw and last_raw != first_raw:
            click.echo(f"{indent}  last-raw {last_raw}")

    for interval in _issue_get(issue, "expected_boundary_intervals", []) or []:
        covered = _issue_get(interval, "covered_boundaries", []) or []
        click.echo(
            f"{indent}expected-boundary "
            f"({_format_issue_time(_issue_get(interval, 'left_open_time'))}, "
            f"{_format_issue_time(_issue_get(interval, 'right_closed_time'))}] "
            f"old={_issue_get(interval, 'old_pid')} new={_issue_get(interval, 'new_pid')} "
            f"covered={len(covered)}"
        )
        for boundary in covered:
            _print_v2_boundary_source(boundary, indent=indent + "  ")


def _print_v2_same_pid_full(issue, *, indent: str) -> None:
    for pair in _issue_get(issue, "conflicting_cycle_pairs", []) or []:
        click.echo(
            f"{indent}conflict-cycle-pair "
            f"{_issue_get(pair, 'left_cycle_index')}->{_issue_get(pair, 'right_cycle_index')} "
            f"boundary={_format_issue_time(_issue_get(pair, 'boundary_timestamp'))} "
            f"before={_format_issue_time(_issue_get(pair, 'before_seen'))} "
            f"after={_format_issue_time(_issue_get(pair, 'after_seen'))}"
        )
        boundary = _issue_get(pair, "boundary")
        if boundary:
            _print_v2_boundary_source(boundary, indent=indent)


def _print_v2_boundary_source(boundary, *, indent: str) -> None:
    boundary = _as_plain(boundary)
    if not isinstance(boundary, dict):
        return
    origin = _format_v2_scope(boundary, scope_field="origin_scope")
    effective = _format_v2_scope(boundary)
    inherited = str(bool(boundary.get("inherited"))).lower()
    click.echo(
        f"{indent}boundary-source {boundary.get('type') or '-'} "
        f"origin={origin} effective={effective} inherited={inherited} "
        f"time={_format_issue_time(boundary.get('timestamp'))}"
    )
    for evidence in boundary.get("support_evidence") or []:
        _print_v2_support_evidence(evidence, indent=indent + "  ")


def _print_v2_support_evidence(evidence, *, indent: str) -> None:
    evidence = _as_plain(evidence)
    if not isinstance(evidence, dict):
        return
    process = evidence.get("process_name") or "-"
    parts = [f"{indent}support-evidence process={process}"]
    old_sequence = evidence.get("old_sequence")
    new_sequence = evidence.get("new_sequence")
    if old_sequence or new_sequence:
        parts.append(f"old_seq={old_sequence or '-'} new_seq={new_sequence or '-'}")
    old_pid = evidence.get("old_pid")
    new_pid = evidence.get("new_pid")
    if old_pid or new_pid:
        parts.append(f"old_pid={old_pid or '-'} new_pid={new_pid or '-'}")
    old_time = evidence.get("old_observed_time")
    new_time = evidence.get("new_observed_time")
    if old_time or new_time:
        parts.append(
            f"old={_format_issue_time(old_time)} new={_format_issue_time(new_time)}"
        )
    click.echo(" ".join(parts))
    old_raw = evidence.get("old_raw")
    new_raw = evidence.get("new_raw")
    if old_raw:
        click.echo(f"{indent}  old-raw {old_raw}")
    if new_raw and new_raw != old_raw:
        click.echo(f"{indent}  new-raw {new_raw}")


def _format_v2_scope(item, *, scope_field: str = "scope") -> str:
    scope = _issue_get(item, scope_field) or _issue_get(item, "scope") or "-"
    cpu_id = _issue_get(item, "cpu_id")
    if scope == "cpu" and cpu_id:
        return f"cpu_{cpu_id}"
    return str(scope)


def _boundary_detail_to_lifecycle_detail(detail: str) -> str:
    if detail == "full":
        return "full"
    if detail == "decisions":
        return "decisions"
    if detail == "off":
        return "off"
    return "summary"


def _mech_lifecycle_dfx_detail(boundary_detail: str, lifecycle_dfx: str) -> str:
    if lifecycle_dfx == "off":
        return "off"
    if lifecycle_dfx == "errors":
        return "summary"
    if lifecycle_dfx in {"decisions", "full"}:
        return lifecycle_dfx
    if boundary_detail == "full":
        return "full"
    return "summary"


def _print_boundary_issues(group: dict, task_id: str, detail: str = "compact") -> None:
    reliable = str(group.get("lifecycle_reliable", True)).lower()
    click.echo(f"  生命周期可靠性: {reliable}")
    lifecycle_result = group.get("lifecycle_split_result")
    if (
        detail != "off"
        and isinstance(lifecycle_result, dict)
        and _has_lifecycle_split_payload(lifecycle_result)
    ):
        _print_lifecycle_split_result(
            lifecycle_result,
            detail=_boundary_detail_to_lifecycle_detail(detail),
        )
    issues = group.get("boundary_issues") or []
    if not issues:
        return

    counts = _boundary_issue_counts(issues)
    click.echo(f"  生命周期切分诊断: {_format_issue_counts(counts)}")

    if detail == "full":
        for issue in issues:
            _print_boundary_issue_full(issue, task_id)
        return

    unsafe_fingerprints = {
        fingerprint
        for issue in issues
        if issue.get("kind") == "unsafe_cycle_split"
        if (fingerprint := _issue_conflict_fingerprint(issue)) is not None
    }
    for issue in issues:
        if issue.get("kind") == "same_pid_kept":
            fingerprint = _issue_conflict_fingerprint(issue)
            if fingerprint in unsafe_fingerprints:
                _print_issue_header(issue)
                click.echo("    same-evidence-as unsafe_cycle_split above")
                _print_issue_hint(issue, task_id)
                continue
        _print_boundary_issue_compact(issue, task_id)
    if counts["INFO"]:
        info_issues = [issue for issue in issues if _severity_key(issue.get("severity")) == "INFO"]
        kind_text = _format_kind_counts(_issue_kind_counts(info_issues))
        suffix = f": {kind_text}" if kind_text else ""
        click.echo(f"  INFO 诊断 {counts['INFO']} 个{suffix}，使用 --boundary-detail full 查看")


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
    boundary_detail,
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
            detail = _mech_lifecycle_dfx_detail(boundary_detail, lifecycle_dfx)
            _print_boundary_issues(group, task_id, detail=detail)
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
def mech_target_logs(task_id, problem_time, module, slot, process_name, pid, label, output):
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
    )
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


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
