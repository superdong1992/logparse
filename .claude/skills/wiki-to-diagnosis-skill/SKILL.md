---
name: wiki-to-diagnosis-skill
description: Use when 把 Markdown 问题定位 wiki 转成 .claude/skills/diagnose-* 中文定位 skill，尤其是需要先调用 logparse-diagnose、只分析 target_logs 路径并生成扁平 result.zip。
---

# Wiki To Diagnosis Skill

## Overview

Generate a repo-local Claude diagnosis skill from a Markdown issue-location wiki. Use a fixed skeleton and fill only the wiki-derived blanks; do not invent a new workflow.

Load `references/wiki-template.md` when the wiki is too loose to extract target roles or analysis steps. Load `references/generated-skill-contract.md` before writing or reviewing the generated skill.

This skill requires two generation inputs before creating or updating a generated skill:

- `wiki`: a Markdown wiki file path or pasted Markdown content.
- `module_name`: the concrete logparse business/output module name for the generated skill, for example `EXAMPLE` or `MODULE2`.

## Stage Machine

1. Extract wiki facts.
2. Fill the generated skill skeleton.
3. Validate the generated skill with `quick_validate.py` and `scripts/validate_generated_skill.py`.
4. Report the generated path, chosen name, assumptions, and validation results.

Stop and ask if `wiki`, `module_name`, target roles, or diagnosis steps are missing. Do not infer `module_name` from internal keys such as `module1` unless the user explicitly says that is also the concrete business module name.

## Extract Wiki Facts

Extract these fields from the wiki:

- `chinese_title`: Chinese display name.
- `skill_name`: `diagnose-<english-topic-slug>`, lowercase letters/digits/hyphens, under 64 characters. Use the wiki `skill_name` if valid; otherwise translate/summarize the title.
- `module_name`: the fixed concrete logparse business/output module for this generated skill, for example `EXAMPLE` or `MODULE2`.
- `problem_scope`: the problem symptoms this skill diagnoses.
- `target_roles`: one row per runtime target process; each row has label, Chinese description, required/optional status, and any wiki hint for process.
- `analysis_steps`: ordered wiki diagnosis steps.
- `judgement_rules`: evidence rules that support or reject conclusions.
- `output_requirements`: wiki-specific fields to include in `result.txt`.
- `assumptions`: any missing but reasonably inferred wiki facts.

Never treat runtime values such as log package path, config path, output directory, problem time, slot, process name, or PID as wiki facts. The generated skill collects those when it runs. The module name is not a runtime value here; it must be fixed once in the generated skill frontmatter as `module_name: <module_name>`.

## Create Skill Folder

Create or update `.claude/skills/<skill_name>/SKILL.md`.

Keep generated Claude skills small and repo-local:

- Do not create `agents/openai.yaml`.
- Do not create README, changelog, or install docs.
- Add reference files only if wiki-specific rules are too long for `SKILL.md`.
- The generated body, runtime prompts, result text, and artifact instructions must be Chinese.

## Generated Skill Skeleton

Fill this skeleton exactly. Replace bracketed placeholders with wiki-derived content. Keep all mandatory sections and contract wording.

~~~markdown
---
name: <skill_name>
description: 用于<chinese_title>；必须先调用 logparse-diagnose skill 获取 target_logs，再只基于 target_logs[*].log_path 指定日志按 wiki 规则分析并生成扁平 result.zip。
effort: medium
module_name: <module_name>
---

# <chinese_title>

中文显示名：<chinese_title>

## 问题范围

<problem_scope>

## 运行时输入

先收集全局输入：

- `input_path`：原始日志输入或 `output/{task_id}` 预处理结果目录。原始日志输入可以是日志压缩包、单个非压缩诊断日志，或原始日志目录。
- `config_path`：repo 内 V3 配置文件路径，必须包含具体 YAML 文件名，例如 `config.yaml` 或 `configs/v3.yaml`，不要只传配置目录。`input_path` 是原始日志输入时必填；已有 `output/{task_id}/result.json` 时不重新解析。
- `output_dir`：解析输出目录，例如 `output`；传给 `logparse-diagnose` 时必须明确。
- `problem_time`：问题发生的近似时间，保留用户给出的时区描述。
- 固定 module_name：当前 skill 的 frontmatter `module_name: <module_name>`，运行时不再向用户询问模块。

再按目标进程记录收集，不要拆成独立列表：

| 标签 | 说明 | 是否必需 | 运行时字段 |
| --- | --- | --- | --- |
<target_roles_table_rows>

