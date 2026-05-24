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
  python cli.py mech-logs <task_id> -s <slot_id> -c <cycle_dir> -p <proc>
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import click

from backend.query import ResultQueryService
from backend.utils import glob_to_regex
from backend.models import ParseResult
from backend.pipeline import Pipeline



def _print_summary(result: ParseResult, output_dir: Path) -> None:
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
                total_logs = sum(cp.total_count for c in s.board_cycles for cp in c.processes)
                total_procs = sum(len(c.processes) for c in s.board_cycles)
                click.echo(f"  slot_{s.slot_id}: {len(s.board_cycles)} 个周期, {total_procs} 进程, {total_logs} 条日志")
                for c in s.board_cycles:
                    click.echo(f"    {c.dir_name}")
                    for p in c.processes:
                        missing = f" 丢号:{p.missing_sequences}" if p.missing_sequences else ""
                        click.echo(f"      {p.process_name}-{p.pid}: {p.total_count} 条{missing}")

    json_output = output_dir / result.task_id / "result.json"
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str),
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
@click.pass_context
def parse(ctx, package_path, output, verbose, product):
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
    pipeline = Pipeline(raw_config)
    result = pipeline.run(source, output_dir, product=product, verbose=verbose)
    if result.errors:
        click.echo(f"\n⚠ {len(result.errors)} 个错误:")
        for e in result.errors:
            click.echo(f"  - {e}")

    # 输出摘要
    _print_summary(result, output_dir)


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
@click.option("--output", "-o", default="./output", help="输出目录")
def mech_slots(task_id, output):
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
    slots = svc.mech_slots(task_id)
    if not slots:
        click.echo("无机制模块解析结果")
        return
    for s in slots:
        total_logs = sum(cp["total_count"] for c in s["board_cycles"] for cp in c["processes"])
        total_procs = sum(len(c["processes"]) for c in s["board_cycles"])
        click.echo(f"slot_{s['slot_id']}: {len(s['board_cycles'])} 周期, {total_procs} 进程, {total_logs} 条日志")


@cli.command()
@click.argument("task_id")
@click.option("--slot", "-s", required=True, help="槽位 ID")
@click.option("--output", "-o", default="./output", help="输出目录")
def mech_lifecycles(task_id, slot, output):
    """列出某 slot 的机制模块周期和进程。"""
    svc = ResultQueryService(Path(output))
    result_data = svc.read_result(task_id)
    if not result_data:
        click.echo("result.json 不存在", err=True)
        sys.exit(1)
    cycles = svc.mech_lifecycles(task_id, slot)
    if cycles is None:
        click.echo(f"未找到 slot_{slot}", err=True)
        return
    for c in cycles:
        click.echo(f"{c['dir_name']}")
        for p in c["processes"]:
            missing = f" 丢号:{p['missing_sequences']}" if p.get("missing_sequences") else ""
            click.echo(f"  {p['process_name']}-{p['pid']}: {p['total_count']} 条{missing}")


@cli.command()
@click.argument("task_id")
@click.option("--slot", "-s", required=True, help="槽位 ID")
@click.option("--cycle", "-c", required=True, help="周期目录名")
@click.option("--proc", "-p", required=True, help="进程名-pid")
@click.option("--output", "-o", default="./output", help="输出目录")
def mech_logs(task_id, slot, cycle, proc, output):
    """查看指定进程批次的机制模块日志。"""
    svc = ResultQueryService(Path(output))
    log_file = svc.mech_log_path(task_id, slot, cycle, proc)
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

    errors: list[str] = []
    warnings: list[str] = []

    try:
        import yaml
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        click.echo("✓ 配置加载成功")
    except Exception as e:
        click.echo(f"✗ 配置加载失败: {e}")
        sys.exit(1)

    products = raw.get("products", {})
    if not products:
        errors.append("无产品配置 (products 段为空)")
    else:
        for prod_name, prod_cfg in products.items():
            prefix = f"[{prod_name}]"

            # Discovery config
            disc = prod_cfg.get("discovery", {}).get("config", {})
            for label, pattern in [
                ("slot_dir_pattern", disc.get("slot_dir_pattern", "slot_*")),
            ]:
                try:
                    glob_to_regex(pattern)
                except Exception:
                    errors.append(f"{prefix} {label}: glob 无效 - {pattern}")

            for p in disc.get("diag_file_patterns", []):
                try:
                    glob_to_regex(p)
                except Exception:
                    errors.append(f"{prefix} diag_file_pattern: glob 无效 - {p}")

            # Parser config
            parser_cfg = prod_cfg.get("log_parser", {}).get("config", {})

            ts_re = parser_cfg.get("timestamp_regex", "")
            if ts_re:
                try:
                    re.compile(ts_re)
                except re.error as e:
                    errors.append(f"{prefix} timestamp_regex: 正则无效 - {e}")

            for mod_key, mod_cfg in parser_cfg.get("mechanism_modules", {}).items():
                mp = f"{prefix}[{mod_key}]"
                if not mod_cfg.get("module_name"):
                    warnings.append(f"{mp} module_name 为空")
                if mod_cfg.get("diag_pattern"):
                    try:
                        r = re.compile(mod_cfg["diag_pattern"])
                        required = {"Slot", "CPU_Id", "ProcessName", "Context"}
                        if not required.issubset(r.groupindex):
                            warnings.append(f"{mp} diag_pattern 缺少命名组: {required - set(r.groupindex)}")
                    except re.error as e:
                        errors.append(f"{mp} diag_pattern: 正则无效 - {e}")

                jnl = mod_cfg.get("journal", {})
                for pat_name in ("line_pattern", "line_pattern2"):
                    val = jnl.get(pat_name, "")
                    if val:
                        try:
                            re.compile(val)
                        except re.error as e:
                            errors.append(f"{mp} journal.{pat_name}: 正则无效 - {e}")

                seq_pat = mod_cfg.get("sequence_pattern", "")
                if seq_pat:
                    try:
                        re.compile(seq_pat)
                    except re.error as e:
                        errors.append(f"{mp} sequence_pattern: 正则无效 - {e}")

    if warnings:
        click.echo(f"\n⚠ {len(warnings)} 个警告:")
        for w in warnings:
            click.echo(f"  - {w}")

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
            mod_cfg = modules[module]
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
        pat_name = "journal.line_pattern"
        pat = re.compile(jnl["line_pattern"]) if jnl.get("line_pattern") else None
        m = pat.match(line) if pat else None
        if not m and jnl.get("line_pattern2"):
            pat_name = "journal.line_pattern2"
            pat = re.compile(jnl["line_pattern2"])
            m = pat.match(line)
        if not m:
            click.echo("✗ 不匹配 journal.line_pattern 及 line_pattern2")
            sys.exit(1)
        click.echo(f"✓ 匹配 {pat_name}")
        click.echo(f"  进程名: {m.group(1)}")
        if m.group(2):
            click.echo(f"  pid: {m.group(2)}")
        click.echo(f"  序号: {m.group(3)}")
        click.echo(f"  Context: {m.group(4)}")
        keyword = jnl.get("identifying_keyword", "")
        if keyword:
            click.echo(f"  识别关键字 '{keyword}': {'✓' if keyword in line.lower() else '✗ (Stage1 会被过滤)'}")
        mod_name = mod_cfg.get("module_name", "")
        click.echo(f"  模块名预过滤: {mod_name} {'✓' if mod_name in line else '✗ (Stage1 会被过滤)'}")


if __name__ == "__main__":
    cli()
