---
name: logparse-develop
description: Develop, fix, refactor, or review logparse inside the LAN while enforcing green product, yellow policy, and red frozen-architecture boundaries. Use for any repository code, configuration, test, artifact, CLI, DFX, diagnosis-skill, or documentation change after the LAN-only handoff.
---

# Develop logparse

## Establish authority and scope

Work only in the authoritative LAN checkout. Do not send code, diffs, commits,
fixtures, configurations, or logs to the frozen external repository. Do not
inspect `output/` or `outputs/` unless the task names a specific LAN case.

Before repository analysis or edits:

1. Read `CLAUDE.md` completely.
2. Read `docs/lan-development-guide.md`.
3. Run `.venv/bin/python scripts/rule_preflight.py --paths <planned-paths...>`.
4. Read every source returned by preflight.
5. Run `.venv/bin/python scripts/change_gate.py --paths <planned-paths...>`.

Use Python 3.12 from `.venv`.

## Follow the reported zone

- Green: modify product topology/format adapters or diagnosis knowledge. Treat
  slot, CPU, board, and active/standby as current-product concepts. Add focused
  tests and record the LAN scenario.
- Yellow: modify lifecycle or Module1/Module2 correlation only when real LAN
  evidence proves the existing policy wrong. Record case id, minimal fixture,
  historical-corpus regression, and schema impact.
- Red: do not modify by default. Continue only when the user has explicitly
  approved an Accepted ADR, full contract/security/smoke validation, and a
  rollback plan.

Never reclassify a file or weaken the gate to make a change pass. Unknown source
paths are red.

## Implement in the owning layer

Keep generic architecture independent from current-product terms. Put product
topology, layouts, patterns, mappings, and compatibility projections in
`backend/extensions/products/`. Put protected mechanism behavior in
`backend/extensions/mechanisms/` or the applicable yellow domain package.
Do not infer permission from the parent directory: the current engine,
artifact writers, metadata/result serializers, Pipeline, query and DFX files
are explicit red exceptions listed by the boundary TOML.

Preserve these contracts:

- Decompressor owns extraction; scanners inspect prepared workspaces.
- Module1 owns V3 lifecycle; Module2 consumes it and does not split again.
- Empty CPU and CPU 0 are board-level current-product values.
- `result.json` is a compact query index without raw/context/per-line logs.
- Standalone logparse does not invoke Claude CLI or GLM5.1.
- `parse`, `mech-target-logs`, `dfx-output`, root `cli.py`, and
  `.agents/skills/logparse-diagnose/` remain issue-locator compatible.

Prefer the smallest owning extension over cross-layer cleanup. Keep legacy import
paths as thin façades; do not add new behavior to them.

For new mechanisms, use the native bounded
`execute(MechanismContext) -> MechanismOutcome` API. Never scaffold or add a new
`parse(ParseResult)` plugin; that mutable path exists only inside the explicit
pre-v1 compatibility adapter.

## Validate and record

Copy `governance/changes/change-record.template.yaml` to a uniquely named record
under `governance/changes/`. Never put raw log content in it.

Run focused tests first. Then run the suite required by the highest zone. For a
performance change, compare scanned files, lines, and mechanism entries as well
as elapsed time. For yellow changes, run the historical LAN corpus.

Before handoff, run:

```bash
.venv/bin/python scripts/rule_preflight.py --changed
.venv/bin/python scripts/change_gate.py --changed
.venv/bin/python scripts/change_gate.py --changed --enforce \
  --change-record governance/changes/<change-id>.yaml
```

Report changed paths, zone, tests, LAN corpus status, schema impact, and remaining
caveats. Do not commit, push, tag, or export without explicit authorization.