`运行时字段` 列只允许列出 `slot`、`process_name` 和可选 `pid`，不要要求用户填写模块。

每组目标进程必须保持为同一条 `固定 module_name + slot + process_name + 可选 pid` 记录。用户提供 PID 时，PID 只约束对应那一组进程。

## 先调用 logparse-diagnose skill

`logparse-diagnose` 也是本项目里的一个 Claude skill，路径是 `.claude/skills/logparse-diagnose/SKILL.md`。

不要把 `logparse-diagnose` 当成 shell 命令、Python 模块或普通说明文字。必须先调用/加载这个 skill，让它完成：

调用时必须把 `input_path + config_path + output_dir + problem_time + targets[]` 一起交给 `logparse-diagnose`。如果 `input_path` 是原始日志输入，不要省略配置文件路径，不要只传配置目录；预处理必须等价于 `python3.12 cli.py parse <input_path> -c <config_path> -o <output_dir>`。当前定位 skill 不要自行运行 parse。

1. 对 `input_path` 做预处理；
2. 根据每组 `固定 module_name + slot + process_name + 可选 pid` 生成 anchor；
3. 对每个 anchor 调用 `cli.py mech-target-logs`，由 logparse 确定性选择 lifecycle/cycle 并拼出目标日志路径；
4. 输出结构化 `target_logs` 清单，每个目标进程对应一个匹配结果。

组装 targets[] 时必须使用 frontmatter 固定 module_name，并把该值写入每组 targets[].module，再合并每组运行时提供的 slot、process_name 和可选 pid。

当前定位 skill 只分析 `logparse-diagnose` 返回的 `target_logs[*].log_path` 指定模块日志。

## 证据收敛约束

禁止发散分析。只允许基于 `logparse-diagnose` 返回的 `target_logs[*].log_path` 目标模块日志和本 wiki 的定位规则给结论。
不要补充 wiki 未要求的排查方向。
不要分析无关模块、无关进程或无关代码。
不要遍历 `output/`，不要重新选择 lifecycle/cycle，不要重新拼接日志路径，不要用相关日志替代缺失的目标日志。
不要直接运行 `cli.py parse` 或绕过 `logparse-diagnose` 处理原始日志输入。
不要根据经验猜根因。
没有日志证据时，定位结论必须写“当前证据不足以确认根因”。

## Wiki 定位步骤

<analysis_steps_as_numbered_list>

## 判断规则

<judgement_rules_as_bullets>

## Result.zip 交付物

生成扁平 `result.zip`，根目录只包含 `result.txt` 和本次实际使用的目标进程日志。

固定文件名：

```text
result.txt
<label>__<module_name>__slot_<slot>__<process_name>[-<pid>].log
<label>__<module_name>__slot_<slot>__cpu_<cpu_id>__<process_name>[-<pid>].log
```

日志文件必须来自 `target_logs[*].log_path`。复制到临时目录时使用安全扁平文件名，替换路径分隔符和 Windows 非法字符；当 `target_logs` 含 `cpu_id` 时，zip 内日志文件名必须包含 `cpu_<cpu_id>`。

`result.txt` 最小结构：

```text
定位结论
<第一段直接给出明确结论；证据不足时写“当前证据不足以确认根因”。>

关键分析依据
1. <日志标签/路径/时间点/关键行或状态变化>
2. <跨进程或跨时间证据>

证据缺口
<仅在日志缺失、nearest/unknown 匹配、V3 caveat 或证据不足时出现。>
```

打包时先把 `result.txt` 和实际使用的日志复制到一个临时目录，然后运行：

```bash
python3.12 .claude/skills/wiki-to-diagnosis-skill/scripts/pack_result_zip.py <临时目录> <result.zip路径>
```

不要创建 `logs/`，不要创建 `manifest.txt`，也不要创建任何子目录。每份日志必须来自 `target_logs[*].log_path`。

## 最终回复

说明 `result.zip` 的路径、定位结论、缺失证据和使用过的目标日志。
~~~

## Validation

Run both validators after creating or updating the generated skill:

```bash
python3.12 -X utf8 <quick_validate.py> .claude/skills/<skill_name>
python3.12 -X utf8 .claude/skills/wiki-to-diagnosis-skill/scripts/validate_generated_skill.py .claude/skills/<skill_name>
```

If a sample `result.zip` was created during validation, also run:

```bash
python3.12 -X utf8 .claude/skills/wiki-to-diagnosis-skill/scripts/validate_generated_skill.py .claude/skills/<skill_name> --result-zip <result.zip路径>
```

Fix every validator failure before reporting completion.
