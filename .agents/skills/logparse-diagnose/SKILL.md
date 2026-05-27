---
name: logparse-diagnose
description: Use this skill to locate issues from logparse preprocessed output when the user provides an approximate problem time plus one or more slot/process/PID anchors. It guides agents through reading metadata.json, result.json, and mech_modules/{module}/slot_{slot}/{cycle}/[cpu_x/]process[-pid].log, matching target processes strictly, selecting exact or nearest cycles, and expanding to mechanism-related logs.
---

# Logparse Diagnose

## Overview

Use logparse preprocessed results as an index into the original problem context. Treat each user-provided process as a target anchor, locate its module/slot/cycle/process log, then add mechanism-related logs from the rules in `references/relation-rules.md`.

Load `references/preprocess-output.md` when you need field meanings, path layout, or CLI command reminders. Load `references/relation-rules.md` before expanding beyond the target anchors.

## Required Inputs

Collect or infer these inputs:

- Task output directory: normally `output/{task_id}` or a directory containing `result.json`.
- Approximate problem time. Preserve the user's timezone wording if present.
- One or more target anchors. Each anchor may include `slot`, `process_name`, `pid`, optional `module`, and optional user label such as `client` or `server`.

If the task output directory or problem time is missing and cannot be inferred from the prompt or current files, ask for it. If an anchor has neither `process_name` nor `pid`, ask for at least one.

## Workflow

1. Find the preprocessed result root.
   - Prefer the directory that contains `result.json`.
   - If only an output base and task id are given, use `output/{task_id}/result.json`.
   - Read `metadata.json` when available for scanner coverage, original log paths, active periods, and parse errors.

2. Build a candidate index from `result.json`.
   - Iterate `mech_results[]`.
   - For each module, record both `module_key` and `module_name`.
   - For each slot, cycle, and process, keep `slot_id`, `dir_name`, `start_time`, `end_time`, `process_name`, `pid`, `missing_sequences`, and the process `logs[]`.
   - Derive the written log path from `mech_modules/{module_name}/slot_{slot_id}/{dir_name}/[cpu_{cpu_id}/]{process_name}[-pid].log`. Use the first log's non-empty `cpu_id` for the optional CPU directory. If the derived path is missing, search that cycle directory for the exact process filename.

3. Match each target anchor independently.
   - Slot must match when the user provides `slot`.
   - Module must match when the user provides `module`; accept either `module_key` or `module_name`.
   - If both `process_name` and `pid` are provided, both must match. Compare process names case-insensitively; compare PID strings exactly.
   - If only `process_name` is provided, match process name case-insensitively.
   - If only `pid` is provided, match PID exactly.
   - Keep multiple matches only when they are genuinely distinct candidates; report the ambiguity instead of silently choosing one by process alone.

4. Select the cycle for each matched anchor.
   - Exact hit: choose a cycle when `start_time <= problem_time <= end_time`.
   - Nearest hit: if no exact hit exists, choose the cycle with the smallest distance from `problem_time` to the cycle interval and mark it as approximate.
   - Unknown cycles: use `dir_name == "unknown"` only when no timed cycle can be selected for that anchor; mark it as not time-bounded.

5. Read the target process logs.
   - Read only the matched process log first.
   - When logs are large, extract a focused window around the problem time plus nearby lines that show errors, state transitions, PID changes, sequence gaps, requests, responses, or timeouts.
   - Preserve source markers like `[diagnostic|...]` and `[journal|...]`; they explain whether evidence came from diagnostic logs or private/journal logs.

6. Expand related logs.
   - Read `references/relation-rules.md`.
   - Apply rules per anchor, not globally. If multiple anchors are in different slots/modules, each anchor gets its own cycle selection.
   - Add mechanism-related logs only from the anchor's selected cycle unless a rule explicitly says otherwise.
   - For `module2`, include same-slot, same-cycle upstream `module1` context when available.

7. Correlate across anchors.
   - Preserve user labels such as `client` and `server`.
   - Align evidence by the shared problem time.
   - Compare each anchor's cycle, PID, sequence numbers, and source files.
   - Call out missing targets, approximate cycle hits, unknown cycles, missing sequences, parse errors, and absent related logs.

## Report Format

Return a concise report in this order:

1. Input conditions: task path, problem time, target anchors.
2. Anchor hits: for each anchor, list module, slot, cycle, exact/nearest status, process log path, and any ambiguity.
3. Related logs: list the extra logs included by relation rules and why each was included.
4. Cross-anchor evidence summary: time-ordered facts that connect the anchors.
5. Gaps and caveats: missing files, nearest-cycle fallback, unknown cycles, parse errors, sequence gaps, or insufficient input.
6. Next steps: specific logs, commands, or extra user inputs that would reduce uncertainty.

Avoid claiming root cause unless the preprocessed logs directly support it. Prefer evidence-backed wording such as "the strongest evidence points to..." or "this is suspicious because...".

## Useful Commands

Use normal shell and JSON tooling available in the current environment. These commands are optional helpers, not requirements:

```bash
python cli.py info <task_id> -o <output_dir>
python cli.py list-slots <task_id> -o <output_dir>
python cli.py mech-slots <task_id> -o <output_dir>
python cli.py mech-lifecycles <task_id> -s <slot_id> -m <module_name> -o <output_dir>
python cli.py mech-logs <task_id> -s <slot_id> -c <cycle_dir> -p <process_name-pid> -m <module_name> -o <output_dir>
```

Prefer direct JSON reads when you need exact matching or multi-anchor correlation; use CLI commands for quick human-readable checks.
