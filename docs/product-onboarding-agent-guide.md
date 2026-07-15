# Product Log Onboarding Agent Guide

This is the framework-neutral workflow for an AI agent that needs to derive a
new product's preprocessing configuration candidate from one or more log
files.  It is not a Codex skill and does not depend on a specific agent SDK.

The logparse command is deterministic.  It analyzes bounded samples, validates
a candidate supplied by the agent, and builds a non-final JSON draft.  It does
not generate the candidate by invoking a model and it never writes product
YAML.

The examples below use the mandatory Windows repository interpreter,
`.venv\Scripts\python.exe`.  On POSIX, replace it with `.venv/bin/python`.

## Hard Rules

1. Read only the log files explicitly supplied by the user.  Do not traverse a
   directory, inspect `output/` or `outputs/`, substitute related files, or pass
   an archive container.
2. Do not copy raw log bodies, candidate bodies, absolute paths, or private
   identifiers into a report, summary, or external handoff.
3. Infer only file discovery, timestamp syntax, a module marker, the required
   diagnostic captures, and an optional sequence capture.
4. Do not infer lifecycle, active/standby role, CPU/topology meaning, empty or
   zero CPU behavior, PID meaning, numeric process-suffix meaning, active-period
   thresholds, or correlation policy.
5. Never write YAML, modify `config.yaml`, enable a product, or treat
   `final_config_ready: false` as approval.
6. Exit code `4` is a normal mandatory pause.  List every `unresolved` item for
   the user and remain paused until explicit confirmation or LAN evidence is
   provided.  Do not apply defaults, retry around the pause, or time it out.

## 1. Analyze Explicit Files

Repeat `--input` for every user-supplied plain or gzip log file:

```powershell
.venv\Scripts\python.exe cli.py product-onboarding analyze `
  --input D:\samples\device_001.log `
  --input D:\samples\device_002.log
```

The command writes one JSON object to stdout.  Read `analysis.files`,
`analysis.format_families`, `analysis.timestamp_candidates`,
`analysis.candidate_hints`, `analysis.unresolved`, and
`analysis.extension_requirements`.  Reports contain aggregate evidence and
basenames only; they do not contain sampled lines.

If the status is `needs_adapter`, stop and explain the listed adapter
requirements.  Do not force a candidate through validation.

## 2. Create Candidate JSON

After bounded inspection of the same explicit files, create one temporary JSON
document with this schema:

```json
{
  "schema_version": 1,
  "adapter": "current_module1",
  "file_patterns": ["device_*.log"],
  "timestamp_regex": "(\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2})()",
  "module_name": "MODULE",
  "diag_pattern": "Slot=(?P<Slot>[^;]+);\\s*CPU-Id=(?P<CPU_Id>[^;]*);\\s*ProcessName=(?P<ProcessName>[^;]+);\\s*Context=(?P<Context>[^;]+)",
  "sequence_pattern": "No\\[(\\d+)\\]"
}
```

`file_patterns`, `timestamp_regex`, `module_name`, and `diag_pattern` are
required.  `sequence_pattern` is optional.  For this adapter the diagnostic
pattern must define exactly usable named captures for `Slot`, `CPU_Id`,
`ProcessName`, and `Context`.  The timestamp pattern must expose timestamp and
timezone as capture groups 1 and 2; an empty second group is valid when the log
has no timezone.

Do not add lifecycle or topology decisions to this document.

## 3. Validate the Candidate

```powershell
.venv\Scripts\python.exe cli.py product-onboarding validate `
  --input D:\samples\device_001.log `
  --input D:\samples\device_002.log `
  --candidate D:\samples\candidate.json
```

Interpret the result by exit code:

- `0`: this validation stage succeeded.  `final_config_ready` is still false.
- `2`: fix the CLI input or candidate document envelope.
- `3`: revise the technical candidate using `diagnostics` and `validation`;
  do not weaken the regex or input safety checks.

Validation replays the regex only against the bounded in-process sample in an
isolated worker.  It does not prove full-corpus performance.

## 4. Build the Non-final Draft

```powershell
.venv\Scripts\python.exe cli.py product-onboarding build-draft `
  --input D:\samples\device_001.log `
  --input D:\samples\device_002.log `
  --candidate D:\samples\candidate.json
```

- Exit `3` means no draft was built because technical validation failed.
- Exit `4` means `draft_product_config` is technically usable but
  `must_not_persist` is true.  Present `unresolved` and `runtime_caveats` to the
  user, then wait.

The draft is a schema-v2 product fragment only.  A later, separately governed
change may create `configs/products/<name>.yaml`, run real LAN corpus
regression, and request approval to add the product to the red root config.

## Machine Contract

Every handled result is one compact JSON document.  The common envelope is:

```json
{
  "contract": "logparse.product_onboarding.report",
  "schema_version": 1,
  "operation": "analyze | validate | build-draft",
  "adapter": "current_module1",
  "status": "...",
  "diagnostics": [],
  "final_config_ready": false
}
```

Handled input/document errors use stderr and exit `2`.  Technical failures use
exit `3`.  Successful stage output and the mandatory-policy-pause draft use
stdout.  Each stream contains at most one JSON document.
