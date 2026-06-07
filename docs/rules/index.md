# logparse Rules Index

This file is the required rule entrypoint for repo analysis and code changes.
Run `python scripts/rule_preflight.py --paths <files...>` before touching code,
or `python scripts/rule_preflight.py --changed` before reviewing a local diff.

## rules:cpu-id-board

Source: `backend/parsing/mech_diag_scanner.py`,
`backend/parsing/mech_journal_scanner.py`, and `backend/plugins/mechanisms/module2.py`.

Checklist:

- `CPU_Id=0 is board-level`.
- Empty `cpu_id` is board-level.
- Only non-zero CPU ids produce CPU-specific `cpu_<id>/...` output.
- Do not treat `slot_1_cpu_0` or `CPU_Id=0` as proof of a CPU-local lifecycle.

## rules:nested-cycle-output

Source: `README.md`, `docs/architecture.md`, and `.agents/skills/logparse-diagnose/references/preprocess-output.md`.

Checklist:

- Board cycles are the top-level lifecycle output.
- CPU cycles are nested under the matching board cycle.
- Board-level process logs live directly under the board cycle directory.
- CPU-cycle process logs live under `cpu_<id>/<cpu_cycle>/`.

## rules:module2-upstream-lifecycle

Source: `README.md`, `docs/architecture.md`, and `.agents/skills/logparse-diagnose/references/relation-rules.md`.

Checklist:

- `module2` is diagnostic-only for lifecycle purposes.
- `module2` reuses `module1` lifecycle cycles.
- `module2` may expand output bounds only within the nearest adjacent module1 gap.
- CPU anchors preserve the same parent board cycle and nested CPU cycle when available.

## rules:compact-result-contract

Source: `backend/result_serializer.py`, `backend/query.py`, and `README.md`.

Checklist:

- Compact `result.json` is a query index, not a raw log archive.
- Per-line `logs[]` and raw text are omitted from compact process summaries.
- New query-facing fields must survive serializer -> query -> CLI.
- Lifecycle issues live under `lifecycle_split_result.issues`.

## rules:scanner-decompression-boundary

Source: `README.md`, `docs/architecture.md`, and `backend/plugins/default/scanner.py`.

Checklist:

- `Decompressor` owns archive extraction.
- Scanner plugins only inspect the already extracted workspace.
- Plain `.gz` logs are streamed by parsers unless debug expansion is enabled.

## rules:lifecycle-v3-config

Source: `backend/config_validation.py`, `backend/plugins/mechanisms/module1.py`, and `backend/parsing/lifecycle_splitter_v3.py`.

Checklist:

- `Module1Plugin` always uses `LifecycleSplitterV3`.
- Current `lifecycle_split` supports only `process_name_mapping`, `reliable_processes`, and `multi_instance_processes`.
- `reliable_processes` and `multi_instance_processes` must be flat lists.
- Conflict checks happen after process name canonicalization and case folding.
- The final result algorithm is always `interval_v3`.

## rules:lifecycle-v3-output

Source: `backend/result_serializer.py`, `backend/query.py`, `cli.py`, and `docs/lifecycle-dfx-guide.md`.

Checklist:

- V3 output contains `candidate_segments`, `merge_decisions`, `lifecycles`, `journal_evidence`, `issues`, and `lifecycle_reliable`.
- `mech-lifecycles --show-boundaries` displays V3 DFX only.
- Legacy result files may be reported as unsupported; do not preserve detailed compatibility display.
