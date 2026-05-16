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
  python cli.py aaa-slots <task_id>
  python cli.py aaa-lifecycles <task_id> -s <slot_id>
  python cli.py aaa-logs <task_id> -s <slot_id> -c <cycle_dir> -p <proc>
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import click

from backend.aaa_parser import AaaParser
from backend.config import BoardConfig, ConfigLoader
from backend.decompressor import Decompressor
from backend.identifier import Identifier
from backend.log_parser import LogParser
from backend.metadata import MetadataGenerator
from backend.models import ParseResult
from backend.scanner import Scanner


def _extract_inner_contents(slots, decompressor):
    """解压每个槽位下的诊断日志压缩包内容到 extracted/ 内的 _extracted 子目录。"""
    for slot in slots:
        for entry in slot.diagnostic_logs:
            if not entry.compressed:
                continue
            src = Path(entry.path)
            if not src.exists():
                continue
            dest = src.parent / f"{entry.name}_extracted"
            try:
                decompressor.extract_all(src, dest)
                entry.extracted_path = str(dest)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("解压诊断日志内容失败 %s: %s", src, e)


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
@click.pass_context
def parse(ctx, package_path, output, verbose):
    """解析日志压缩包。"""
    config_path = ctx.obj["config_path"]
    config_loader = ConfigLoader(config_path)
    config_loader.load()

    decompressor = Decompressor(config_loader)
    scanner = Scanner(config_loader)
    log_parser = LogParser(config_loader)
    identifier = Identifier()
    aaa_parser = AaaParser(config_loader)
    aaa_parser.verbose = verbose
    aaa_parser.debug_filter = "dhcp"  # 只追踪指定进程的调试日志
    metadata_gen = MetadataGenerator()

    source = Path(package_path)
    output_dir = Path(output)
    task_id = source.stem

    extract_dir = output_dir / task_id / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []

    def _step(label, fn):
        t0 = time.time()
        try:
            r = fn()
        except Exception as e:
            click.echo(f"  {label} ✗ {e}")
            errors.append(f"{label}: {e}")
            return None
        elapsed = time.time() - t0
        extra = ""
        if verbose:
            if isinstance(r, list):
                extra = f" ({len(r)} 项, {elapsed:.1f}s)"
            elif isinstance(r, dict):
                extra = f" ({len(r)} 模块, {elapsed:.1f}s)"
            else:
                extra = f" ({elapsed:.1f}s)"
        click.echo(f"  {label} ✓{extra}")
        return r

    _step(f"[1/6] 解压 {source.name}",
          lambda: decompressor.extract_all(source, extract_dir, recursive=True))

    diag_slots = _step("[2/6] 扫描 diag/", lambda: scanner.scan_diag(extract_dir)) or []
    private_slots = _step("[3/6] 扫描 varlog/", lambda: scanner.scan_private(extract_dir)) or []

    result = ParseResult(
        task_id=task_id,
        package_name=source.name,
        extracted_root=str(extract_dir),
        diagnostic_slots=diag_slots,
        private_slots=private_slots,
        errors=errors,
    )

    _step("[4/6] 解压诊断日志内容",
          lambda: _extract_inner_contents(result.diagnostic_slots, decompressor))

    # 机制模块日志解析（优先）
    aaa_results = _step("[5/6] AAA 解析",
                         lambda: aaa_parser.parse_all(result))
    if aaa_results:
        for aaa_result in aaa_results.values():
            aaa_parser.apply_to_identifier(aaa_result, result)
            aaa_dir = aaa_parser.write_output(aaa_result, output_dir / task_id)
            if verbose:
                total = sum(cp.total_count for s in aaa_result.slots for c in s.board_cycles for cp in c.processes)
                diag = aaa_result.diag_entry_count
                jnl = aaa_result.journal_entry_count
                match_mark = "" if diag + jnl == total else " ⚠ 条数不一致"
                click.echo(f"    [{aaa_result.module_name}] 诊断:{diag} + journal:{jnl} = {diag+jnl} → 输出:{total}{match_mark} → {aaa_dir}")
        result.aaa_results = list(aaa_results.values())

    # 兜底
    _step("[6/6] 时间戳提取+兜底识别",
          lambda: (log_parser.build_all_periods(result.diagnostic_slots), identifier.analyze(result)))

    cfg = config_loader.get_config()
    if cfg.output.generate_metadata:
        metadata_path = metadata_gen.generate(result, output_dir / task_id)
        if verbose:
            click.echo(f"  元数据: {metadata_path}")

    if errors:
        click.echo(f"\n⚠ {len(errors)} 个错误:")
        for e in errors:
            click.echo(f"  - {e}")

    # 输出摘要
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

    if result.aaa_results:
        for aaa_result in result.aaa_results:
            click.echo(f"\n--- 机制模块 [{aaa_result.module_name}] 日志 ---")
            click.echo(f"  主控信号槽位: {aaa_result.active_master_slots}")
            for s in aaa_result.slots:
                total_logs = sum(cp.total_count for c in s.board_cycles for cp in c.processes)
                total_procs = sum(len(c.processes) for c in s.board_cycles)
                click.echo(f"  slot_{s.slot_id}: {len(s.board_cycles)} 个周期, {total_procs} 进程, {total_logs} 条日志")
                for c in s.board_cycles:
                    click.echo(f"    {c.dir_name}")
                    for p in c.processes:
                        missing = f" 丢号:{p.missing_sequences}" if p.missing_sequences else ""
                        click.echo(f"      {p.process_name}-{p.pid}: {p.total_count} 条{missing}")

    if result.switchover_timeline:
        click.echo(f"\n主备倒换事件 ({len(result.switchover_timeline)} 次):")
        for event in result.switchover_timeline:
            click.echo(f"  slot_{event.from_slot} → slot_{event.to_slot} @ {event.time}")
            click.echo(f"    依据: {event.evidence}")

    json_output = output_dir / task_id / "result.json"
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    click.echo(f"\n完整结果: {json_output}")


