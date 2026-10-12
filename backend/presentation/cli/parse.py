from __future__ import annotations


import sys


from pathlib import Path

import click

from backend.application.configuration import (
    ConfigurationError,
    load_config_file,
)


from backend.config_validation import validate_config

from backend.application.parse_service import ParseServiceError

from backend.contracts.runtime import ParseRequest, ParseRuntimeOptions


from backend.presentation.cli.composition import (
    build_parse_application,
)


from backend.presentation.cli.rendering import (
    _print_summary,
    _print_parse_errors,
)


@click.command()
@click.argument("package_path", type=click.Path(exists=True))
@click.option("--config", "-c", default=None, help="配置文件路径")
@click.option("--output", "-o", default="./output", help="输出目录")
@click.option(
    "--verbose", "-v", is_flag=True, help="通用详细输出；生命周期 DFX 请用 --lifecycle-dfx"
)
@click.option("--product", "-p", default="default", help="产品名（default/compact）")
@click.option(
    "--debug-expand-gz", is_flag=True, default=False, help="强制在解析过程中将 .gz 日志就地展开"
)
@click.option("--profile", is_flag=True, default=False, help="生成 performance.json 并打印性能摘要")
@click.option(
    "--keep-workspace", is_flag=True, default=False, help="保留任务 extracted 工作区用于人工调试"
)
@click.option(
    "--lifecycle-dfx",
    type=click.Choice(["off", "errors", "summary", "decisions", "full"]),
    default="errors",
    show_default=True,
    help="生命周期聚合/切分中文说明输出级别",
)
@click.pass_context
def parse(
    ctx,
    package_path,
    config,
    output,
    verbose,
    product,
    debug_expand_gz,
    profile,
    keep_workspace,
    lifecycle_dfx,
):
    """解析日志压缩包、单个日志文件或原始日志目录。"""
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

    if not Path(config_path).exists():
        click.echo(f"✗ 配置文件不存在: {config_path}", err=True)
        raise click.exceptions.Exit(1)
    try:
        raw_config = load_config_file(Path(config_path))
    except ConfigurationError as exc:
        click.echo(f"✗ 配置加载失败: {exc}", err=True)
        raise click.exceptions.Exit(1) from exc
    config_errors = validate_config(raw_config)
    if config_errors:
        click.echo(f"✗ 配置检查失败: {len(config_errors)} 个错误", err=True)
        for error in config_errors:
            click.echo(f"  - {error}", err=True)
        raise click.exceptions.Exit(1)
    pipeline_config = raw_config.get("pipeline", {})
    if not isinstance(pipeline_config, dict):
        pipeline_config = {}
    application = build_parse_application(raw_config)
    request = ParseRequest(
        source=source,
        output_root=output_dir,
        product=product,
        options=ParseRuntimeOptions(
            extraction_workers=pipeline_config.get("extraction_workers", "auto"),
            diagnostic_scan_workers=pipeline_config.get(
                "diagnostic_scan_workers",
                "auto",
            ),
            debug_expand_gz=(
                debug_expand_gz or bool(pipeline_config.get("debug_expand_gz", False))
            ),
            keep_workspace=(keep_workspace or bool(pipeline_config.get("keep_workspace", False))),
            profile=profile,
            verbose=verbose,
        ),
    )
    try:
        run = application.service.run(request)
    except ParseServiceError as exc:
        click.echo(f"✗ 解析失败: {exc}", err=True)
        if exc.manifest_written:
            click.echo(
                f"失败清单: {output_dir / exc.task_id / 'parse_manifest.json'}",
                err=True,
            )
        else:
            click.echo("任务目录未修改。", err=True)
        raise click.exceptions.Exit(1) from exc
    result = run.result
    _print_parse_errors(result, verbose=verbose, lifecycle_dfx=lifecycle_dfx)

    _print_summary(result, output_dir, detailed=not profile)
    click.echo(f"任务清单: {output_dir / result.task_id / 'parse_manifest.json'}")
    if profile:
        click.echo("\n=== 性能摘要 ===")
        for line in application.engine.performance.summary_lines():
            click.echo(line)
