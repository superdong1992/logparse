# ADR-0002: Enforced Green, Yellow, and Red Architecture Boundaries

- Status: Accepted
- Date: 2026-07-11

## Context

Current-product concepts such as slot, CPU, board role, Module1, and Module2 were
mixed with generic parsing infrastructure. A model fixing a real-log rule could
therefore unintentionally change extraction, artifacts, or query compatibility.

## Decision

Separate changes into three machine-enforced zones:

- Green: product topology, format adapters, diagnosis knowledge, and focused
  tests. GLM5.1 may modify these in normal LAN work.
- Yellow: lifecycle and correlation policy. Require a real case, minimal
  fixture, corpus regression, and schema conclusion.
- Red: contracts, application/infrastructure, artifacts, CLI compatibility,
  security, and governance. Require an Accepted ADR, explicit approval, full
  validation, and rollback.

The exact paths live in `governance/architecture-boundaries.toml`. Red has
precedence, and unclassified source paths default to red. `change_gate.py`,
`rule_preflight.py`, and the TOML are red. The gate also hard-codes its governing
documents and scripts as non-downgradable red paths, so a TOML-only edit cannot
reclassify the guardrails it is changing.

Slot/CPU/board/role are green product concepts. The generic core uses abstract
scope/hierarchy contracts and cannot import concrete product extensions.

## Consequences

- Routine business evolution is localized and inexpensive for GLM5.1.
- High-risk policy and architecture changes carry proportionate evidence.
- Adding a new unclassified directory is conservative by default.
- Bypassing the gate is itself a prohibited red architecture change.
