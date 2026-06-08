# logparse Preprocessed Output Reference

Use this reference when locating issues from logparse output. The input can be a raw compressed package or an already preprocessed result root. The result root is usually `output/{task_id}` and contains `metadata.json`, compact `result.json`, `mech_modules/`, and often `extracted/`.

## Raw Package Input

When the user gives a raw log package instead of a result root, run the repository parser before diagnosis:

```bash
python3.12 cli.py parse <package_path> -c <config_path> -o <output_dir>
```

Use config/output values from the prompt. For generated diagnosis skills, require a concrete `config_path` that includes the YAML filename, such as `config.yaml` or `configs/v3.yaml`; do not omit `-c <config_path>` and do not provide only a config directory. In interactive manual diagnosis, use the repository `config.yaml` only when the user explicitly accepts that default. Current module1 lifecycle splitting is V3-only. Verify the produced `result.json` contains V3 lifecycle payloads before continuing.

Run all repository Python commands through Python 3.12. Prefer `python3.12`; on Windows only fall back to `py -3.12` if `python3.12` is unavailable. Install dependencies with the same interpreter, for example `python3.12 -m pip install -r requirements.txt`, and do not use bare `python`.

Parsing handles archive extraction through the pipeline. It writes:

```text
{output_dir}/{task_id}/metadata.json
{output_dir}/{task_id}/result.json
{output_dir}/{task_id}/mech_modules/
{output_dir}/{task_id}/extracted/
```

After parsing, derive `task_id` from the CLI output or the package stem. If `result.json` lacks `lifecycle_split_result.algorithm == "interval_v3"` for module1 slots, report a config/result mismatch and do not present the diagnosis as official V3 evidence.

## Main Files

### `metadata.json`

Use this file to understand scan coverage and original input locations.

- `diagnostic_slots[]`: discovered diagnostic slots, their role, active periods, diagnostic log files, extracted paths, dump time, and timestamp counts.
- `private_slots[]`: discovered private/journal log areas such as `slot_1` and `slot_1_cpu_0`.
- `mech_results[]`: compact mechanism summary for overview only.
- `errors[]`: parse, config, decompression, and mechanism warnings that may explain missing logs.

### `result.json`

Use this file as the primary compact index for locating target logs and V3 lifecycle context. Default compact mode keeps summaries and omits raw per-line mechanism log entries; read written `.log` files under `mech_modules/` for original evidence.

- `mech_results[].module_key`: configured mechanism key, such as `module1` or `module2`.
- `mech_results[].module_name`: output directory name under `mech_modules/`, such as `EXAMPLE`.
- `slots[].slot_id`: slot identifier used in `slot_{slot_id}` directories.
- `slots[].lifecycle_reliable`: false when V3 lifecycle evidence has known error-level issues.
- `slots[].lifecycle_split_result`: official lifecycle_split payload when present.
- `board_cycles[].dir_name`: board cycle directory name, usually `{start}-{end}` like `20260103T000100-20260103T000200`; `unknown` means no bounded board cycle.
- `board_cycles[].start_time` / `end_time`: machine-readable board cycle interval for problem-time matching.
- `board_cycles[].processes[]`: board-level process lifecycle summaries.
- `board_cycles[].cpu_cycles[]`: nested CPU lifecycle summaries under the parent board cycle.
- `board_cycles[].cpu_cycles[].cpu_id`: CPU slot id used in the `cpu_{cpu_id}` path component.
- `board_cycles[].cpu_cycles[].dir_name`: nested CPU cycle directory name; `unknown` means no bounded CPU cycle inside the parent board cycle.
- `processes[]`: process lifecycle summaries keyed by `process_name` and `pid`.
- `processes[].missing_sequences`: missing `No[n]` values detected inside that lifecycle.

## V3 Lifecycle Payload

For current diagnosis, expect `slots[].lifecycle_split_result.algorithm == "interval_v3"` on module1 slots when lifecycle splitting is enabled. Treat these fields as the primary lifecycle evidence:

- `candidate_segments[]`: provisional segments produced from silent-gap candidate splits.
- `merge_decisions[]`: decisions explaining why adjacent candidate segments were merged or kept split.
- `lifecycles[]`: final V3 lifecycle segments; use these as the authoritative lifecycle model.
- `journal_evidence[]`: journal wrap or sequence evidence supporting candidate boundaries.
- `issues[]`: V3 reliability diagnostics; error-level issues make lifecycle evidence unsafe or incomplete.
- `lifecycle_reliable`: false when V3 found error-level reliability issues.

`candidate_segments[]` are not final lifecycles. Always cross-check them with `merge_decisions[]` and `lifecycles[]` before explaining boundaries.

## Written Log Layout

Mechanism logs are written under one of these path forms:

```text
{result_root}/mech_modules/{module_name}/slot_{slot_id}/{board_cycle_dir}/{process_name}[-pid].log
{result_root}/mech_modules/{module_name}/slot_{slot_id}/{board_cycle_dir}/cpu_{cpu_id}/{process_name}[-pid].log
{result_root}/mech_modules/{module_name}/slot_{slot_id}/{board_cycle_dir}/cpu_{cpu_id}/{cpu_cycle_dir}/{process_name}[-pid].log
```

