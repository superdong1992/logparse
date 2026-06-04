---
name: wiki-to-diagnosis-skill
description: Use when converting a Markdown issue-location wiki into a repo-local Chinese diagnosis skill for logparse. The generated skill must collect runtime log inputs and per-target module/slot/process anchors, call logparse-diagnose to preprocess and retrieve module logs, analyze those logs with wiki rules, and produce result.zip with result.txt and used process logs.
---

# Wiki To Diagnosis Skill

## Overview

Use this Claude project skill to generate a repo-local problem diagnosis skill from a Markdown issue-location wiki. The generated skill is for humans to maintain, so its body, runtime questions, result text, and artifact instructions must be written in Chinese, while its folder name and frontmatter `name` must remain English lowercase hyphen-case.

Load `references/wiki-template.md` when the wiki structure is unclear or when you need to ask the user to normalize a wiki. Load `references/generated-skill-contract.md` before writing or reviewing the generated skill.

## Workflow

1. Read the Markdown wiki.
   - Extract the Chinese display name, problem scope, target process roles, analysis steps, decision rules, and output expectations.
   - Do not assume the wiki contains runtime values such as log package path, problem time, slot, module, process name, or PID.
   - If the wiki does not state how many target process roles the analysis needs, infer the minimum role count from the analysis steps; if that is still ambiguous, ask the user.

2. Choose the generated skill name.
   - Use `diagnose-<english-topic-slug>`.
   - Require lowercase English letters, digits, and hyphens only, under 64 characters.
   - If the user provided `skill_name` in the wiki, use it after validating the naming rule.
   - If the wiki title is Chinese and no `skill_name` is provided, translate or summarize it into a short English slug, then preserve the Chinese title in the generated skill body.

3. Create or update the generated skill folder.
   - Target path: `.claude/skills/<skill_name>/SKILL.md`.
   - Use Claude's skill shape: a folder containing `SKILL.md` with YAML frontmatter fields `name` and `description`, plus optional `references/`, `scripts/`, or `assets/`.
   - Do not add Codex/OpenAI-specific `agents/openai.yaml` files to generated Claude skills.
   - Do not create extra README, changelog, or install docs.
   - Keep reference files only if the wiki-specific rules are too long for a concise `SKILL.md`.

4. Write the generated skill in Chinese.
   - The frontmatter `name` stays English.
   - The frontmatter `description` is Claude's trigger predicate. It may include English trigger terms, but it must clearly state in Chinese that this skill uses `logparse-diagnose`, analyzes returned module logs, and creates `result.zip`.
   - The body must follow `references/generated-skill-contract.md`.
   - Embed the wiki-derived target roles and analysis steps directly enough that a future agent can run the diagnosis without re-reading the original wiki.

5. Validate the generated skill.
   - Run `quick_validate.py` with UTF-8 mode on Windows when available.
   - Confirm the generated skill lives under `.claude/skills/`, not `.agents/skills/`.
   - Confirm the generated body requires per-target grouped inputs: `module + slot + process_name`, optional `pid`.
   - Confirm it states that `logparse-diagnose` returns the module logs and that the generated skill analyzes those logs.
   - Confirm it requires `result.zip` with `result.txt` first giving a clear conclusion and then key analysis evidence.

## Generated Skill Requirements

Every generated diagnosis skill must require these runtime inputs:

- Global inputs: `input_path` and `problem_time`.
- Per-target process inputs: one or more records, each containing `module`, `slot`, and `process_name`; `pid` is optional.

Every generated diagnosis skill must perform these phases:

- Build one `logparse-diagnose` anchor per target process record.
- Use `logparse-diagnose` to preprocess the input and retrieve each target process's corresponding module log.
- Read and analyze the returned module logs according to the wiki-derived rules.
- Create `result.zip` containing `result.txt` and the process logs actually used in the analysis.

Do not allow the generated skill to split `slot` and `process_name` into separate lists or combine them across targets. They belong to the same target process record.

## Output Rules

When this generator finishes, report:

- The generated skill path.
- The chosen English skill name.
- Any assumptions made while converting the wiki.
- The validation command and result.

If generation cannot be completed because the wiki lacks enough information about target roles or diagnosis steps, stop and ask for the missing wiki-level information instead of generating a vague skill.
