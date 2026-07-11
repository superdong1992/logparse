# LAN-only DFX Operating Model

## Authority and Information Boundary

After the handoff, the LAN repository is the only authoritative logparse source.
The external checkout is a frozen historical reference. There is no LAN-to-
external synchronization of code, commits, diffs, fixtures, product
configuration, or logs.

Real logs and final diagnosis remain in the LAN. If an approved workflow needs a
human-readable conclusion outside that boundary, it is limited to:

```text
ERROR_CODE: 中文结论
```

The line must not contain raw log text, paths, private identifiers, or excerpts.

## logparse Responsibility

Standalone logparse is deterministic infrastructure. It:

- prepares a safe temporary workspace;
- discovers product logs through explicit extensions;
- builds versioned metadata, compact query indexes, and mechanism evidence;
- resolves lifecycle/target selection deterministically;
- emits stable error codes, structured DFX, and bounded context manifests.

It does not call Claude CLI, GLM5.1, or another model. Model availability must
never change parse, query, target selection, or DFX output.

## GLM5.1 Responsibility

Claude Code + GLM5.1 develops and diagnoses inside the LAN. For diagnosis,
GLM5.1 receives only deterministic, preselected inputs:

- `dfx_report.json`;
- `parse_manifest.json` when needed for artifact integrity;
- `target_logs` returned by `mech-target-logs`;
- small files explicitly listed in `dfx_context/manifest.json`.

Do not ask GLM5.1 to traverse an upload, search the whole output tree, select a
lifecycle, invent a path, or replace a missing target with related logs.

For code changes, GLM5.1 follows the green/yellow/red boundaries in
`governance/architecture-boundaries.toml`. Product topology such as slot/CPU is
green; lifecycle and Module1/Module2 policy is yellow; deterministic DFX,
artifact contracts, and the gate itself are red.

## Default and Deep DFX

Default DFX reads structured artifacts and file metadata, not log bodies. Deep
DFX is opt-in and runs only in the LAN:

- select targets deterministically before reading content;
- center windows on `problem_time` when a matching timestamp exists;
- otherwise use a documented deterministic fallback;
- allow at most 5 windows, 48 lines per window, and 80 KiB total;
- record relative path, line range, hash, selection reason, truncation, and
  caveats in `dfx_context/manifest.json`;
- do not create `dfx_context/` if no window is produced.

`dfx_summary.txt` remains one line and contains no raw evidence.

## issue-locator Compatibility

issue-locator remains the orchestration layer and may call these stable
entrypoints:

- `parse`
- `mech-target-logs`
- `dfx-output`
- `.agents/skills/logparse-diagnose/`

`result.zip` belongs to issue-locator, not logparse. logparse must not absorb
model orchestration or result packaging merely because both run inside the LAN.

## Failure Behavior

- Missing/invalid config, unsafe extraction, failed discovery, and artifact
  writes are fatal and produce structured diagnostics.
- A mechanism failure may allow independent mechanisms to continue; dependents
  are skipped explicitly.
- Missing performance data is normal when profiling is disabled.
- Missing or ambiguous target evidence remains missing/ambiguous; GLM5.1 does
  not improvise a replacement.
- Any model timeout, malformed response, or error-code mismatch falls back to
  deterministic `dfx_summary.txt`.
