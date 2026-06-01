# logparse Rules Index

This file is the required rule entrypoint for repo analysis and code changes.
Run `python scripts/rule_preflight.py --paths <files...>` before touching code,
or `python scripts/rule_preflight.py --changed` before reviewing a local diff.

## rules:cpu-id-board

Source: `backend/parsing/mech_diag_scanner.py`,
`backend/parsing/mech_journal_scanner.py`, `backend/plugins/mechanisms/module2.py`,
and `docs/superpowers/plans/2026-05-26-module2-dependent-cycle-output.md`.

Checklist:

- `CPU_Id=0 is board-level`.
- Empty `cpu_id` is board-level.
- Only non-zero CPU ids produce nested `cpu_<id>/<cpu_cycle>/...` output.
- Do not treat `slot_1_cpu_0` or `CPU_Id=0` as proof of a CPU-local lifecycle.

## rules:nested-cycle-output

Source: `README.md`, `docs/lifecycle-split-logic.md`, and
`.agents/skills/logparse-diagnose/references/preprocess-output.md`.

Checklist:

- Board cycles are the top-level lifecycle output.
- CPU cycles are nested under the matching board cycle.
- Board-level process logs live directly under the board cycle directory.
- CPU process logs live under `cpu_<id>/<cpu_cycle>/`.

## rules:module2-upstream-lifecycle

Source: `README.md`, `docs/architecture.md`, and
`.agents/skills/logparse-diagnose/references/relation-rules.md`.

Checklist:

- `module2` is diagnostic-only for lifecycle purposes.
- `module2` reuses `module1` lifecycle cycles.
- CPU anchors preserve the same parent board cycle and nested CPU cycle when available.

## rules:compact-result-contract

Source: `backend/result_serializer.py`, `README.md`, and
`.agents/skills/logparse-diagnose/references/preprocess-output.md`.

Checklist:

- Compact `result.json` is a query index, not a raw log archive.
- Per-line `logs[]` and raw text are omitted from compact process summaries.
- New query-facing fields must survive serializer -> query -> CLI.

## rules:scanner-decompression-boundary

Source: `README.md`, `docs/architecture.md`, and `backend/plugins/default/scanner.py`.

Checklist:

- `Decompressor` owns archive extraction.
- Scanner plugins only inspect the already extracted workspace.
- Plain `.gz` logs are streamed by parsers unless debug expansion is enabled.

## rules:lifecycle-config

Source: `docs/lifecycle-split-v2-rules.md`,
`docs/lifecycle-split-v2-refactor-plan.md`, `backend/config_validation.py`,
and `backend/plugins/mechanisms/module1.py`.

Checklist:

- `enabled must be an explicit boolean true to enable v2`.
- Missing `lifecycle_split.enabled` keeps v2 disabled.
- `enabled: false` keeps the old `CycleDetector` path.
- `reliable_processes` is a flat list in the current config shape.
- Legacy `reliable_processes.board` / `reliable_processes.cpu` objects are accepted
  as compatibility input and merged.
- `reliable_processes` and `multi_instance_processes` must be disjoint after
  process name canonicalization and case folding.
- Conflict checks happen after process name canonicalization and case folding.

## rules:lifecycle-v2-split

Source: `docs/lifecycle-split-v2-rules.md` and
`docs/lifecycle-split-v2-refactor-plan.md`.

Checklist:

- Board evidence creates board-origin boundaries.
- CPU-local evidence creates CPU-origin boundaries and does not affect board origin.
- Inherited board boundaries participate in CPU effective boundaries.
- `wide_support` is top-level evidence only; it is not attached to one boundary.
- same-PID checks use final effective cycle indexes.
- reliable processes must not have multiple PIDs in the same final lifecycle cycle.
