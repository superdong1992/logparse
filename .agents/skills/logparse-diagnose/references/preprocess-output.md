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
- `board_cycles[].dir_name`: cycle directory name, usually `{start}-{end}` like `20260103T000100-20260103T000200`; `unknown` means no bounded cycle.
- `board_cycles[].start_time` / `end_time`: machine-readable cycle interval for problem-time matching.
- `processes[]`: process lifecycle groups, keyed by `process_name`, `pid`, and effectively `cpu_id` from the first log entry.
- `processes[].missing_sequences`: missing `No[n]` values detected inside that lifecycle.
- `processes[].logs[]`: parsed log entries with timestamp, source, source file, slot, CPU, process, PID, sequence, context, and raw text.

## Written Log Layout

Mechanism logs are written under:

```text
{result_root}/mech_modules/{module_name}/slot_{slot_id}/{cycle_dir}/[cpu_{cpu_id}/]{process_name}[-pid].log
```

Examples:

```text
mech_modules/EXAMPLE/slot_1/20260103T000100-20260103T000200/SERVICE-12345.log
mech_modules/EXAMPLE/slot_1/20260103T000100-20260103T000200/cpu_1/SERVICE-12345.log
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
- When no exact cycle covers the problem time, pick the nearest timed cycle and mark it approximate.
- Use `unknown` cycle only when no timed cycle can be selected.

## CLI Helpers

These commands are useful for quick checks. Use the repository root as the working directory.

```bash
python cli.py info <task_id> -o <output_dir>
python cli.py list-slots <task_id> -o <output_dir>
python cli.py query-diag <task_id> -s <slot_id> -o <output_dir>
python cli.py mech-slots <task_id> [-m <module_name>] -o <output_dir>
python cli.py mech-lifecycles <task_id> -s <slot_id> [-m <module_name>] -o <output_dir>
python cli.py mech-logs <task_id> -s <slot_id> -c <cycle_dir> -p <process_name-pid> [-m <module_name>] -o <output_dir>
```

`-m` uses `module_name`, not `module_key`, in the current CLI query service.

## Common Signals

- `errors[]` is non-empty: explain these before diagnosing missing or malformed output.
- A target process appears in `result.json` but the written `.log` file is missing: report an output consistency issue and use `logs[].raw` from JSON as fallback evidence.
- `missing_sequences` is non-empty: mention possible dropped or unparsed `No[n]` entries.
- Multiple slots have active periods around the same time: avoid assuming the active board without stronger mechanism evidence.
- `diag_entry_count` or `journal_entry_count` is zero: the mechanism may be diagnostic-only, journal-only, or misconfigured.
- `unknown` cycle: the mechanism could not map entries to a bounded lifecycle; do not overstate time correlation.
