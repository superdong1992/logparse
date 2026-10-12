# 生成诊断 skill 的契约与模板

诊断正文保存于 `.agents/skills/diagnose-<english-topic-slug>/SKILL.md`。同名 `.claude/skills/diagnose-*/SKILL.md` 只负责发现和加载正文，两份 frontmatter 的 `name`、`description`、`effort`、`module_name` 必须相同。不要复制诊断流程到包装器，不新增 `agents/openai.yaml`。

名称只能包含小写字母、数字和短横线，总长度小于 64 字符；目录名必须与 `name` 相同。正文、运行时提问和结果均使用自然的简体中文。`module_name` 必须是明确的业务模块名，不能保留占位符。

以下模板只替换 wiki 对应内容，保留确定性目标选择、严格 PID 约束、证据缺口和扁平打包要求。`<input_path>`、`<config_path>` 等运行时命令占位符可以保留，frontmatter 必须完整填写。

所有命令使用 `.venv` 中的 Python 3.12；原生 Windows 使用 `.venv\Scripts\python.exe`。

## 正文模板

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
- `config_path`：完整根配置文件路径，必须包含具体 YAML 文件名，例如 `config.yaml`，不要只传配置目录，也不能直接使用被 include 的产品片段。`input_path` 是原始日志输入时必填；已有 `output/{task_id}/result.json` 时不重新解析。
- `product`：根配置 `products` 下的产品键，例如 `default` 或 `compact`。原始日志输入时必填；沿用用户已指定的产品，缺失时先询问，不根据文件名或模块名猜测，也不默认为 `default`。已有 `result.json` 时无需补传产品。
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

`logparse-diagnose` 是本仓的诊断 skill，正文位于 `.agents/skills/logparse-diagnose/SKILL.md`，Claude skill 入口位于 `.claude/skills/logparse-diagnose/SKILL.md`。

不要把 `logparse-diagnose` 当成 shell 命令、Python 模块或普通说明文字。必须先调用/加载这个 skill，让它完成：

原始日志输入的交接字段为 `input_path + config_path + product + output_dir + problem_time + targets[]`，必须一起交给 `logparse-diagnose`。不要省略配置文件路径或产品，不要只传配置目录；预处理必须等价于 `.venv/bin/python cli.py parse <input_path> -c <config_path> --product <product> -o <output_dir>`。当前定位 skill 不要自行运行 parse。已有 `result.json` 时交接结果路径、输出目录、问题时间和目标即可，不要求补传 `config_path` 或 `product`，也不重新解析；后续查询无需 `--product`。

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
.venv/bin/python .claude/skills/wiki-to-diagnosis-skill/scripts/pack_result_zip.py <临时目录> <result.zip路径>
```

不要创建 `logs/`，不要创建 `manifest.txt`，也不要创建任何子目录。每份日志必须来自 `target_logs[*].log_path`。

## 最终回复

说明 `result.zip` 的路径、定位结论、缺失证据和使用过的目标日志。
~~~

## Claude 入口

复制正文的四个 frontmatter 字段，正文仅写：

```markdown
读取 `.agents/skills/<skill_name>/SKILL.md`，按其中流程完成诊断。
所有规则和参考资料以该正文为准，不在此处另行维护。
```

## 仓内验证

```bash
.venv/bin/python -X utf8 .claude/skills/wiki-to-diagnosis-skill/scripts/validate_generated_skill.py .agents/skills/<skill_name> --wrapper .claude/skills/<skill_name>
```

如已生成样例 `result.zip`，追加 `--result-zip <result.zip路径>`。验证器检查 YAML/frontmatter、实际模块名、必需证据约束、正文与 Claude 入口的路径及字段一致性，以及 zip 扁平结构。修复全部失败后再交付。无需依赖仓外的技能校验脚本。