@cli.command()
@click.argument("task_id")
@click.option("--output", "-o", default="./output", help="输出目录")
def info(task_id, output):
    """查看任务的元数据。"""
    metadata_path = Path(output) / task_id / "metadata.json"
    if not metadata_path.exists():
        click.echo(f"任务 {task_id} 的元数据不存在", err=True)
        sys.exit(1)
    click.echo(metadata_path.read_text(encoding="utf-8"))


@cli.command()
@click.argument("task_id")
@click.option("--output", "-o", default="./output", help="输出目录")
def list_slots(task_id, output):
    """列出任务中识别到的所有槽位。"""
    metadata_path = Path(output) / task_id / "metadata.json"
    if not metadata_path.exists():
        click.echo(f"任务 {task_id} 的元数据不存在", err=True)
        sys.exit(1)
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    for slot in data.get("diagnostic_slots", []):
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
    metadata_path = Path(output) / task_id / "metadata.json"
    if not metadata_path.exists():
        click.echo(f"任务 {task_id} 的元数据不存在", err=True)
        sys.exit(1)
    data = json.loads(metadata_path.read_text(encoding="utf-8"))

    for s in data.get("diagnostic_slots", []):
        if s["slot_id"] == slot:
            click.echo(json.dumps(s, ensure_ascii=False, indent=2))
            return

    click.echo(f"未找到 slot_{slot}", err=True)


