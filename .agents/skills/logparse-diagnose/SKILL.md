---
name: logparse-diagnose
description: Use when diagnosing logparse log packages or preprocessed output with approximate problem time plus slot/process/PID anchors, especially raw compressed archives, V3 lifecycle_split output, compact result.json indexes, mech_modules logs, strict process/PID matching, module1/module2 lifecycle correlation, and unknown or nearest-cycle caveats.
---

# Logparse Diagnose

## Overview

Use logparse preprocessed results as an index into the original problem context. If the user gives a raw compressed log package, run the repository parse pipeline first to create that preprocessed result. The primary deliverable is the log file that matches the user-specified module, slot, problem time, and process name; when the user specifies multiple processes, provide each matched log. Treat lifecycle_split V3 as the official lifecycle model: final lifecycle evidence comes from `lifecycle_split_result.algorithm == "interval_v3"` and the written process logs under `mech_modules/`.

Load `references/preprocess-output.md` when you need field meanings, path layout, or CLI reminders. Load `references/relation-rules.md` before expanding beyond target anchors, especially for module2.

## Python Execution

Run repository Python commands with Python 3.12. Use `python3.12` for CLI commands, helper scripts, dependency installation, and ad hoc JSON inspection; do not use bare `python` or rely on PATH defaults. If `python3.12` is unavailable on Windows, try `py -3.12`; if no Python 3.12 interpreter is available, stop and set up Python 3.12 before parsing or diagnosing.

When dependencies are missing, install them with the Python 3.12 interpreter, for example `python3.12 -m pip install -r requirements.txt`. If creating a virtual environment, create it from Python 3.12 first, for example `python3.12 -m venv .venv`, then use that environment's Python.

## Required Inputs

Collect or infer these inputs:

- Input path: either a raw log package such as `.zip`, `.tar`, `.tar.gz`, `.tgz`, or `.gz`, or a preprocessed result directory such as `output/{task_id}` containing `result.json`.
- Approximate problem time. Preserve the user's timezone wording if present.
- One or more target anchors. Each anchor should include `module`, `slot`, and `process_name`; `pid` and a user label such as `client` or `server` are optional.
- Optional parse settings when the input is a raw package: V3 config path, output directory, and whether verbose output is desired.

If the input path, problem time, module, slot, or process name is missing and cannot be inferred, ask for it. If a PID is supplied, use it as a strict additional match.

## Workflow

1. Resolve the input into a preprocessed result root.
   - Prefer the directory containing `result.json`.
   - If only an output base and task id are given, use `output/{task_id}/result.json`.
   - If the input is a raw package file or a directory without `result.json`, parse it first with `python3.12 cli.py parse <package_path> -c <v3_config_path> -o <output_dir>`.
   - Use parse settings from the prompt when provided. Otherwise use the repo-local profile that currently enables formal V3 lifecycle splitting. Do not infer lifecycle version from `cli.py` option defaults alone.
   - After parsing, verify the parsed result has V3 lifecycle payloads before diagnosing.
   - After parsing, derive `task_id` from the CLI output or package stem, then use `<output_dir>/<task_id>/result.json`.
   - Read `metadata.json` when available for scanner coverage, original paths, active periods, private slots, and parse errors.
   - If parsing reports errors and the requested module has no `result.json` entry or no `mech_modules/{module_name}/` logs, stop and report an error instead of continuing with fallback evidence.

2. Confirm V3 readiness.
   - Open `result.json` and find module1 slots with `lifecycle_split_result.algorithm == "interval_v3"`.
   - If V3 payloads are missing after a fresh parse, report the config/result mismatch before diagnosing. Do not silently treat V2 or CycleDetector output as official V3.
   - If the user explicitly asks to inspect legacy output, continue with a clear non-V3 caveat.

3. Resolve each target anchor with the deterministic CLI.
   - For each anchor, run `python3.12 cli.py mech-target-logs <task_id> --problem-time <problem_time> --module <module> --slot <slot> --process-name <process_name> [--pid <pid>] [--label <label>] -o <output_dir>`.
   - Do not manually choose lifecycle/cycle from `result.json`. The CLI owns slot normalization, module key/name matching, process/PID matching, exact/nearest/unknown lifecycle selection, and log-path construction.
   - Run one command per requested target anchor. If multiple anchors are supplied, concatenate the returned `target_logs[]` in the same order as the anchors.
   - If `mech-target-logs` returns `missing` or `ambiguous`, report that target as missing/ambiguous evidence. Do not substitute related logs.

4. Provide the target process logs.
   - For each user-specified process, provide the structured `target_logs` entry returned by `mech-target-logs` and the log content relevant to the requested time.
   - Each `target_logs` entry must include `label`, `module_key`, `module_name`, `slot`, `process_name`, optional `pid`, `match_status`, `log_path`, board cycle, CPU cycle when applicable, and `caveats`.
   - Treat `target_logs[*].log_path` as the handoff contract for generated diagnosis skills. Consumers must read only these paths for the requested target logs.
   - If the user specifies multiple processes, provide one matched log section per process in the same order as the request.
   - When logs are large, include a focused window around the problem time plus nearby lines showing errors, state transitions, PID changes, sequence gaps, requests, responses, or timeouts. Clearly mark the window as truncated if the whole file is not shown.
   - Preserve source markers such as `[diagnostic|...]` and `[journal|...]`; they explain whether evidence came from diagnostic logs or private/journal logs.
   - If a compact-result process summary has no written log file, report an output consistency error for that requested process and omit `log_path` for that failed target. Do not substitute related logs for the requested process log.

