# logparse Agent Instructions

This repository is configured for Everything Claude Code (ECC) usage in Codex.
Use the project-local `.codex/config.toml` multi-agent roles when a task benefits
from read-only exploration, owner-style review, or primary-source documentation
checks.

## Repository Rules

Read `CLAUDE.md` before repo analysis or code edits. Its rule preflight,
architecture boundaries, lifecycle V3 contract, and testing notes are the
repository-specific source of truth.

Before inspecting or changing files, run the relevant local preflight command
from `CLAUDE.md`, for example:

```bash
python scripts/rule_preflight.py --changed
```

## LAN DFX Operating Model

Before changing diagnosis, query, DFX, CLI, or output artifacts, read
`docs/lan-dfx-operating-model.md`.

This repo is often implemented outside the LAN with Codex, but real logs and
final diagnosis run inside the LAN. Keep standalone logparse deterministic: it
must not call Claude CLI by default, and external handoff should remain a single
`ERROR_CODE: 中文结论` line without raw log text.

## ECC Boundaries

- Keep workflow contributions in `.agents/skills/` first.
- Keep machine-specific MCP servers, credentials, and secrets out of this repo.
- Use read-only agents for exploration and review unless the user explicitly asks
  for a scoped implementation.
- Do not commit or push without an explicit user request.
