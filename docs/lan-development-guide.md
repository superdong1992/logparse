# LAN Development Guide

## Start Every Change

Use the LAN checkout and Python 3.12 virtual environment. Commands below use
the POSIX/WSL path `.venv/bin/python`; on native Windows use
`.venv\Scripts\python.exe` (create it with `py -3.12 -m venv .venv`).

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

`.python-version` and the exact runtime/development pins are the handoff
environment. Dependency upgrades are red changes and require the same delivery
verification as other architecture changes.

```bash
git status --short
.venv/bin/python scripts/rule_preflight.py --paths <planned-paths...>
.venv/bin/python scripts/change_gate.py --paths <planned-paths...>
```

Read every rule returned by preflight. Copy
`governance/changes/change-record.template.yaml` to a uniquely named YAML file
under `governance/changes/`. Do not include raw logs or private content in the
record.

## Choose the Owning Zone

### Green: normal product work

Use green for product topology and format knowledge: slot/CPU/board/role,
directories, filenames, regular expressions, keywords, process mapping, and
diagnosis knowledge. Add or update focused tests and describe the LAN scenario
in the change record.

Do not move a generic contract or artifact rule into green merely to avoid red
review.

### Yellow: evidence-driven policy work

Use yellow for lifecycle and correlation behavior, including Module1/Module2,
PID/time fallback, midpoint, unknown assignment, expansion, and clamp. Before
editing, capture:

- a non-sensitive LAN case id;
- a minimal synthetic or approved sanitized fixture;
- expected behavior before and after;
- historical-corpus comparison;
- schema-impact conclusion.

Keep the real logs in the LAN evidence system; do not paste them into Git.

### Red: stop for architecture approval

Red covers contracts, orchestration, extraction/security, artifact layout and
schema, query/CLI compatibility, DFX budgets, and governance. Do not implement a
red change until the owner accepts an ADR. Record approval, full contract,
security and smoke validation, and a concrete rollback. The gate verifies that
each referenced ADR exists under `docs/adr/` and has `Status: Accepted`.

The gate and boundary TOML are red. Never change them to reclassify the task you
are currently trying to land. Critical governance paths have a hard-coded red
fallback in the gate and cannot be downgraded by TOML alone.

## Implement Safely

1. Modify the smallest owning extension or policy.
2. Keep product concepts out of contracts/application/infrastructure.
3. Preserve stable issue-locator entrypoints and compatibility projections.
4. Add a focused regression before broad cleanup.
5. Run focused tests, then the zone-required suite.
6. Inspect artifact shape when query, serializer, CLI, or DFX behavior changes.
7. Run preflight and change gate over the final worktree.

Typical commands:

```bash
.venv/bin/python -m pytest <focused-tests> -q \
  --basetemp /tmp/logparse-focused -p no:cacheprovider
.venv/bin/python -m pytest tests -q \
  --basetemp /tmp/logparse-full -p no:cacheprovider
.venv/bin/python scripts/verify_delivery.py
.venv/bin/python scripts/rule_preflight.py --changed
.venv/bin/python scripts/change_gate.py --changed
.venv/bin/python scripts/change_gate.py --changed --enforce \
  --change-record governance/changes/<change-id>.yaml
```

Use `--base <revision> --head <revision>` instead of `--changed` when validating
a commit range. `--head` defaults to `HEAD` when omitted.

## Self-service Inspection

Use deterministic commands before asking GLM5.1 to infer repository state:

```bash
.venv/bin/python cli.py doctor -c config.yaml --json
.venv/bin/python cli.py explain-config -c config.yaml -p default --json
.venv/bin/python cli.py migrate-config -c legacy-v1.yaml -o config-v2.yaml
.venv/bin/python cli.py artifact-check output/<task_id> --json
.venv/bin/python cli.py scaffold-extension --kind product --name <name>
.venv/bin/python cli.py scaffold-extension --kind mechanism --name <name>
```

`doctor` checks Python, dependencies, config, plugin graph, and optional output
root. `explain-config` shows the effective schema v2 configuration and mechanism
order. `migrate-config` writes a deterministic v2 file from a legacy source;
when given a v2 root it preserves product `$include` references instead of
inlining green configuration into red. `artifact-check` is
read-only and verifies manifest/schema/hash/index/evidence consistency.
`scaffold-extension` creates a green product or yellow mechanism skeleton plus
configuration and focused tests. Mechanism scaffolds use the native bounded
`execute(context) -> MechanismOutcome` API; do not replace it with mutable
`parse(result)`. Classify generated paths before filling them with LAN rules.

## Validation by Zone

| Highest zone | Required evidence |
| --- | --- |
| Green | Focused tests and LAN scenario |
| Yellow | Green requirements + case id, fixture, corpus regression, schema impact |
| Red | Green requirements + Accepted ADR, approval, rollback, contract/security/smoke tests |

For performance changes, compare elapsed time, scanned file count, line count,
and mechanism entry count. A faster run that scans less input is a regression
unless the omission is the explicitly approved behavior. Keep each raw
`performance.json` and its manifest in the LAN task directory; record the
baseline/candidate revision, Python and machine, run counts, medians, delta,
scan counters, representative artifact hashes and verdict under
`validation.performance` in the change record. For a real 2GB package, Git
receives only the non-sensitive case id and aggregate values.

## Real-log Diagnosis

Use `.agents/skills/logparse-diagnose/` and deterministic `mech-target-logs`.
GLM5.1 reads only returned target paths and bounded DFX context. Do not traverse
`output/` or `outputs/` broadly, and never add real logs to fixtures or change
records.

## Complete the Change

- Ensure the record covers every governed changed path and declares every zone.
- Confirm generated JSON contains no raw/context/per-line log fields.
- Record real-corpus results for yellow behavior.
- Keep commits and remotes inside the approved LAN repository workflow.
- Do not send patches, bundles, fixtures, or code back to the frozen external
  checkout.
