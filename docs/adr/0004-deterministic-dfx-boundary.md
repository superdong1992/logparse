# ADR-0004: Deterministic DFX and Bounded Model Context

- Status: Accepted
- Date: 2026-07-11

## Context

GLM5.1 is useful for explanation inside the LAN but should not select targets by
exploring broad private log trees. Model-dependent selection would be difficult
to reproduce, expensive in context, and unsafe for evidence handling.

## Decision

Standalone logparse never invokes Claude CLI or GLM5.1. It deterministically
selects lifecycle, process/PID targets, evidence paths, error codes, and bounded
DFX windows.

Default DFX consumes structured artifacts without reading log bodies. Opt-in
deep DFX may produce at most five windows, 48 lines per window, and 80 KiB total,
preferably centered on `problem_time`. Its manifest records path, line range,
hash, selection reason, truncation, and caveats.

GLM5.1 consumes structured DFX and only the explicitly selected windows. A
missing or ambiguous target is never replaced by model exploration. The summary
contract is one `ERROR_CODE: 中文结论` line without raw evidence.

## Consequences

- Diagnosis selection remains reproducible and testable without a model.
- Model context stays bounded and attributable.
- Model failure falls back to deterministic `dfx_summary.txt`.
- DFX budgets and selection behavior are red architecture contracts.
