# logparse Usage

## Parse Packages

Default product:

```bash
python cli.py parse tests\mock_data\diagnostic_information_20260103.zip \
  --config config.yaml \
  --output output \
  --product default
```

Compact product example:

```bash
python cli.py parse tests\mock_data_compact\compact_package_20260103.zip \
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

Config validation rejects old switch/algorithm fields and old top-level lifecycle name-map/restart fields. Use `python cli.py check-config -c config.yaml` before parsing.

Inspect V3 DFX:

```bash
python cli.py parse <package> -c config.yaml --lifecycle-dfx decisions
python cli.py mech-lifecycles <task_id> -s 1 --module EXAMPLE --show-boundaries --lifecycle-dfx full
```

`mech-lifecycles --show-boundaries` displays V3 DFX only. Legacy result files may be reported as unsupported.

## Query Logs

```bash
python cli.py mech-slots <task_id> --output output
python cli.py mech-lifecycles <task_id> --output output -s 1 --module EXAMPLE
python cli.py mech-logs <task_id> --output output -s 1 -c <board_cycle_dir> -p SERVICE --pid 12345 --module EXAMPLE
```

Mechanism logs are written under `mech_modules/<module>/slot_<id>/<board_cycle>/`.
Plain safe process names use the legacy `<proc>-<pid>.log` format. Names with
path separators, Windows-reserved names, or filesystem-unsafe characters are
encoded before writing.

Nested CPU cycle logs:

```bash
python cli.py mech-logs <task_id> \
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
python cli.py parse xxx.zip --output output --product default --debug-expand-gz
```

This uses the supported `Decompressor.extract_all(..., expand_gz=True)` path.

## Profile

```bash
python cli.py parse tests\mock_data\diagnostic_information_20260103.zip \
  --output output_optimized \
  --product default \
  --profile
```

Compare business output:

```bash
python scripts/compare_parse_outputs.py output_baseline output_optimized
```

## Config And Rule Checks

```bash
python cli.py check-config -c config.yaml
python scripts/rule_preflight.py --changed
```
