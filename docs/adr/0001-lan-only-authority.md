# ADR-0001: LAN Repository Is the Only Authority

- Status: Accepted
- Date: 2026-07-11

> 2026-10-12：本文件保留历史决策。当前移交前精简的授权及被替代的兼容约定见 [ADR-0006](0006-pre-handoff-simplification.md)。

## Context

logparse behavior depends on private product components and real logs that cannot
leave the LAN. Code modified from those findings also cannot be synchronized
back outside. Maintaining active LAN and external branches would create two
unmergeable implementations.

## Decision

After handoff, the LAN repository is the only authoritative source. The external
checkout is frozen. No LAN code, diff, commit, fixture, product configuration,
or raw log is synchronized outward. Claude Code + GLM5.1 perform future
development and real-log validation inside the LAN.

An approved human conclusion may cross the boundary only as
`ERROR_CODE: 中文结论`, without raw evidence or private identifiers.

## Consequences

- All development, review, regression, release, and recovery capability must be
  available inside the LAN.
- Repository documentation and agent skills carry the operating knowledge.
- External Codex cannot safely implement or review later logparse changes.
- LAN storage and internal remotes require their own backups and access control.
