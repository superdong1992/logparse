from __future__ import annotations

import json


import sys


from pathlib import Path

import click


from backend.extensions.products.current.dfx import build_dfx_output, check_task_artifacts


@click.command("dfx-output")
@click.argument("output_task_dir")
@click.option("--deep", is_flag=True, default=False, help="局域网内读取目标日志短窗口")
@click.option(
    "--targets-json",
    default=None,
    help="目标 anchors JSON；可为列表或包含 targets/problem_time 的对象",
)
@click.option(
    "--problem-time",
    default=None,
    help="target 级 DFX 的问题时间；优先级高于 targets-json 内 problem_time",
)
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


@click.command("artifact-check")
@click.argument("output_task_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "json_output", is_flag=True, help="输出机器可读 JSON")
def artifact_check_command(output_task_dir, json_output):
    """只读校验 manifest、schema、hash、索引与证据目录。"""
    report = check_task_artifacts(Path(output_task_dir))
    if json_output:
        click.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        mark = "✓" if report["ok"] else "✗"
        click.echo(f"{mark} artifact-check: {report['status']}")
        for issue in report["issues"]:
            click.echo(f"  {issue['code']}: {issue['message']}")
    if not report["ok"]:
        raise click.exceptions.Exit(1)
