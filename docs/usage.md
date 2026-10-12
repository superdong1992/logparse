# logparse Usage

以下命令使用 `.venv` 中的 Python 3.12；Windows 将 `.venv/bin/python` 替换为 `.venv\Scripts\python.exe`。配置必须显式声明 `schema_version: 2`，旧 v1 配置及 `migrate-config` 已移除。

旧版配置升级请按 [配置迁移指南](config-migration-v2.md) 操作。指南以原 `default` 的 Module1/Module2 配置迁移为 `galaxy` 款型为例，列出字段对应关系、插件路径、业务参数替换和验证步骤。

按任务查阅代码见 [AI 阅读入口](ai-reading-guide.md)，实现职责见 [架构](architecture.md)，两套产品的目录、字段、过滤规则、完整合成示例及已知限制见 [产品日志规格](product-log-specification.md)。本文保留命令用法，日志规格以专题文档为准。

产品由 `--product` 显式选择，CLI 默认使用 `default`。同一根配置同时包含 default 和 compact 时，仅指定 `-c config.yaml` 不会自动识别 compact。原始输入的预处理命令应明确使用 `--product default` 或 `--product compact`；已有任务按其 `metadata.json` / manifest 核对产品，不重新解析。

## Parse Packages

Default product:

```bash
.venv/bin/python cli.py parse tests\mock_data\diagnostic_information_20260103.zip \
  --config config.yaml \
  --output output \
  --product default
```

Compact product example:

```bash
.venv/bin/python cli.py parse tests\mock_data_compact\compact_package_20260103.zip \
  --config config.yaml \
  --output output \
  --product compact
```

Common options:

| Option | Meaning |
|---|---|
| `--config` | Config file; current entry is `config.yaml` |
| `--output` | Output directory |
| `--product` | Product branch, such as `default` or `compact` |
| `--lifecycle-dfx` | V3 lifecycle DFX level: `errors`, `summary`, `decisions`, `full`, `off` |
| `--debug-expand-gz` | Debug-only plain `.gz` expansion |
| `--profile` | Generate `performance.json` and print a performance summary |

The checked-in `tests/mock_data*` packages are demo/smoke assets. Their
generator scripts can refresh those packages, but pytest unit tests should use
focused fixtures unless they explicitly need a full-package smoke input.

## Loose Default Inputs

For the default product, normal `diag/slot_*` discovery is still preferred. In
addition, scanner discovery can merge loose diagnostic logs that match
`products.default.discovery.config.loose_diagnostics.file_patterns` anywhere in
the extracted workspace. The default patterns are intentionally conservative and
do not include `*.log`; add a specific pattern such as `diag_*.log` before
parsing a single loose diagnostic file.

After parsing, mechanism log entries are deduplicated by parsed content across
all discovered files and within a single file. The source path is excluded from
duplicate identity; timestamp, slot/cpu, process/pid, context, sequence, active
flag, and raw text must match for a later entry to be removed.

Journal logs remain anchored by `varlog/slot_*` or `varlog/slot_*_cpu_*` so
slot/cpu ownership is explicit. Under each slot directory, scanner discovery
accepts `journal.log` and `journal.log.*.gz` from `varlog*.zip_extracted`
children containing `varlog*` directories, such as
`varlog_other.zip_extracted/varlog_other/journal.log`.

## Lifecycle Split V3

`Module1Plugin` always uses `LifecycleSplitterV3`. Current `lifecycle_split` supports only:

```yaml
lifecycle_split:
  process_name_mapping:
    canonical_proc:
      - alias_in_diag
      - alias_in_journal
  reliable_processes:
    - canonical_lifecycle_proc
  multi_instance_processes: []
```

The output algorithm is fixed:

```text
lifecycle_split_result.algorithm == "interval_v3"
```

V3 result fields:

- `candidate_segments`
- `merge_decisions`
- `lifecycles`
- `journal_evidence`
- `issues`
- `lifecycle_reliable`

Config validation rejects old switch/algorithm fields and old top-level lifecycle name-map/restart fields. Use `.venv/bin/python cli.py check-config -c config.yaml` before parsing.

Inspect V3 DFX:

```bash
.venv/bin/python cli.py parse <input_path> -c config.yaml --lifecycle-dfx decisions
.venv/bin/python cli.py mech-lifecycles <task_id> -s 1 --module EXAMPLE --show-boundaries --lifecycle-dfx full
```

`mech-lifecycles --show-boundaries` displays V3 DFX only. Legacy result files may be reported as unsupported.

## Query Logs

```bash
.venv/bin/python cli.py mech-slots <task_id> --output output
.venv/bin/python cli.py mech-lifecycles <task_id> --output output -s 1 --module EXAMPLE
.venv/bin/python cli.py mech-logs <task_id> --output output -s 1 -c <board_cycle_dir> -p SERVICE --pid 12345 --module EXAMPLE
```

Mechanism logs are written under `mech_modules/<module>/slot_<id>/<board_cycle>/`.
Plain safe process names use the legacy `<proc>-<pid>.log` format. Names with
path separators, Windows-reserved names, or filesystem-unsafe characters are
encoded before writing.

Nested CPU cycle logs:

```bash
.venv/bin/python cli.py mech-logs <task_id> \
  --output output \
  -s 1 \
  -c <board_cycle_dir> \
  -p SERVICE \
  --pid 12345 \
  --module EXAMPLE \
  --cpu 1 \
  --cpu-cycle <cpu_cycle_dir>
```

## Plain `.gz` Logs

Plain `.gz` logs are not expanded into `extracted/` by default. Parsers stream them directly. For manual inspection:

```bash
.venv/bin/python cli.py parse xxx.zip --output output --product default --debug-expand-gz
```

This uses the supported `Decompressor.extract_all(..., expand_gz=True)` path.

## Profile

```bash
.venv/bin/python cli.py parse tests\mock_data\diagnostic_information_20260103.zip \
  --output output_optimized \
  --product default \
  --profile
```

Compare business output:

```bash
.venv/bin/python scripts/compare_parse_outputs.py output_baseline output_optimized
```

## Config And Rule Checks

```bash
.venv/bin/python cli.py check-config -c config.yaml
.venv/bin/python scripts/rule_preflight.py --changed
```
