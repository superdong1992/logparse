# logparse Mechanism Relation Rules

Use these rules only after the requested target process logs have been found, or when the user explicitly asks for broader diagnosis. They describe which extra logs and lifecycle context to include. Apply rules per anchor; do not merge all anchors into one cycle unless a rule says to. Related logs are never a substitute for a missing requested process log.

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
    lifecycle: same-v3-lifecycle | nearest-v3-lifecycle | derived-from-anchor
    cycle: same-output-cycle | nearest-to-problem-time | derived-from-anchor
    cpu_cycle: same | nearest-to-problem-time | none | derived-from-anchor
    processes: target-only | all-in-cycle | [process names]
  reason: why this related context matters
```

When both `module_key` and `module_name` are present, prefer `module_key` for stable rules and use `module_name` only for output paths.

## Current Rules

### module1 target

```yaml
- id: module1-v3-target-context
  when:
    target_module_key: module1
  include:
    module_key: module1
    slot: same
    lifecycle: same-v3-lifecycle
    cycle: same-output-cycle
    processes: target-only
  reason: module1 owns lifecycle_split V3; the matched target process plus its V3 lifecycle evidence is the primary context.
```

For `module1`, read the matched target process first. Add other module1 process logs only when product knowledge or the V3 evidence says they explain the same mechanism path. Use `lifecycles[]` as final lifecycle context; use `candidate_segments[]` only together with `merge_decisions[]`.

### module2 target

```yaml
- id: module2-derived-v3-upstream-context
  when:
    target_module_key: module2
  include:
    module_key: module1
    slot: same
    lifecycle: derived-from-anchor
    cycle: derived-from-anchor
    cpu_cycle: derived-from-anchor
    processes: all-in-cycle
  reason: module2 is diagnostic-only and consumes module1 V3 lifecycle context, but its output cycle can be derived by PID/time routing and may not equal a module1 directory name.
```

For `module2`, do not assume the module2 output cycle directory is the same as a module1 cycle directory. Module2 entries may be assigned by exact `process_name + pid`, PID-only candidates with timestamp disambiguation, timestamp-window fallback, unknown nearest-time resolution, projected or expanded targets, or bounds expansion/clamp.

To choose upstream module1 context for a module2 anchor:

- Use same slot first.
- Use the module2 anchor's `cpu_id` when present; prefer module1 V3 CPU lifecycles with the same `cpu_id`.
- Use the problem time and the module2 entry time from the written log to select the nearest or containing module1 V3 `lifecycles[]` segment.
- If the module2 process/PID also exists in module1, use exact process+PID as stronger evidence; PID-only matches are weaker and must be labeled.
- If module2 has a known parent board and CPU cycle that matches module1 by time, include that upstream cycle as related context.
- If module2 has `unknown` board or CPU cycle, include the nearest admissible module1 V3 lifecycle around the problem time and mark it approximate.
- If module2's output bounds are expanded or clamped, report the module2 output cycle as derived and avoid rewriting module1 lifecycle semantics.

For CPU anchors, "derived context" means same slot, same `cpu_id`, and the module1 V3 CPU lifecycle that contains or is nearest to the anchor time. If no CPU lifecycle exists, fall back to the parent board V3 lifecycle and mark CPU context absent.

## Legacy Lifecycle Compatibility

If `lifecycle_split_result.algorithm == "interval_v3"` is missing, fall back to legacy `boundary_issues[]`, CycleDetector `split_traces`, and timed `board_cycles[]` only as compatibility evidence. Label this explicitly as non-V3 or legacy output.

## Multi-Anchor Guidance

- Treat every user-specified process as a target anchor, not as secondary context.
- Preserve labels such as `client`, `server`, `active`, `standby`, or custom user wording.
- If anchors live in different slots or modules, match each independently and then correlate by problem time.
- Apply relation rules to each anchor separately. Deduplicate identical related log paths in the final report.
- If any requested target log is missing, report that missing log first instead of continuing with only related context.
- If a relation rule adds a large `all-in-cycle` set, summarize included process names and read only logs relevant to the issue symptoms first.

## Adding Product Rules

When adding a product-specific rule, include:

- The trigger module or process.
- Whether the related slot is the same slot, a peer slot, active/standby, or user-specified.
- Whether related context must use the same V3 lifecycle, nearest V3 lifecycle, or anchor-derived routing.
- Whether nested CPU-cycle context must use the same CPU, nearest CPU lifecycle, or be omitted.
- Whether to include all process logs in the cycle or a named process list.
- A short reason that explains why the relation is valid.
