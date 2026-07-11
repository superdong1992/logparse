# logparse Agent Instructions

The LAN checkout is the only authoritative repository after handoff. External
copies are frozen references: do not develop a second branch outside the LAN and
do not export LAN code, diffs, fixtures, configurations, or logs.

## Mandatory First Read

Before repository analysis or edits:

1. Read `CLAUDE.md` completely.
2. Run `.venv/bin/python scripts/rule_preflight.py --paths <planned-paths...>`.
3. Read every rule source returned by preflight.
4. Read `docs/lan-development-guide.md` for implementation work.
5. Read `docs/lan-dfx-operating-model.md` before changing diagnosis, query,
   DFX, CLI, or artifacts.

Use Python 3.12 from `.venv`; do not rely on bare `python`. On native Windows,
use `.venv\Scripts\python.exe`.

## Architecture Change Zones

`governance/architecture-boundaries.toml` is the machine-readable source of
truth. Unclassified source files are red by default.

- Green: product topology and format adapters, diagnosis knowledge, and their
  focused tests. `slot`, `CPU`, board, and active/standby are current-product
  concepts and belong here.
- Yellow: lifecycle and Module1/Module2 correlation policy. Change only from a
  real LAN case, with a minimal fixture and corpus regression.
- Red: contracts, orchestration, filesystem/security, artifact schemas, CLI
  compatibility, and governance. Do not modify by default.

Before handing off a change, run:

```bash
.venv/bin/python scripts/verify_delivery.py
.venv/bin/python scripts/change_gate.py --changed
.venv/bin/python scripts/change_gate.py --changed --enforce \
  --change-record governance/changes/<change-id>.yaml
```

Never weaken `change_gate.py`, `rule_preflight.py`, or the boundary TOML to make
a change pass.

## Runtime and Delivery Boundaries

- Standalone logparse is deterministic and never invokes Claude CLI or GLM5.1.
- GLM5.1 consumes structured DFX plus explicitly bounded context only.
- Preserve issue-locator compatibility for `parse`, `mech-target-logs`,
  `dfx-output`, root `cli.py`, and `.agents/skills/logparse-diagnose/`.
- Never add raw log bodies to `result.json`, summaries, or outbound handoffs.
- Do not touch `output/` or `outputs/` unless the user explicitly asks to inspect
  a named task. They may contain LAN-only evidence.
- Do not commit, push, tag, or copy material outside the LAN without explicit
  user authorization and the approved internal workflow.
