from __future__ import annotations

import json

import re

import sys


from pathlib import Path

import click

from backend.application.configuration import (
    ConfigurationError,
    explain_config as build_config_explanation,
    load_config_file,
)

from backend.application.doctor import run_doctor

from backend.config_validation import validate_config


from backend.parsing.mech_journal_pattern import (
    JournalPatternMatcher,
    passes_line_pattern2_required_substrings,
)


from backend.presentation.cli.composition import (
    build_product_onboarding_application,
)

from backend.presentation.cli.product_onboarding import run_product_onboarding




@click.command(
    "product-onboarding",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("arguments", nargs=-1, type=click.UNPROCESSED)
def product_onboarding_command(arguments):
    """Analyze explicit samples and validate a configuration candidate.

    \b
    Operations:
      analyze --input FILE [--input FILE ...]
      validate --input FILE ... --candidate CANDIDATE.json
      build-draft --input FILE ... --candidate CANDIDATE.json
    """

    exit_code = run_product_onboarding(
        arguments,
        service_factory=build_product_onboarding_application,
    )
    raise click.exceptions.Exit(exit_code)


@click.command("doctor")
@click.option("--config", "-c", default="config.yaml", help="配置文件路径")
@click.option("--product", "-p", default=None, help="仅检查指定产品")
@click.option("--output", "-o", default=None, help="同时检查输出根目录可写性")
@click.option("--json", "json_output", is_flag=True, help="输出机器可读 JSON")
def doctor_command(config, product, output, json_output):
    """只读检查 Python、依赖、配置、插件图和输出目录。"""
    try:
        raw = load_config_file(Path(config))
        report = run_doctor(
            raw,
            product=product,
            output_root=Path(output) if output else None,
        )
    except ConfigurationError as exc:
        click.echo(str(exc), err=True)
        raise click.exceptions.Exit(1) from exc
    payload = report.to_dict()
    if json_output:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for check in payload["checks"]:
            mark = "✓" if check["ok"] else "✗"
            click.echo(f"{mark} {check['name']}: {check['message']}")
    if not report.ok:
        raise click.exceptions.Exit(1)


@click.command("explain-config")
@click.option("--config", "-c", default="config.yaml", help="配置文件路径")
@click.option("--product", "-p", default=None, help="仅解释指定产品")
@click.option("--json", "json_output", is_flag=True, help="输出机器可读 JSON")
def explain_config_command(config, product, json_output):
    """显示 schema v2 生效配置、依赖和执行顺序。"""
    try:
        payload = build_config_explanation(
            load_config_file(Path(config)),
            product=product,
        )
    except ConfigurationError as exc:
        click.echo(str(exc), err=True)
        raise click.exceptions.Exit(1) from exc
    if json_output:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    import yaml

    click.echo(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False).rstrip())


@click.command("scaffold-extension")
@click.option(
    "--kind",
    type=click.Choice(["product", "mechanism"]),
    required=True,
    help="扩展类型",
)
@click.option("--name", required=True, help="小写 snake_case 扩展名")
@click.option(
    "--root",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    help="仓库根目录",
)
@click.option("--force", is_flag=True, help="覆盖已存在的 scaffold 文件")
def scaffold_extension_command(kind, name, root, force):
    """生成产品或机制 extension 的最小代码、配置和测试。"""
    if re.fullmatch(r"[a-z][a-z0-9_]*", name) is None:
        click.echo("name 必须是小写 snake_case", err=True)
        raise click.exceptions.Exit(2)
    files = _extension_scaffold_files(kind, name)
    existing = [path for relative in files if (path := root / relative).exists()]
    if existing and not force:
        click.echo(f"文件已存在: {existing[0]}", err=True)
        raise click.exceptions.Exit(1)
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        click.echo(str(target))


