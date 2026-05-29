# logparse Preprocessed Output Reference

Use this reference when locating issues from logparse output. The result root is usually `output/{task_id}` and contains `metadata.json`, `result.json`, `mech_modules/`, and often `extracted/`.

## Main Files

### `metadata.json`

Use this file to understand scan coverage and original input locations.

- `diagnostic_slots[]`: discovered diagnostic slots, their role, active periods, diagnostic log files, extracted paths, dump time, and timestamp counts.
- `private_slots[]`: discovered private/journal log areas such as `slot_1` and `slot_1_cpu_0`.
- `mech_results[]`: compact mechanism summary. It is useful for overview, but `result.json` has full process logs.
- `errors[]`: parse, config, decompression, and mechanism warnings that may explain missing logs.

### `result.json`

Use this file as the primary index for locating target logs.

- `mech_results[].module_key`: configured mechanism key, such as `module1` or `module2`.
- `mech_results[].module_name`: output directory name under `mech_modules/`, such as `EXAMPLE`.
- `slots[].slot_id`: slot identifier used in `slot_{slot_id}` directories.
- `slots[].lifecycle_reliable`: false when lifecycle boundaries had known unsafe or overlapping splits.
- `slots[].boundary_issues[]`: diagnostic records for boundary adjustments, overlaps, or unsafe splits.
- `board_cycles[].dir_name`: board cycle directory name, usually `{start}-{end}` like `20260103T000100-20260103T000200`; `unknown` means no bounded board cycle.
- `board_cycles[].start_time` / `end_time`: machine-readable board cycle interval for problem-time matching.
- `board_cycles[].processes[]`: board-level process lifecycle groups.
- `board_cycles[].cpu_cycles[]`: nested CPU lifecycle groups under the parent board cycle.
- `board_cycles[].cpu_cycles[].cpu_id`: CPU slot id used in the `cpu_{cpu_id}` path component.
- `board_cycles[].cpu_cycles[].dir_name`: nested CPU cycle directory name; `unknown` means no bounded CPU cycle inside the parent board cycle.
- `processes[]`: process lifecycle groups, keyed by `process_name` and `pid`.
- `processes[].missing_sequences`: missing `No[n]` values detected inside that lifecycle.
- `processes[].logs[]`: parsed log entries with timestamp, source, source file, slot, CPU, process, PID, sequence, context, and raw text.

## Written Log Layout

Mechanism logs are written under:

```text
{result_root}/mech_modules/{module_name}/slot_{slot_id}/{board_cycle_dir}/{process_name}[-pid].log
{result_root}/mech_modules/{module_name}/slot_{slot_id}/{board_cycle_dir}/cpu_{cpu_id}/{cpu_cycle_dir}/{process_name}[-pid].log
```

Examples:

```text
mech_modules/EXAMPLE/slot_1/20260103T000100-20260103T000200/SERVICE-12345.log
mech_modules/EXAMPLE/slot_1/20260103T000000-20260103T001000/cpu_1/20260103T000100-20260103T000200/SERVICE-12345.log
mech_modules/EXAMPLE/slot_1/20260103T000000-20260103T001000/cpu_1/unknown/SERVICE-12345.log
mech_modules/MODULE2/slot_2/unknown/worker-8899.log
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
- For board-level logs, match the problem time against `board_cycles[]`.
- For CPU-level logs, match the problem time against nested `cpu_cycles[]` first, then preserve the parent board cycle in the report.
- When no exact cycle covers the problem time, pick the nearest timed board or CPU cycle and mark it approximate.
- Use `unknown` board or CPU cycle only when no timed cycle can be selected.

## CLI Helpers

These commands are useful for quick checks. Use the repository root as the working directory.

```bash
python cli.py info <task_id> -o <output_dir>
python cli.py list-slots <task_id> -o <output_dir>
python cli.py query-diag <task_id> -s <slot_id> -o <output_dir>
python cli.py mech-slots <task_id> [-m <module_name>] -o <output_dir>
python cli.py mech-lifecycles <task_id> -s <slot_id> [-m <module_name>] -o <output_dir>
python cli.py mech-logs <task_id> -s <slot_id> -c <board_cycle_dir> -p <process_name-pid> [-m <module_name>] [--cpu <cpu_id> --cpu-cycle <cpu_cycle_dir>] -o <output_dir>
```

`-m` uses `module_name`, not `module_key`, in the current CLI query service. `--cpu` and `--cpu-cycle` are required to locate nested CPU-cycle logs; without `--cpu-cycle`, the query service falls back to `unknown` under the CPU directory.

## Common Signals

- `errors[]` is non-empty: explain these before diagnosing missing or malformed output.
- A target process appears in `result.json` but the written `.log` file is missing: report an output consistency issue and use `logs[].raw` from JSON as fallback evidence.
- `missing_sequences` is non-empty: mention possible dropped or unparsed `No[n]` entries.
- `lifecycle_reliable=false` or `boundary_issues[]` is non-empty: mention that lifecycle boundaries may be approximate or unsafe.
- Multiple slots have active periods around the same time: avoid assuming the active board without stronger mechanism evidence.
- `diag_entry_count` or `journal_entry_count` is zero: the mechanism may be diagnostic-only, journal-only, or misconfigured.
- `unknown` cycle: the mechanism could not map entries to a bounded lifecycle; do not overstate time correlation.
