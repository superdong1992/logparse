# logparse

logparse preprocesses device diagnostic packages. It uses a temporary workspace,
discovers product logs, writes lifecycle-organized evidence under
`mech_modules/`, and emits a versioned `parse_manifest.json`, scan-oriented
`metadata.json`, and compact query-oriented `result.json`.

After handoff, the LAN checkout is the only authoritative repository. Read
`CLAUDE.md` and `docs/lan-development-guide.md` before making changes.

## Quick Start

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python cli.py check-config -c config.yaml
.venv/bin/python cli.py parse tests/mock_data/diagnostic_information_20260103.zip -c config.yaml --product default
```

Compact product example:

```bash
.venv/bin/python cli.py parse tests/mock_data_compact/compact_package_20260103.zip -c config.yaml --product compact
```

## Commands

```bash
python cli.py parse <input_path> [-c config.yaml] [-o ./output] [--product default|compact] [--lifecycle-dfx errors|summary|decisions|full|off]
python cli.py parse <input_path> --debug-expand-gz
python cli.py parse <input_path> --profile
python cli.py parse <input_path> --keep-workspace

python cli.py info <task_id>
python cli.py list-slots <task_id>
python cli.py mech-slots <task_id> [-m MODULE]
python cli.py mech-lifecycles <task_id> -s <slot_id> [-m MODULE] [--show-boundaries] [--lifecycle-dfx summary|decisions|full|off]
python cli.py mech-target-logs <task_id> --problem-time <ISO_TIME> --module <module> --slot <slot_id> --process-name <name> [--pid <pid>] [--explain]
python cli.py mech-logs <task_id> -s <slot_id> -c <board_cycle_dir> -p <proc_name> [--pid <pid>] [-m MODULE] [--cpu <cpu_id> --cpu-cycle <cpu_cycle_dir>]
python cli.py dfx-output output/<task_id> [--deep] [--targets-json <json>]

python cli.py check-config [-c config.yaml]
python cli.py doctor [-c config.yaml] [--json]
python cli.py explain-config [-c config.yaml] [-p PRODUCT] [--json]
python cli.py migrate-config -c legacy-v1.yaml [-o config-v2.yaml]
python cli.py artifact-check output/<task_id> [--json]
python cli.py scaffold-extension --kind product|mechanism --name <name>
python cli.py test-pattern -m module1 -t diag "log line"
python cli.py test-pattern -m module1 -t journal "log line"
```

## Lifecycle Split

`config.yaml` uses schema v2 and keeps the red root index small by including
green product files from `configs/products/`. Use `explain-config` to inspect the
resolved configuration and `migrate-config` for an older v1 file; do not edit
the root schema or include loader for routine product changes.

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
python cli.py parse <input_path> -c config.yaml --lifecycle-dfx decisions
python cli.py mech-lifecycles <task_id> -s <slot_id> -m <module_name> --show-boundaries --lifecycle-dfx full
```

## Output Layout

The formal task layout is:

```text
output/<task_id>/
├── parse_manifest.json
├── metadata.json
├── result.json
├── mech_modules/
├── performance.json       # --profile only
├── dfx_report.json        # after dfx-output
├── dfx_summary.txt        # after dfx-output
└── dfx_context/           # only when deep DFX produced windows
```

The extraction directory is temporary by default. `--keep-workspace` retains it
for explicit LAN debugging. `result.json` is always compact and never contains
raw/context/per-line logs.

Board cycles are top-level lifecycles. CPU cycles are nested under the matching board cycle. Board logs are written to `mech_modules/<module>/slot_<id>/<board_cycle>/<proc>-<pid>.log`; CPU-cycle logs are written to `mech_modules/<module>/slot_<id>/<board_cycle>/cpu_<id>/<cpu_cycle>/<proc>-<pid>.log`. Plain safe names are kept readable for compatibility with older scripts; names containing path separators, Windows-reserved names, or filesystem-unsafe characters are encoded before writing.

Slot, CPU, board role, Module1, and Module2 are current-product extension
concepts; they are not generic architecture contracts.

`module2` is diagnostic-log only for lifecycle purposes. It reuses module1 lifecycle results and does not run independent lifecycle splitting. When a module2 diagnostic `Slot` value uses a frame/slot form such as `1/2`, module2 maps it to the last non-empty segment (`2`) before matching module1 slots and writing `slot_<id>` output.