def _extension_scaffold_files(kind: str, name: str) -> dict[Path, str]:
    if kind == "product":
        package = Path("backend/extensions/products") / name
        return {
            package / "__init__.py": '"""LAN product extension."""\n',
            package / "adapter.py": (
                '"""Replace the skeleton with real LAN topology adapters."""\n\n'
                "from pathlib import Path\n\n"
                "from backend.plugins.base import DirectoryDiscoveryPlugin, LogParserPlugin\n\n\n"
                "class DiscoveryPlugin(DirectoryDiscoveryPlugin):\n"
                "    def discover(self, extracted_root: Path):\n"
                "        return [], []\n\n\n"
                "class ParserPlugin(LogParserPlugin):\n"
                "    def parse(self, result):\n"
                "        return result\n\n"
                "    def write_output(self, mech_result, output_dir: Path) -> Path:\n"
                "        output_dir.mkdir(parents=True, exist_ok=True)\n"
                "        return output_dir\n"
            ),
            Path("configs/products") / f"{name}.yaml": (
                "archive:\n"
                "  recursive_extraction: true\n"
                "  compressed_extensions: [.zip, .gz]\n"
                "discovery:\n"
                f"  plugin: backend.extensions.products.{name}.adapter.DiscoveryPlugin\n"
                "  config: {}\n"
                "parser:\n"
                f"  plugin: backend.extensions.products.{name}.adapter.ParserPlugin\n"
                "  config:\n"
                "    timestamp_regex: '(\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2})'\n"
                "    active_period_gap_seconds: 300\n"
                "mechanisms: {}\n"
            ),
            Path("tests/extensions/products") / f"test_{name}_adapter.py": (
                f"from backend.extensions.products.{name}.adapter import "
                "DiscoveryPlugin, ParserPlugin\n\n\n"
                f"def test_{name}_scaffold_imports():\n"
                "    assert DiscoveryPlugin is not None\n"
                "    assert ParserPlugin is not None\n"
            ),
        }

    module_path = Path("backend/extensions/mechanisms") / f"{name}.py"
    return {
        module_path: (
            '"""LAN mechanism extension scaffold."""\n\n'
            "from backend.contracts.plugins import MechanismContext, MechanismOutcome\n"
            "from backend.extensions.mechanisms.base import MechanismPlugin as NativeMechanismPlugin\n\n\n"
            "class MechanismPlugin(NativeMechanismPlugin):\n"
            "    def execute(self, context: MechanismContext) -> MechanismOutcome:\n"
            "        # Consume context.extension_input/dependency_results/scan_batch only.\n"
            "        return MechanismOutcome()\n"
        ),
        Path("tests/extensions/mechanisms") / f"test_{name}.py": (
            f"from backend.extensions.mechanisms.{name} import MechanismPlugin\n\n\n"
            f"def test_{name}_descriptor():\n"
            f"    plugin = MechanismPlugin({{'module_name': '{name}'}}, module_key='{name}')\n"
            f"    assert plugin.descriptor.key == '{name}'\n"
            "    assert plugin.descriptor.api_version == 1\n"
        ),
        Path(".agents/skills") / f"diagnose-{name}" / "SKILL.md": (
            "---\n"
            f"name: diagnose-{name}\n"
            f"description: Diagnose {name} from bounded structured logparse evidence.\n"
            "---\n\n"
            "Consume target_logs and structured DFX only. Never export raw logs.\n"
        ),
    }


@click.command()
@click.option("--config", "-c", default="config.yaml", help="配置文件路径")
def check_config(config):
    """检查配置文件的有效性。"""
    config_path = Path(config)
    if not config_path.exists():
        click.echo(f"✗ 配置文件不存在: {config_path}")
        sys.exit(1)

    try:
        raw = load_config_file(config_path)
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


@click.command()
@click.option("--config", "-c", default="config.yaml", help="配置文件路径")
@click.option("--module", "-m", required=True, help="机制模块 key")
@click.option("--type", "-t", "log_type", type=click.Choice(["diag", "journal"]), required=True)
@click.argument("line")
def test_pattern(config, module, log_type, line):
    """用配置的正则测试一条日志行。"""
    config_path = Path(config)
    raw = load_config_file(config_path) if config_path.exists() else {}

    # Find module config from first product that has it
    mod_cfg = None
    for prod_name, prod_cfg in raw.get("products", {}).items():
        modules = prod_cfg.get("mechanisms")
        if not isinstance(modules, dict):
            continue
        if module in modules:
            module_entry = modules[module]
            mod_cfg = module_entry.get("config", {})
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
        click.echo(
            f"  模块名预过滤: {mod_name} {'✓' if mod_name in line else '✗ (Stage1 会被过滤)'}"
        )
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
            click.echo(f"✗ line_pattern2_required_substrings 未命中: {required_substrings}")
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
            click.echo(f"  line_pattern2_required_substrings: ✓ {required_substrings}")
        keyword = jnl.get("identifying_keyword", "")
        if keyword:
            click.echo(
                f"  识别关键字 '{keyword}': {'✓' if keyword in line.lower() else '✗ (Stage1 会被过滤)'}"
            )
        mod_name = mod_cfg.get("module_name", "")
        click.echo(
            f"  模块名预过滤: {mod_name} {'✓' if mod_name in line else '✗ (Stage1 会被过滤)'}"
        )
