---
name: logparse-diagnose
description: Use when diagnosing logparse log packages or preprocessed output in Claude Code, especially when a generated diagnosis skill needs preprocessing and target module logs for module/slot/process anchors.
---

# Logparse Diagnose

This is a Claude project-skill wrapper for the repo-local canonical diagnosis workflow.

When this skill is invoked in Claude Code:

1. Read `.agents/skills/logparse-diagnose/SKILL.md`.
2. Follow that canonical workflow exactly.
3. When the canonical workflow tells you to load references, read them from `.agents/skills/logparse-diagnose/references/`.
4. Preserve the canonical output contract: return the matched target process module logs, V3 context, gaps, and caveats.

Do not reimplement lifecycle, cycle, or output-path selection in this wrapper. Its only purpose is to make `logparse-diagnose` discoverable as a Claude project skill under `.claude/skills/`.
