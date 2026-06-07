# Lifecycle V3 DFX Guide

V3 treats a 30-second silent gap as a candidate split. It then uses reliable process PID evidence and journal evidence to decide whether adjacent candidates should be merged or kept split.

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
- `journal_evidence`: journal wrap evidence.
- `issues`: reliability issues.
- `lifecycle_reliable`: false when error-level lifecycle issues exist.

## Common Signals

- `decision=merged`: adjacent candidates were merged.
- `decision=kept_split`: the candidate boundary was kept.
- `reliable_process_multiple_pid_in_lifecycle`: one reliable process has multiple PIDs in one final lifecycle.
- `invalid_lifecycle_evidence`: evidence used by lifecycle logic is missing required fields.

## Config

```yaml
lifecycle_split:
  process_name_mapping: {}
  reliable_processes: []
  multi_instance_processes: []
```

Put stable boundary evidence processes in `reliable_processes`. Put allowed same-name multi-instance processes in `multi_instance_processes`. The same canonical process cannot appear in both lists.
