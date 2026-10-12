"""CLI 命令注册与统一错误展示。"""

from __future__ import annotations
import sys
import click
from backend.application.configuration import ConfigurationError
from backend.infrastructure.artifact_reader import ArtifactReadError

from backend.presentation.cli.parse import parse
from backend.presentation.cli.query import (
    info,
    list_slots,
    query_diag,
    mech_slots,
    mech_lifecycles,
    mech_target_logs,
    mech_logs,
)
from backend.presentation.cli.configuration import (
    product_onboarding_command,
    doctor_command,
    explain_config_command,
    scaffold_extension_command,
    check_config,
    test_pattern,
)
from backend.presentation.cli.dfx import dfx_output, artifact_check_command


class LogparseGroup(click.Group):
    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except (ArtifactReadError, ConfigurationError) as exc:
            raise click.ClickException(str(exc)) from exc


@click.group(cls=LogparseGroup)
@click.option("--config", "-c", default="config.yaml", help="配置文件路径")
@click.pass_context
def cli(ctx, config):
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


cli.add_command(parse)
cli.add_command(info)
cli.add_command(list_slots)
cli.add_command(query_diag)
cli.add_command(mech_slots)
cli.add_command(mech_lifecycles)
cli.add_command(mech_target_logs)
cli.add_command(mech_logs)
cli.add_command(product_onboarding_command)
cli.add_command(doctor_command)
cli.add_command(explain_config_command)
cli.add_command(scaffold_extension_command)
cli.add_command(check_config)
cli.add_command(test_pattern)
cli.add_command(dfx_output)
cli.add_command(artifact_check_command)
