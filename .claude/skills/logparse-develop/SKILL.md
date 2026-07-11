---
name: logparse-develop
description: Use when developing, fixing, refactoring, or reviewing logparse in the authoritative LAN repository and the change must follow green, yellow, and red architecture boundaries.
---

# logparse Develop

This is the Claude project-skill wrapper for the repository's canonical LAN
development workflow.

When invoked:

1. Read `.agents/skills/logparse-develop/SKILL.md` completely.
2. Follow that workflow exactly, including preflight, zone classification,
   change-record evidence, testing, and final enforced gate.
3. Treat `governance/architecture-boundaries.toml` as authoritative when prose
   and a remembered path classification differ.
4. Stop before a red change unless the required ADR and explicit human approval
   already exist.

Do not duplicate or weaken the canonical workflow in this wrapper. Do not export
LAN code, diffs, fixtures, configurations, or logs to an external checkout.
