# logparse Mechanism Relation Rules

Use these rules after target anchors have been matched. They describe which extra logs to include as related context. Apply rules per anchor; do not merge all anchors into one cycle unless a rule says to.

## Rule Format

Rules are written as a human-maintained table. Add product-specific rules here as mechanisms become known.

```yaml
- id: short-rule-name
  when:
    target_module_key: module_key or "*"
    target_module_name: module_name or "*"
  include:
    module_key: related module_key
    slot: same | explicit slot id | from anchor field
    cycle: same | nearest-to-problem-time
    cpu_cycle: same | nearest-to-problem-time | none
    processes: target-only | all-in-cycle | [process names]
  reason: why this related log matters
```

When both `module_key` and `module_name` are present, prefer `module_key` for stable rules and use `module_name` only for output paths.

## Current Rules

### module1 target

```yaml
- id: module1-target-only
  when:
    target_module_key: module1
  include:
    module_key: module1
    slot: same
    cycle: same
    processes: target-only
  reason: module1 logs already merge diagnostic and journal entries for the same process lifecycle.
```

For `module1`, include only the matched target process by default. Add fixed related processes here when product knowledge says they are part of the same mechanism path.

### module2 target

```yaml
- id: module2-upstream-module1-context
  when:
    target_module_key: module2
  include:
    module_key: module1
    slot: same
    cycle: same
    processes: all-in-cycle
  reason: module2 is diagnostic-only and assigns entries to module1 lifecycle cycles; module1 provides upstream lifecycle context.
```

For `module2`, include same-slot, same-cycle `module1` logs when that upstream cycle exists. If the module2 cycle is `unknown`, try the nearest module1 cycle by problem time and mark it approximate.

For CPU anchors, "same cycle" means the same parent board cycle plus the same nested `cpu_id`/CPU cycle when available. If a nested CPU cycle is `unknown`, keep the same board cycle and `cpu_id`, then mark the CPU-cycle context as not time-bounded.

## Multi-Anchor Guidance

- Treat every user-specified process as a target anchor, not as secondary context.
- Preserve labels such as `client`, `server`, `active`, `standby`, or custom user wording.
- If anchors live in different slots or modules, match each independently and then correlate by problem time.
- Apply relation rules to each anchor separately. Deduplicate identical related log paths in the final report.
- If a relation rule adds a large `all-in-cycle` set, summarize the included process names and read only logs relevant to the issue symptoms first.

## Adding Product Rules

When adding a product-specific rule, include:

- The trigger module or process.
- Whether the related slot is the same slot, a peer slot, active/standby, or user-specified.
- Whether the related cycle must be the same cycle or nearest to the problem time.
- Whether nested CPU-cycle context must be the same CPU cycle, nearest CPU cycle, or omitted.
- Whether to include all process logs in the cycle or a named process list.
- A short reason that explains why the relation is valid.
