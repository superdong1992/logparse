# logparse Rules Index

This file is the required rule entrypoint for repo analysis and code changes.
Run `.venv/bin/python scripts/rule_preflight.py --paths <files...>` before
touching code, or `.venv/bin/python scripts/rule_preflight.py --changed` before
reviewing a local diff. The preflight also reads
`governance/architecture-boundaries.toml`; an unclassified source path is red.

## governance:green

Green paths hold current-product topology, log-format adapters, and diagnosis
knowledge. `slot`, `CPU`, board and active/standby concepts belong here, not in
the product-neutral core. GLM5.1 may change green paths with focused tests and a
documented LAN validation scenario.

## governance:yellow

Yellow paths hold protected lifecycle and correlation policy. Modify them only
when a real LAN case proves the current rule wrong. Record the real case id, a
minimal fixture, historical-corpus regression, and schema impact.

## governance:red

Red paths hold frozen architecture, contracts, artifacts, security, CLI
compatibility, and the governance controls themselves. Modification requires an
Accepted ADR, explicit human approval, contract/security/smoke validation, and
a rollback plan. Editing the gate to bypass it is prohibited.

## rules:cpu-id-board

Source: `backend/parsing/mech_diag_scanner.py`,
`backend/parsing/mech_journal_scanner.py`, and
`backend/extensions/mechanisms/module2.py`.

Checklist:

- `CPU_Id=0 is board-level`.
- Empty `cpu_id` is board-level.
- Only non-zero CPU ids produce CPU-specific `cpu_<id>/...` output.
- Do not treat `slot_1_cpu_0` or `CPU_Id=0` as proof of a CPU-local lifecycle.

## rules:nested-cycle-output

Source: `backend/extensions/products/current/artifacts.py`,
`docs/architecture.md`, and
`.agents/skills/logparse-diagnose/references/preprocess-output.md`.

Checklist:

- Board cycles are the top-level lifecycle output.
- CPU cycles are nested under the matching board cycle.
- Board-level process logs live directly under the board cycle directory.
- CPU-cycle process logs live under `cpu_<id>/<cpu_cycle>/`.

## rules:module2-upstream-lifecycle

Source: `backend/extensions/mechanisms/module2.py`, `docs/architecture.md`, and
`.agents/skills/logparse-diagnose/references/relation-rules.md`.

Checklist:

- `module2` is diagnostic-only for lifecycle purposes.
- `module2` reuses `module1` lifecycle cycles.
- Module2 frame/slot diagnostic values such as `1/2` map to the last segment before matching module1 slots.
- `module2` may expand output bounds only within the nearest adjacent module1 gap.
- CPU anchors preserve the same parent board cycle and nested CPU cycle when available.

## rules:compact-result-contract

Source: `backend/extensions/products/current/result_serializer.py`,
`backend/query.py`, and `docs/architecture.md`.

Checklist:

- Compact `result.json` is a query index, not a raw log archive.
- Per-line `logs[]` and raw text are omitted from compact process summaries.
- New query-facing fields must survive serializer -> query -> CLI.
- Lifecycle issues live under `lifecycle_split_result.issues`.

## rules:scanner-decompression-boundary

Source: `docs/architecture.md`, `backend/decompressor.py`, and
`backend/extensions/products/current/scanner.py`.

Checklist:

- `Decompressor` owns archive extraction.
- Scanner plugins only inspect the already extracted workspace.
- Plain `.gz` logs are streamed by parsers unless debug expansion is enabled.

## rules:lifecycle-v3-config

Source: `backend/extensions/mechanisms/validation.py`,
`backend/extensions/mechanisms/module1.py`, and
`backend/domain/lifecycle/splitter_v3.py`.

Checklist:

- `Module1Plugin` always uses `LifecycleSplitterV3`.
- Current `lifecycle_split` supports only `process_name_mapping`, `reliable_processes`, and `multi_instance_processes`.
- `reliable_processes` and `multi_instance_processes` must be flat lists.
- Conflict checks happen after process name canonicalization and case folding.
- The final result algorithm is always `interval_v3`.

## rules:lifecycle-v3-output

Source: `backend/domain/lifecycle/splitter_v3.py`, `backend/result_serializer.py`,
`backend/query.py`, `cli.py`, and `docs/lifecycle-dfx-guide.md`.

Checklist:

- V3 output contains `candidate_segments`, `merge_decisions`, `lifecycles`, `journal_evidence`, `issues`, and `lifecycle_reliable`.
- `mech-lifecycles --show-boundaries` displays V3 DFX only.
- Legacy result files may be reported as unsupported; do not preserve detailed compatibility display.

## rules:artifact-contract

Source: `backend/contracts/artifacts.py`,
`backend/infrastructure/artifact_repository.py`, and `docs/architecture.md`.

Checklist:

- `parse_manifest.json` records run status, stages, versions, hashes, counters,
  diagnostics, and workspace retention.
- `metadata.json` is discovery/scan coverage; `result.json` is a compact query
  index; `mech_modules/` is selected evidence.
- Formal artifacts use one `ArtifactLayout` and atomic repository writes.
- Extraction is temporary workspace, not a formal artifact.
- Raw/context/per-line logs never appear in compact `result.json`.

## rules:deterministic-dfx-boundary

Source: `backend/dfx.py`, `docs/lan-dfx-operating-model.md`, and ADR-0004.

Checklist:

- Standalone logparse never invokes Claude CLI or GLM5.1.
- Target and lifecycle selection are deterministic before a model reads context.
- Summary output is one `ERROR_CODE: 中文结论` line without raw log text.
- Deep DFX is opt-in and bounded to 5 windows, 48 lines per window, and 80 KiB
  total, with a selection manifest.
- A missing or ambiguous target stays missing or ambiguous; do not substitute
  model exploration.