@cli.command()
@click.argument("task_id")
@click.option("--output", "-o", default="./output", help="输出目录")
def aaa_slots(task_id, output):
    """列出 AAA 模块各 slot 概况。"""
    metadata_path = Path(output) / task_id / "metadata.json"
    if not metadata_path.exists():
        click.echo(f"任务 {task_id} 的元数据不存在", err=True)
        sys.exit(1)
    # 直接从 result.json 读取完整结构
    result_path = Path(output) / task_id / "result.json"
    if not result_path.exists():
        click.echo("result.json 不存在", err=True)
        sys.exit(1)
    data = json.loads(result_path.read_text(encoding="utf-8"))
    aaa = data.get("aaa_results")
    if not aaa:
        click.echo("无 AAA 解析结果")
        return
    aaa = aaa[0]  # 取第一个模块
    for s in aaa.get("slots", []):
        total_logs = sum(cp["total_count"] for c in s["board_cycles"] for cp in c["processes"])
        total_procs = sum(len(c["processes"]) for c in s["board_cycles"])
        click.echo(f"slot_{s['slot_id']}: {len(s['board_cycles'])} 周期, {total_procs} 进程, {total_logs} 条日志")


@cli.command()
@click.argument("task_id")
@click.option("--slot", "-s", required=True, help="槽位 ID")
@click.option("--output", "-o", default="./output", help="输出目录")
def aaa_lifecycles(task_id, slot, output):
    """列出某 slot 的 AAA 周期和进程。"""
    result_path = Path(output) / task_id / "result.json"
    if not result_path.exists():
        click.echo("result.json 不存在", err=True)
        sys.exit(1)
    data = json.loads(result_path.read_text(encoding="utf-8"))
    aaa = data.get("aaa_results")
    if not aaa:
        click.echo("无 AAA 解析结果")
        return
    aaa = aaa[0]
    for s in aaa.get("slots", []):
        if s["slot_id"] == slot:
            for c in s["board_cycles"]:
                click.echo(f"{c['dir_name']}")
                for p in c["processes"]:
                    missing = f" 丢号:{p['missing_sequences']}" if p.get("missing_sequences") else ""
                    click.echo(f"  {p['process_name']}-{p['pid']}: {p['total_count']} 条{missing}")
            return
    click.echo(f"未找到 slot_{slot}", err=True)