Use the first form for board-level processes without CPU context. Use the second form for board-level process summaries whose log entries carry `cpu_id` but are not inside a nested CPU cycle. Use the third form for processes under `board_cycles[].cpu_cycles[]`.

Examples:

```text
mech_modules/EXAMPLE/slot_1/20260103T000100-20260103T000200/SERVICE-12345.log
mech_modules/EXAMPLE/slot_1/20260103T000100-20260103T000200/cpu_1/SERVICE-12345.log
mech_modules/EXAMPLE/slot_1/20260103T000000-20260103T001000/cpu_1/20260103T000100-20260103T000200/SERVICE-12345.log
mech_modules/EXAMPLE/slot_1/20260103T000000-20260103T001000/cpu_1/unknown/SERVICE-12345.log
mech_modules/MODULE2/slot_2/unknown/cpu_3/unknown/worker-8899.log
```

Each line is written as:

```text
[sequence] [source|source_file] raw log line
```

`source` is usually `diagnostic` or `journal`. The `source_file` points back to the diagnostic archive or private/journal file that produced the line.

## Matching Rules

- Match module by either `module_key` or `module_name` when the user provides a module.
- Match slot exactly after normalizing `slot_1` to `1` if needed.
- Match process name case-insensitively.
- Match PID exactly as a string.
- When both process name and PID are provided, require both.
- Treat each requested process as requiring its own matched log. Multiple requested processes produce multiple matched logs.
- Use `python3.12 cli.py mech-target-logs ...` for target-log lookup. Do not make the model manually compare `problem_time` against `board_cycles[]` or nested `cpu_cycles[]`.
- `mech-target-logs` matches board-level logs against `board_cycles[]`, CPU-level logs against nested `cpu_cycles[]` first, and returns exact/nearest/unknown/missing/ambiguous status in JSON.
- Use `unknown` board or CPU cycle only when `mech-target-logs` returns that status; do not substitute related logs.

## CLI Helpers

These commands are useful for quick checks. Use the repository root as the working directory.

```bash
python3.12 cli.py info <task_id> -o <output_dir>
python3.12 cli.py parse <package_path> -c <config_path> -o <output_dir>
python3.12 cli.py parse <package_path> -c <config_path> -o <output_dir> --verbose
python3.12 cli.py list-slots <task_id> -o <output_dir>
python3.12 cli.py query-diag <task_id> -s <slot_id> -o <output_dir>
python3.12 cli.py mech-slots <task_id> [-m <module_name>] -o <output_dir>
python3.12 cli.py mech-target-logs <task_id> --problem-time <ISO_TIME> --module <module_key_or_name> --slot <slot_id> --process-name <process_name> [--pid <pid>] [--label <label>] -o <output_dir>
python3.12 cli.py mech-lifecycles <task_id> -s <slot_id> [-m <module_name>] --show-boundaries --lifecycle-dfx decisions -o <output_dir>
python3.12 cli.py mech-lifecycles <task_id> -s <slot_id> [-m <module_name>] --show-boundaries --lifecycle-dfx full -o <output_dir>
python3.12 cli.py mech-logs <task_id> -s <slot_id> -c <board_cycle_dir> -p <process_name-pid> [-m <module_name>] [--cpu <cpu_id> --cpu-cycle <cpu_cycle_dir>] -o <output_dir>
```

`mech-target-logs --module` accepts either `module_key` or `module_name` and is the preferred target-log handoff for diagnosis skills. `mech-lifecycles -m` and `mech-logs -m` use `module_name`. `--cpu` with `--cpu-cycle` locates nested CPU-cycle logs. For board-level CPU logs, `mech-target-logs` checks the direct `cpu_{cpu_id}/{process}.log` path when the nested CPU-cycle path is absent.

## Common Signals

- `errors[]` is non-empty: explain these before diagnosing missing or malformed output.
- `errors[]` is non-empty and the requested module has no `result.json` entry or no `mech_modules/{module_name}/` logs: report a hard parse/output error.
- `lifecycle_split_result.algorithm != "interval_v3"` or missing: treat lifecycle evidence as legacy or incomplete for current V3-first diagnosis.
- `lifecycle_reliable=false` or V3 `issues[]` is non-empty: mention that lifecycle evidence may be unsafe or incomplete.
- V3 `merge_decisions[]` kept a split: explain the blocking reason before treating adjacent candidates as separate lifecycles.
- `missing_sequences` is non-empty: mention possible dropped or unparsed `No[n]` entries.
- A target process summary exists in compact `result.json` but the written `.log` file is missing: report an output consistency error for that requested process.
- Multiple slots have active periods around the same time: avoid assuming the active board without stronger mechanism evidence.
- `diag_entry_count` or `journal_entry_count` is zero: the mechanism may be diagnostic-only, journal-only, or misconfigured.
- `unknown` cycle: the mechanism could not map entries to a bounded lifecycle; do not overstate time correlation.
