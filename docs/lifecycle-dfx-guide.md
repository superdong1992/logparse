# Lifecycle V3 DFX Guide

V3 treats a 30-second silent gap as a candidate split. It then uses reliable process No wrap evidence and reliable process PID evidence to decide whether adjacent candidates should be merged or kept split.

## Commands

```bash
python cli.py parse <package> -c config.yaml --lifecycle-dfx decisions
python cli.py mech-lifecycles <task_id> -s <slot_id> -m <module_name> --show-boundaries --lifecycle-dfx full
```

`--show-boundaries` now displays V3 DFX only. If a result lacks `algorithm: interval_v3`, CLI reports legacy output as unsupported.

## Fields

- `candidate_segments`: initial candidate segments.
- `merge_decisions`: merge/keep decisions for candidate boundaries.
- `lifecycles`: final lifecycle segments.
- `journal_evidence`: No wrap evidence. The field name is kept for the V3 output contract.
- `issues`: reliability issues.
- `lifecycle_reliable`: false when error-level lifecycle issues exist.

## Common Signals

- `decision=merged`: adjacent candidates were merged.
- `decision=kept_split`: the candidate boundary was kept.
- `reliable_process_multiple_pid_in_lifecycle`: one reliable process has multiple PIDs in one final lifecycle.
- `invalid_lifecycle_evidence`: evidence used by lifecycle logic is missing required fields.

## No Wrap Evidence

V3 uses a merge-first policy: 默认尽量合并相邻候选生命周期 unless there
is boundary evidence that blocks the merge. No 回绕只是否定证据; absence
of No wrap is not a positive reason to split or merge by itself.

No wrap evidence is produced only for configured 可靠进程 entries with
`sequence > 0`. A No wrap counts when all of these are true:

- The two entries are in the same slot and lifecycle scope.
- The two entries have the same canonicalized process name after
  `process_name_mapping`.
- The process is listed in `reliable_processes`.
- The source may be `diagnostic` or `journal`, and the two sides may come from
  different sources.
- PID is ignored for the No comparison / 忽略 PID; PID conflict checks run only
  after No wrap evidence is considered.
- The older entry belongs to the left candidate segment / 左候选段, and the
  newer entry belongs to the right candidate segment / 右候选段.
- The sequence decreases across that candidate boundary, for example
  `No[99]` in the left candidate followed by `No[1]` in the right candidate.

These cases are not No wrap evidence:

- 非可靠进程, even if the numbers look like `No[99]` followed by `No[1]`.
- Different canonical process names, even if the numbers look like `No[99]`
  followed by `No[1]`.
- Missing or zero sequence on either side.
- Increasing or equal sequence, such as `No[10]` followed by `No[11]`.

When No wrap evidence exists, the boundary is kept. When it does not exist,
the decision falls through to reliable-process PID checks and then to the
default merge behavior.

## Config

```yaml
lifecycle_split:
  process_name_mapping: {}
  reliable_processes: []
  multi_instance_processes: []
```

Put stable boundary evidence processes in `reliable_processes`. Put allowed same-name multi-instance processes in `multi_instance_processes`. The same canonical process cannot appear in both lists.