5. Interpret V3 lifecycle evidence.
   - Treat `lifecycles[]` as final V3 lifecycle segments.
   - Treat `candidate_segments[]` as provisional split candidates, not final proof of a lifecycle boundary.
   - Use `merge_decisions[]` to explain why adjacent candidates were merged or kept split; important `blocking_reason` values include journal wrap and reliable PID conflict.
   - Use `journal_evidence[]` to connect sequence wraps or journal signals to candidate boundaries.
   - Use `issues[]` and `lifecycle_reliable=false` to caveat unsafe or incomplete V3 evidence.
   - Use legacy `boundary_issues[]` or CycleDetector wording only when no V3 payload exists in the result.

6. Expand related logs.
   - Read `references/relation-rules.md`.
   - Expand related logs only after all requested process logs have been found, or when the user explicitly asks for broader diagnosis.
   - Apply rules per anchor, not globally. If multiple anchors are in different slots/modules, each anchor gets its own cycle selection.
   - Add related logs only from the anchor's selected board cycle and selected CPU cycle when applicable, unless a rule explicitly says otherwise.
   - For module2, use relation rules to map derived module2 output back to module1 V3 lifecycle context by slot, CPU, problem time, PID, and process evidence.

7. Correlate across anchors.
   - Preserve user labels such as `client`, `server`, `active`, or `standby`.
   - Align evidence by the shared problem time.
   - Compare each anchor's module, V3 lifecycle, cycle, PID, sequence numbers, and source files.
   - Call out missing targets, approximate hits, unknown cycles, V3 reliability issues, missing sequences, parse errors, and absent related logs.

## Output Contract

If the requested module was not parsed, parsing failed in a way that prevents module output, or a requested process log cannot be found, return an error with the missing module/process and the relevant parse or output evidence.

Otherwise, return a concise result in this order:

1. Input conditions: task path, problem time, target anchors.
2. `target_logs`: for each requested process, return one structured entry in the same order as the anchors. Use this shape exactly enough for downstream generated diagnosis skills to copy and read paths without searching:

```yaml
target_logs:
  - label: client
    module_key: module1
    module_name: MODULE_NAME
    slot: "1"
    process_name: PROCESS
    pid: "1234"        # omit when the anchor did not provide one
    match_status: exact | nearest | unknown | missing | ambiguous
    log_path: D:/path/to/output/task/mech_modules/MODULE_NAME/slot_1/.../PROCESS-1234.log
    board_cycle: 20260103T000100-20260103T000200
    cpu_cycle: null    # use null when not applicable
    caveats:
      - nearest-cycle fallback, V3 issue, truncation, or parse caveat
```

If `match_status` is `missing` or `ambiguous`, explain the failure evidence and leave `log_path` absent. A generated diagnosis skill must treat absent `log_path` as missing required evidence.
3. Matched log content: for each `target_logs` entry that has `log_path`, include the requested log content or a clearly marked focused window.
4. V3 context: lifecycle/candidate/merge-decision context needed to explain why that log was selected.
5. Gaps and caveats: V3 issues, nearest-cycle fallback, unknown cycles, parse errors, sequence gaps, truncation, or ambiguity.
6. Optional diagnosis: only include related logs or cross-anchor interpretation when the user asked for diagnosis beyond retrieving the target logs.

Avoid claiming root cause unless the preprocessed logs directly support it. Prefer evidence-backed wording such as "the strongest evidence points to..." or "this is suspicious because...".

Generated diagnosis skills consume the `target_logs` block as the only target-log handoff. They must not traverse `output/`, recompute lifecycle or cycle selection, rebuild log paths, or replace a missing target log with a related log.

## Useful Commands

Use normal shell and JSON tooling available in the current environment. These commands are optional helpers, not requirements:

```bash
python3.12 cli.py info <task_id> -o <output_dir>
python3.12 cli.py parse <package_path> -c <v3_config_path> -o <output_dir>
python3.12 cli.py parse <package_path> -c <v3_config_path> -o <output_dir> --verbose
python3.12 cli.py list-slots <task_id> -o <output_dir>
python3.12 cli.py mech-slots <task_id> -o <output_dir>
python3.12 cli.py mech-target-logs <task_id> --problem-time <ISO_TIME> --module <module_key_or_name> --slot <slot_id> --process-name <process_name> --pid <pid> --label <label> -o <output_dir>
python3.12 cli.py mech-lifecycles <task_id> -s <slot_id> -m <module_name> --show-boundaries --lifecycle-dfx decisions -o <output_dir>
python3.12 cli.py mech-lifecycles <task_id> -s <slot_id> -m <module_name> --show-boundaries --lifecycle-dfx full -o <output_dir>
python3.12 cli.py mech-logs <task_id> -s <slot_id> -c <board_cycle_dir> -p <process_name-pid> -m <module_name> --cpu <cpu_id> --cpu-cycle <cpu_cycle_dir> -o <output_dir>
```

Prefer direct JSON reads for exact matching and multi-anchor correlation; use CLI commands for quick human-readable checks.
