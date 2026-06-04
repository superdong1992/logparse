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

3. Build a compact result index.
   - Iterate `mech_results[]`; record both `module_key` and `module_name`.
   - For each slot, keep `slot_id`, `lifecycle_reliable`, `lifecycle_split_result`, and legacy `boundary_issues` when present.
   - For V3 slots, use `lifecycle_split_result.algorithm == "interval_v3"` and keep `candidate_segments`, `merge_decisions`, `lifecycles`, `journal_evidence`, `issues`, and `lifecycle_reliable`.
   - For each board cycle, record `dir_name`, `start_time`, `end_time`, board-level `processes[]`, and nested `cpu_cycles[]`.
   - For each process summary, keep `process_name`, `pid`, `total_count`, `missing_sequences`, and the parent board/CPU cycle fields. Compact `result.json` does not contain raw per-line process logs.
   - Derive written log paths from `mech_modules/{module_name}/slot_{slot_id}/...`; see `references/preprocess-output.md` for board-level, board-level CPU, and nested CPU-cycle path forms.
   - If the user specified a module and that module is absent from both `result.json` and `mech_modules/`, report that the specified module was not parsed and stop.

4. Match each target anchor independently.
   - Slot must match when the user provides `slot`.
   - Module must match when provided; accept either `module_key` or `module_name`.
   - If both `process_name` and `pid` are provided, both must match. Compare process names case-insensitively; compare PID strings exactly.
   - If only `process_name` is provided, match process name case-insensitively.
   - If only `pid` is provided, match PID exactly.
   - Keep multiple matches only when genuinely distinct; report ambiguity instead of choosing by process name alone.

5. Select the cycle for each matched anchor.
   - For board-level processes, select by the parent board cycle interval.
   - For nested CPU processes, select by the CPU cycle interval first, while preserving the parent board cycle.
   - Exact hit: choose a cycle when `start_time <= problem_time <= end_time`.
   - Nearest hit: if no exact hit exists, choose the cycle with the smallest distance from `problem_time` to the relevant interval and mark it approximate.
   - Unknown cycles: use `dir_name == "unknown"` only when no timed board/CPU cycle can be selected; mark it not time-bounded.
   - For module2, preserve that cycle names may be derived from PID/time routing, unknown nearest-time resolution, projected or expanded targets, or bounds expansion/clamp. Do not assume a module2 cycle directory is identical to the upstream module1 V3 lifecycle directory.

6. Provide the target process logs.
   - For each user-specified process, provide the matched process `.log` path and the log content relevant to the requested time.
   - If the user specifies multiple processes, provide one matched log section per process in the same order as the request.
   - When logs are large, include a focused window around the problem time plus nearby lines showing errors, state transitions, PID changes, sequence gaps, requests, responses, or timeouts. Clearly mark the window as truncated if the whole file is not shown.
   - Preserve source markers such as `[diagnostic|...]` and `[journal|...]`; they explain whether evidence came from diagnostic logs or private/journal logs.
   - If a compact-result process summary has no written log file, report an output consistency error for that requested process. Do not substitute related logs for the requested process log.

7. Interpret V3 lifecycle evidence.
   - Treat `lifecycles[]` as final V3 lifecycle segments.
   - Treat `candidate_segments[]` as provisional split candidates, not final proof of a lifecycle boundary.
   - Use `merge_decisions[]` to explain why adjacent candidates were merged or kept split; important `blocking_reason` values include journal wrap and reliable PID conflict.
   - Use `journal_evidence[]` to connect sequence wraps or journal signals to candidate boundaries.
   - Use `issues[]` and `lifecycle_reliable=false` to caveat unsafe or incomplete V3 evidence.
   - Use legacy `boundary_issues[]` or CycleDetector wording only when no V3 payload exists in the result.

8. Expand related logs.
   - Read `references/relation-rules.md`.
   - Expand related logs only after all requested process logs have been found, or when the user explicitly asks for broader diagnosis.
   - Apply rules per anchor, not globally. If multiple anchors are in different slots/modules, each anchor gets its own cycle selection.
   - Add related logs only from the anchor's selected board cycle and selected CPU cycle when applicable, unless a rule explicitly says otherwise.
   - For module2, use relation rules to map derived module2 output back to module1 V3 lifecycle context by slot, CPU, problem time, PID, and process evidence.

9. Correlate across anchors.
   - Preserve user labels such as `client`, `server`, `active`, or `standby`.
   - Align evidence by the shared problem time.
   - Compare each anchor's module, V3 lifecycle, cycle, PID, sequence numbers, and source files.
   - Call out missing targets, approximate hits, unknown cycles, V3 reliability issues, missing sequences, parse errors, and absent related logs.

## Output Contract

If the requested module was not parsed, parsing failed in a way that prevents module output, or a requested process log cannot be found, return an error with the missing module/process and the relevant parse or output evidence.

Otherwise, return a concise result in this order:

1. Input conditions: task path, problem time, target anchors.
2. Matched logs: for each requested process, list module, slot, board cycle, CPU cycle when applicable, exact/nearest status, log path, and the requested log content or clearly marked focused window.
3. V3 context: lifecycle/candidate/merge-decision context needed to explain why that log was selected.
4. Gaps and caveats: V3 issues, nearest-cycle fallback, unknown cycles, parse errors, sequence gaps, truncation, or ambiguity.
5. Optional diagnosis: only include related logs or cross-anchor interpretation when the user asked for diagnosis beyond retrieving the target logs.

Avoid claiming root cause unless the preprocessed logs directly support it. Prefer evidence-backed wording such as "the strongest evidence points to..." or "this is suspicious because...".

## Useful Commands

Use normal shell and JSON tooling available in the current environment. These commands are optional helpers, not requirements:

```bash
python3.12 cli.py info <task_id> -o <output_dir>
python3.12 cli.py parse <package_path> -c <v3_config_path> -o <output_dir>
python3.12 cli.py parse <package_path> -c <v3_config_path> -o <output_dir> --verbose
python3.12 cli.py list-slots <task_id> -o <output_dir>
python3.12 cli.py mech-slots <task_id> -o <output_dir>
python3.12 cli.py mech-lifecycles <task_id> -s <slot_id> -m <module_name> --show-boundaries --lifecycle-dfx decisions -o <output_dir>
python3.12 cli.py mech-lifecycles <task_id> -s <slot_id> -m <module_name> --show-boundaries --lifecycle-dfx full -o <output_dir>
python3.12 cli.py mech-logs <task_id> -s <slot_id> -c <board_cycle_dir> -p <process_name-pid> -m <module_name> --cpu <cpu_id> --cpu-cycle <cpu_cycle_dir> -o <output_dir>
```

Prefer direct JSON reads for exact matching and multi-anchor correlation; use CLI commands for quick human-readable checks.
