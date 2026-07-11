# logparse LAN Development Contract

This file is the mandatory entrypoint for Claude Code and GLM5.1. After the
handoff, the LAN checkout is the only authoritative source. External copies are
frozen and receive no code, diff, fixture, configuration, or log synchronization
from the LAN.

## 1. Mandatory Preflight

Before reading implementation files or editing anything:

```bash
.venv/bin/python scripts/rule_preflight.py --paths <files-you-will-touch...>
```

Read every returned rule source. Before completion, run:

```bash
.venv/bin/python scripts/rule_preflight.py --changed
.venv/bin/python scripts/verify_delivery.py
.venv/bin/python scripts/change_gate.py --changed
.venv/bin/python scripts/change_gate.py --changed --enforce \
  --change-record governance/changes/<change-id>.yaml
```

Copy `governance/changes/change-record.template.yaml` for the record. Never edit
the gate, boundary config, tests, or record dishonestly to bypass a requirement.

## 2. Modification Zones

The exact paths are defined in `governance/architecture-boundaries.toml`.
Precedence is red, then yellow, then green; an unclassified source path is red.

### Green — LAN business extension

GLM5.1 may modify these paths for normal product work with focused tests and a
documented LAN scenario:

```text
configs/products/**
backend/extensions/products/**
backend/extensions/diagnosis/**
tests/extensions/**
.agents/skills/diagnose-*/**
```

Current-product knowledge belongs here: `slot`, `CPU`, board topology,
active/standby role, directory layout, filename/glob/regex/keywords, process
mapping, and diagnosis knowledge. These concepts must not leak into the generic
architecture.

### Yellow — protected business policy

Change only when a real LAN case proves the current policy wrong:

```text
backend/domain/lifecycle/**
backend/domain/correlation/**
backend/extensions/mechanisms/**
.agents/skills/logparse-diagnose/**
```

The change record must include the real case id, minimal fixture, historical
corpus result, and schema-impact conclusion. This zone owns lifecycle splitting,
Module1/Module2 correlation, PID/time fallback, unknown assignment, midpoint,
range expansion, and clamp rules.

Plugin base classes and execution orchestration are red even when a compatibility
façade sits inside a mechanism or product directory; the TOML's red precedence
decides. Current-product engine/artifact writers, metadata/result schemas,
legacy Pipeline, query compatibility contract, and deterministic DFX are
explicit red exceptions under `backend/extensions/products/current/`; their
failure semantics, issue-locator behavior, schemas/error codes and DFX budgets
are not routine product rules. See the TOML for the exact filenames.

### Red — frozen architecture

Do not modify by default:

```text
backend/contracts/**
backend/ports/**
backend/application/**
backend/infrastructure/**
backend/presentation/**
governance/**
scripts/change_gate.py
scripts/rule_preflight.py
cli.py
```

A red change requires an Accepted ADR, explicit human approval, contract,
security, and smoke validation, plus a rollback plan. Governance controls are
red themselves and have a non-downgradable fallback in `change_gate.py`.

## 3. Architecture Contract

The processing direction is:

```text
raw package
-> product topology and format adapter
-> product-neutral contracts and scopes
-> protected lifecycle/correlation policy
-> artifact repository and deterministic query/DFX
-> bounded diagnosis context for GLM5.1
```

Dependency direction:

```text
presentation -> application -> ports/contracts
infrastructure -> ports/contracts
green/yellow business code -> public contracts and application services
yellow lifecycle/correlation policy -> green current-product models
```

Red contracts/application/infrastructure must not import green or yellow product
implementations. The red core uses generic source, event, scope, result, and
diagnostic concepts. Green adapters project the current slot/board/CPU shape;
yellow domain/mechanism code owns how that topology participates in lifecycle
and correlation.

Stable boundaries:

- Decompressor alone owns extraction and path-safety enforcement.
- Scanners inspect a prepared workspace; ordinary `.gz` logs are streamed.
- Plugins declare API version and dependencies; Module2 depends explicitly on
  Module1 and never creates an independent lifecycle.
- Native mechanisms receive bounded `MechanismContext` and return
  `MechanismOutcome`; only pre-v1 plugins may use the explicit
  `LegacyMechanismContext` adapter.
- Artifact paths come from one `ArtifactLayout`; writes are atomic.
- `metadata.json` is scan coverage, `result.json` is a compact query index, and
  `mech_modules/` is evidence. Do not merge their responsibilities.
- Preserve issue-locator entrypoints: `parse`, `mech-target-logs`, `dfx-output`,
  root `cli.py`, and `.agents/skills/logparse-diagnose/`.

Read `docs/architecture.md` for the full design and ADRs under `docs/adr/` for
the decisions that may not be silently reversed.

## 4. Lifecycle and Product Rules

Lifecycle splitting remains V3-only:

- `CPU_Id=0` and empty CPU id are board-level current-product values.
- Only non-zero CPU ids create nested CPU lifecycles.
- Module1 always uses `LifecycleSplitterV3` / `interval_v3`.
- Module2 reuses Module1 board and nested CPU lifecycle context.
- V3 output includes `candidate_segments`, `merge_decisions`, `lifecycles`,
  `journal_evidence`, `issues`, and `lifecycle_reliable`.
- Do not restore lifecycle V2 compatibility; archived material is under
  `docs/archive/lifecycle-v2/`.

These are current-product policies, not generic architecture. Their runtime
behavior may change only through the yellow-zone evidence workflow.

## 5. Artifact and DFX Rules

The formal task artifacts are:

```text
parse_manifest.json
metadata.json
result.json
mech_modules/
performance.json      # only with profile
dfx_report.json       # only after dfx-output
dfx_summary.txt       # only after dfx-output
dfx_context/          # only when deep DFX produces bounded windows
```

`extracted/` is temporary workspace, not a formal artifact. Never put raw,
context, or per-line `logs[]` into compact `result.json`. DFX summaries remain a
single `ERROR_CODE: 中文结论` line without raw log text. Standalone logparse
must not invoke Claude CLI or GLM5.1; see `docs/lan-dfx-operating-model.md`.

## 6. Testing

Use Python 3.12. On native Windows, replace `.venv/bin/python` with
`.venv\Scripts\python.exe`:

```bash
.venv/bin/python cli.py check-config -c config.yaml
.venv/bin/python cli.py doctor -c config.yaml --json
.venv/bin/python cli.py explain-config -c config.yaml -p default --json
.venv/bin/python cli.py migrate-config -c legacy-v1.yaml -o config-v2.yaml
.venv/bin/python cli.py artifact-check output/<task_id> --json
.venv/bin/python cli.py scaffold-extension --kind product --name <name>
.venv/bin/python cli.py scaffold-extension --kind mechanism --name <name>
.venv/bin/python scripts/verify_delivery.py
.venv/bin/python -m pytest tests -q --basetemp /tmp/logparse-pytest -p no:cacheprovider
.venv/bin/python cli.py parse tests/mock_data/diagnostic_information_20260103.zip \
  -c config.yaml --product default -o output/smoke-default
.venv/bin/python cli.py parse tests/mock_data_compact/compact_package_20260103.zip \
  -c config.yaml --product compact -o output/smoke-compact
```

For a focused change, run the closest tests first, then the full suite required
by the zone. Real-log policy changes also require LAN corpus comparison. Never
claim a performance improvement without matching scanned files, lines, and
mechanism entry counts; omitting input is not an optimization.
