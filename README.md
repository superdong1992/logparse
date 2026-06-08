# logparse

logparse preprocesses device diagnostic packages. It extracts archives, discovers diagnostic/private logs, parses mechanism modules, writes lifecycle-organized log files under `mech_modules/`, and emits `metadata.json` plus a compact query-oriented `result.json`.

## Quick Start

```bash
pip install -r requirements.txt
python cli.py check-config -c config.yaml
python cli.py parse tests\mock_data\diagnostic_information_20260103.zip -c config.yaml --product default
```

Compact product example:

```bash
python cli.py parse tests\mock_data_compact\compact_package_20260103.zip -c config.yaml --product compact
```

## Commands

```bash
python cli.py parse <package_path> [-c config.yaml] [-o ./output] [--product default|compact] [--lifecycle-dfx errors|summary|decisions|full|off]
python cli.py parse <package_path> --debug-expand-gz
python cli.py parse <package_path> --profile

python cli.py info <task_id>
python cli.py list-slots <task_id>
python cli.py mech-slots <task_id> [-m MODULE]
python cli.py mech-lifecycles <task_id> -s <slot_id> [-m MODULE] [--show-boundaries] [--lifecycle-dfx summary|decisions|full|off]
python cli.py mech-logs <task_id> -s <slot_id> -c <board_cycle_dir> -p <proc_name> [--pid <pid>] [-m MODULE] [--cpu <cpu_id> --cpu-cycle <cpu_cycle_dir>]

python cli.py check-config [-c config.yaml]
python cli.py test-pattern -m module1 -t diag "log line"
python cli.py test-pattern -m module1 -t journal "log line"
```

## Lifecycle Split

`module1` now always uses `LifecycleSplitterV3`. The current `lifecycle_split_result.algorithm` is always `interval_v3`. Older detector/v2 display paths, legacy boundary issue output, and detailed legacy `result.json` compatibility are removed.

Current `lifecycle_split` supports only these fields:

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

Old switch/algorithm fields and old top-level lifecycle name-map/restart fields are rejected by config validation. V3 first builds candidate segments from a 30-second silent gap, then merges or keeps adjacent candidates based on reliable process PID evidence and journal evidence.

V3 `lifecycle_split_result` contains `candidate_segments`, `merge_decisions`, `lifecycles`, `journal_evidence`, `issues`, and `lifecycle_reliable`. Lifecycle issues live under `lifecycle_split_result.issues`.

Inspect V3 DFX:

```bash
python cli.py parse <package_path> -c config.yaml --lifecycle-dfx decisions
python cli.py mech-lifecycles <task_id> -s <slot_id> -m <module_name> --show-boundaries --lifecycle-dfx full
```

## Output Layout

Board cycles are top-level lifecycles. CPU cycles are nested under the matching board cycle. Board logs are written to `mech_modules/<module>/slot_<id>/<board_cycle>/<proc>-<pid>.log`; CPU-cycle logs are written to `mech_modules/<module>/slot_<id>/<board_cycle>/cpu_<id>/<cpu_cycle>/<proc>-<pid>.log`. Plain safe names are kept readable for compatibility with older scripts; names containing path separators, Windows-reserved names, or filesystem-unsafe characters are encoded before writing.

`module2` is diagnostic-log only for lifecycle purposes. It reuses module1 lifecycle results and does not run independent lifecycle splitting.

## Extraction And Performance

`Decompressor` owns archive extraction. Scanner plugins inspect the already extracted workspace only. Plain `.gz` logs are streamed by parsers unless debug expansion is enabled. For manual full-text inspection, use `--debug-expand-gz` or `pipeline.debug_expand_gz: true`.

```bash
python cli.py parse tests\mock_data\diagnostic_information_20260103.zip --profile --output output
python scripts/compare_parse_outputs.py output_baseline output_optimized
```

The checked-in `tests/mock_data*` packages and generator scripts are demo/smoke
assets. Unit tests should use focused fixtures unless they need a full package.

## Verification

```bash
python scripts/rule_preflight.py --changed
python cli.py check-config -c config.yaml
python -m pytest tests -q --basetemp output\pytest-tmp -p no:cacheprovider
```

## Docs

- `docs/usage.md`: CLI usage
- `docs/architecture.md`: architecture overview
- `docs/lifecycle-dfx-guide.md`: V3 lifecycle DFX guide
- `docs/archive/lifecycle-v2/`: archived legacy v2 config and rules