## Extraction And Performance

`Decompressor` owns archive extraction. Scanner plugins inspect the already extracted workspace only. Plain `.gz` logs are streamed by parsers unless debug expansion is enabled. For manual full-text inspection, use `--debug-expand-gz` or `pipeline.debug_expand_gz: true`.

The default product also supports configured loose diagnostic inputs. After the
normal `diag/slot_*` scan, files matching
`products.default.discovery.config.loose_diagnostics.file_patterns` are merged
from anywhere under the extracted workspace.
The shipped default is conservative and does not include broad patterns such as
`*.log`; add specific patterns before parsing a single loose diagnostic log.
Parsed mechanism logs are deduplicated by parsed content across files and within
the same file. Source path fields such as `source_file` are intentionally not
part of duplicate identity, so copied logs with identical parsed timestamp,
slot/cpu, process/pid, context, sequence, active flag, and raw text collapse to
the first occurrence.
For `cli.py parse`, `<input_path>` means raw log input: a compressed log
package, a single non-compressed diagnostic log file, or a raw log directory.
Already parsed result directories such as `output/{task_id}` are consumed by
diagnosis/query workflows, not by `parse`.
Journal discovery still requires `varlog/slot_*` or `varlog/slot_*_cpu_*` as the
slot/cpu anchor, but it accepts journal files under `varlog*.zip_extracted`
children that contain `varlog*` directories.

## Deterministic DFX

Use `dfx-output` on an already parsed `output/{task_id}` directory when you need
a local, non-AI explanation of parse/output/tool consistency. Standalone
logparse does not invoke Claude CLI.

```bash
python cli.py dfx-output output/<task_id>
python cli.py dfx-output output/<task_id> --targets-json '{"problem_time":"2026-01-03T00:05:00","targets":[{"module":"EXAMPLE","slot":"1","process_name":"SERVICE","pid":"123"}]}'
```

The command writes `dfx_report.json` and one-line `dfx_summary.txt` under the task
directory. Default mode reads only structured output and `mech_modules` file
structure and does not create an empty context directory. `--deep` is LAN-only
and may write bounded target-log windows plus a manifest into `dfx_context/`;
the summary remains a single
`ERROR_CODE: 中文结论` line and must not contain raw log text.

```bash
python cli.py parse tests/mock_data/diagnostic_information_20260103.zip --profile --output output_baseline
python cli.py parse tests/mock_data/diagnostic_information_20260103.zip --profile --output output_optimized
python scripts/compare_parse_outputs.py output_baseline output_optimized
```

The comparison verifies deterministic business artifacts and intentionally
ignores timing values. Compare the two task-local `performance.json` files
separately; see `docs/large-package-performance.md` for the LAN benchmark and
2GB acceptance workflow.

The checked-in `tests/mock_data*` packages and generator scripts are demo/smoke
assets. Unit tests should use focused fixtures unless they need a full package.

## Verification

```bash
.venv/bin/python scripts/rule_preflight.py --changed
.venv/bin/python scripts/change_gate.py --changed
.venv/bin/python scripts/change_gate.py --changed --enforce \
  --change-record governance/changes/<change-id>.yaml
.venv/bin/python scripts/verify_delivery.py
.venv/bin/python cli.py check-config -c config.yaml
.venv/bin/python -m pytest tests -q --basetemp /tmp/logparse-pytest -p no:cacheprovider
```

`verify_delivery.py` runs Ruff, compileall, shipped-config validation, the full
pytest suite, and the line/branch/core coverage thresholds.

## Docs

- `docs/usage.md`: CLI usage
- `docs/architecture.md`: architecture overview
- `docs/lan-handoff-refactor-plan.md`: consolidated handoff design and status
- `docs/lan-development-guide.md`: authoritative LAN change workflow
- `docs/lan-dfx-operating-model.md`: LAN-only DFX and model boundary
- `docs/large-package-performance.md`: profile evidence and 2GB LAN acceptance
- `docs/adr/`: accepted architecture and handoff decisions
- `docs/lifecycle-dfx-guide.md`: V3 lifecycle DFX guide
- `docs/archive/lifecycle-v2/`: archived legacy v2 config and rules