@cli.command()
@click.argument("task_id")
@click.option("--slot", "-s", required=True, help="槽位 ID")
@click.option("--cycle", "-c", required=True, help="周期目录名")
@click.option("--proc", "-p", required=True, help="进程名-pid")
@click.option("--output", "-o", default="./output", help="输出目录")
def aaa_logs(task_id, slot, cycle, proc, output):
    """查看指定进程批次的 AAA 日志。"""
    log_file = Path(output) / task_id / "aaa" / f"slot_{slot}" / cycle / f"{proc}.log"
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

    # 加载配置
    try:
        loader = ConfigLoader(config_path)
        loader.load()
        cfg = loader.get_config()
        click.echo("✓ 配置加载成功")
    except Exception as e:
        click.echo(f"✗ 配置加载失败: {e}")
        sys.exit(1)

    # 检查正则可编译
    checks = [
        ("诊断日志文件名时间戳正则", cfg.diagnostic_files.filename_timestamp_regex),
        ("日志内容时间戳正则", cfg.log_content.timestamp_regex),
        ("journal 文件序号正则", cfg.private_logs.journal_files.sequence_regex),
    ]
    for label, pattern in checks:
        try:
            re.compile(pattern)
        except re.error as e:
            errors.append(f"{label}: 正则无效 - {e}")

    # 检查 glob 模式
    globs = [
        ("slot 目录匹配", cfg.boards.get("main_control", BoardConfig()).dir_pattern),
        *[(f"varlog 目录匹配[{p}]", p) for p in cfg.private_logs.dir_patterns],
        *[(f"诊断日志文件匹配[{p}]", p) for p in cfg.diagnostic_files.patterns],
        *[(f"journal 文件匹配[{p}]", p) for p in cfg.private_logs.journal_files.patterns],
    ]
    for label, pattern in globs:
        try:
            _compile_glob(pattern)
        except Exception:
            errors.append(f"{label}: glob 无效 - {pattern}")

    # 检查机制模块配置
    for mod_key, mod_cfg in cfg.mechanism_modules.items():
        if not mod_cfg.enabled:
            continue
        if not mod_cfg.module_name:
            warnings.append(f"[{mod_key}] module_name 为空，不会生效")
        if mod_cfg.diag_pattern:
            try:
                re.compile(mod_cfg.diag_pattern)
            except re.error as e:
                errors.append(f"[{mod_key}] diag_pattern 无效: {e}")
        else:
            warnings.append(f"[{mod_key}] diag_pattern 为空")
        if mod_cfg.journal.line_pattern:
            try:
                re.compile(mod_cfg.journal.line_pattern)
            except re.error as e:
                errors.append(f"[{mod_key}] journal.line_pattern 无效: {e}")
        if mod_cfg.journal.line_pattern2:
            try:
                re.compile(mod_cfg.journal.line_pattern2)
            except re.error as e:
                errors.append(f"[{mod_key}] journal.line_pattern2 无效: {e}")
        if not mod_cfg.journal.identifying_keyword:
            warnings.append(f"[{mod_key}] journal.identifying_keyword 为空")
        if mod_cfg.sequence_pattern:
            try:
                re.compile(mod_cfg.sequence_pattern)
            except re.error as e:
                errors.append(f"[{mod_key}] sequence_pattern 无效: {e}")

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
    loader = ConfigLoader(config)
    loader.load()
    mod_cfg = loader.get_mech_module_config(module)
    if mod_cfg is None:
        click.echo(f"✗ 模块 '{module}' 未配置", err=True)
        sys.exit(1)

    if log_type == "diag":
        if not mod_cfg.diag_pattern:
            click.echo("✗ diag_pattern 未配置", err=True)
            sys.exit(1)
        pat = re.compile(mod_cfg.diag_pattern)
        m = pat.search(line)
        if not m:
            click.echo("✗ 不匹配 diag_pattern")
            sys.exit(1)
        click.echo("✓ 匹配 diag_pattern")
        click.echo(f"  模块名预过滤: {mod_cfg.module_name} {'✓' if mod_cfg.module_name in line else '✗ (Stage1 会被过滤)'}")
        for name, value in m.groupdict().items():
            click.echo(f"  {name}: {value}")
        seq_m = re.search(mod_cfg.sequence_pattern, line) if mod_cfg.sequence_pattern else None
        if seq_m:
            click.echo(f"  序号: {seq_m.group(1)}")
        if mod_cfg.active_master_keyword and re.search(mod_cfg.active_master_keyword, line):
            click.echo(f"  ✓ 命中主控关键字: {mod_cfg.active_master_keyword}")
        ts = loader.extract_content_timestamps(line)
        if ts:
            click.echo(f"  时间戳: {ts[0].isoformat()}")

    else:  # journal
        if not mod_cfg.journal.line_pattern and not mod_cfg.journal.line_pattern2:
            click.echo("✗ journal.line_pattern 和 line_pattern2 均未配置", err=True)
            sys.exit(1)
        pat_name = "journal.line_pattern"
        pat = re.compile(mod_cfg.journal.line_pattern) if mod_cfg.journal.line_pattern else None
        m = pat.match(line) if pat else None
        if not m and mod_cfg.journal.line_pattern2:
            pat_name = "journal.line_pattern2"
            pat = re.compile(mod_cfg.journal.line_pattern2)
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
        kw = mod_cfg.journal.identifying_keyword
        if kw:
            click.echo(f"  识别关键字 '{kw}': {'✓' if kw in line.lower() else '✗ (Stage1 会被过滤)'}")
        click.echo(f"  模块名预过滤: {mod_cfg.module_name} {'✓' if mod_cfg.module_name in line else '✗ (Stage1 会被过滤)'}")


def _compile_glob(pattern: str) -> re.Pattern:
    regex = re.escape(pattern)
    regex = regex.replace(r"\*", ".*")
    regex = regex.replace(r"\?", ".")
    return re.compile(f"^{regex}$", re.IGNORECASE)


if __name__ == "__main__":
    cli()
