# 生成出的定位 Skill 契约

本文件定义由 `wiki-to-diagnosis-skill` 生成的问题定位 skill 必须满足的行为。生成出的 skill 面向人工维护，正文、运行时提问、分析结果和 `result.txt` 必须使用中文。

## 命名

- 生成出的 skill 必须放在 `.claude/skills/diagnose-<english-topic-slug>/SKILL.md`，用于 Claude Code 项目级 skill 发现。
- skill 目录名和 frontmatter `name` 使用 `diagnose-<english-topic-slug>`，并保持完全一致。
- 名字只能包含英文小写字母、数字和短横线，长度少于 64 字符。
- 中文问题名保存在正文中，例如“中文显示名：链路超时问题定位”。
- 不要为生成出的 Claude skill 添加 Codex/OpenAI 专用的 `agents/openai.yaml`。

## Frontmatter

生成出的定位 skill 默认使用 `effort: medium`，避免弱模型或高 effort 配置下过度发散。除非用户明确要求，不要生成 `high`、`xhigh`、`max` 等更高 effort。

```yaml
---
name: diagnose-<english-topic-slug>
description: 中文说明：用于根据指定 wiki 定位某类问题；必须先调用 logparse-diagnose skill 获取 target_logs，再只基于 target_logs 指定日志按 wiki 规则分析并生成扁平 result.zip。
effort: medium
---
```

## 运行时输入

生成出的定位 skill 必须先收集运行时输入。

全局输入：

- `input_path`：完整日志包、诊断日志压缩包、单个诊断日志文件，或 `output/{task_id}` 预处理结果目录。
- `problem_time`：问题发生的近似时间，保留用户提供的时区描述。

目标进程输入：

- 根据 wiki 定位流程收集 N 组目标进程信息。
- 每组必须包含 `module`、`slot`、`process_name`。
- 每组可选 `pid`；用户提供 PID 时必须作为该组进程的严格匹配条件。
- 每组应保留 wiki 标签，例如 `client`、`server`、`active`、`standby`。

禁止行为：

- 不要把 `slot`、`process_name`、`module` 拆成独立列表。
- 不要交叉组合不同目标进程的字段。
- 不要从 wiki 或 result 目录猜测缺失的必填字段；缺失时用中文补问整组目标进程信息。

## 日志获取阶段

生成出的定位 skill 必须使用 `logparse-diagnose` 做预处理和目标日志获取。

生成出的定位 skill 必须把下面这件事说清楚：`logparse-diagnose` 也是一个 Claude skill，不是 shell 命令、不是 Python 模块、也不是普通提示词。当前定位 skill 不能直接开始分析原始日志，必须先调用/加载 `logparse-diagnose` skill。

生成出的定位 skill 必须包含这个中文小节，或语义完全等价的内容：

```markdown
## 先调用 logparse-diagnose skill

`logparse-diagnose` 也是本项目里的一个 Claude skill，路径是 `.claude/skills/logparse-diagnose/SKILL.md`。

不要把 `logparse-diagnose` 当成 shell 命令、Python 模块或普通说明文字。必须先调用/加载这个 skill，让它完成：

1. 对 `input_path` 做预处理；
2. 根据每组 `module + slot + process_name + 可选 pid` 生成 anchor；
3. 对每个 anchor 调用 `cli.py mech-target-logs`，由 logparse 确定性选择 lifecycle/cycle 并拼出目标日志路径；
4. 输出结构化 `target_logs` 清单，每个目标进程对应一个匹配结果。

当前定位 skill 只负责分析 `logparse-diagnose` 返回的 `target_logs[*].log_path` 指定模块日志，并生成扁平 `result.zip`。
```

在 Claude 中使用时，优先通过项目级 Claude skill `$logparse-diagnose` / `/logparse-diagnose` 调用该能力；如果 Claude 没有自动加载该 skill，则读取 `.claude/skills/logparse-diagnose/SKILL.md` 并按其说明执行。

对每组目标进程信息构造一个 anchor：

```text
label=<wiki 标签>
module=<module>
slot=<slot>
process_name=<process_name>
pid=<pid，可选>
```

调用 `logparse-diagnose` 时传入：

- `input_path`
- `problem_time`
- 所有目标进程 anchors

如果当前 `logparse-diagnose` 或 logparse 版本报告不支持诊断日志压缩包、单个诊断日志文件或其他输入类型，生成出的定位 skill 必须用中文说明“当前工具版本不支持该输入类型，需要先升级 logparse/logparse-diagnose 或改用已支持的输入”，不能绕过 `logparse-diagnose` 自己解析日志。

必须使用 `logparse-diagnose` 返回的 `target_logs` 作为后续分析输入。每个目标进程必须有一条对应记录，字段包括：

- `label`；
- `module_key`；
- `module_name`；
- `slot`；
- `process_name`；
- `pid`，如果用户提供；
- `match_status`，例如 `exact`、`nearest`、`unknown`、`missing`、`ambiguous`；
- `log_path`，仅在目标日志成功匹配时存在；
- board/cpu cycle 匹配状态；
- `caveats`，例如 V3 lifecycle caveat、nearest/unknown、解析错误或截断说明；
- 日志内容或问题时间附近窗口；
- 缺失日志或解析错误。

`target_logs[*].log_path` 是唯一允许读取的目标模块日志来源。生成出的定位 skill 不得遍历 `output/`，不得绕过 `logparse-diagnose` 或 `cli.py mech-target-logs` 自己选择 lifecycle、cycle 或输出路径，不得重新拼接日志路径。如果某个目标的 `log_path` 缺失，必须把该目标日志视为缺失证据。

## Wiki 分析阶段

生成出的定位 skill 必须读取并分析 `logparse-diagnose` 返回的 `target_logs[*].log_path` 目标模块日志。

生成出的定位 skill 必须包含这个中文小节，或语义完全等价的内容：

```markdown
## 证据收敛约束

禁止发散分析。只允许基于 `logparse-diagnose` 返回的 `target_logs[*].log_path` 目标模块日志和本 wiki 的定位规则给结论。
不要补充 wiki 未要求的排查方向。
不要分析无关模块、无关进程或无关代码。
不要遍历 `output/`，不要重新选择 lifecycle/cycle，不要重新拼接日志路径，不要用相关日志替代缺失的目标日志；生命周期选择必须来自 `logparse-diagnose` 调用的 `cli.py mech-target-logs`。
不要根据经验猜根因。
没有日志证据时，定位结论必须写“当前证据不足以确认根因”。
```

分析要求：

- 按 wiki 定位步骤检查日志中的状态、错误、请求响应、超时、序号、PID、时间顺序和跨进程关联证据。
- 多个目标进程按 wiki 定义的标签和顺序关联分析。
- 如果某个目标的 `target_logs` 记录缺失或 `log_path` 缺失，先报告缺失证据，不能用相关日志替代目标日志。
- 除非日志证据直接支持，否则不能宣称根因；使用“证据指向”“可疑点”“仍需确认”等保守表述。
- 不要输出 wiki 未要求的额外排查树、泛化建议或经验性猜测。

如果证据不足，定位结论必须明确写：

```text
当前证据不足以确认根因。
```

随后说明缺失的日志、匹配 caveat 或需要补充的目标进程信息。

## Result.zip 交付物

分析完成后必须生成 `result.zip`。

固定目录结构：

```text
result.zip
├── result.txt
├── <label>__<module>__slot_<slot>__<process_name>[-<pid>].log
└── ...
```

要求：

- `result.txt` 必须使用中文。
- `result.txt` 第一段必须是明确的定位结论。
- `result.txt` 第二部分给出关键分析依据。
- 只有存在日志缺失、nearest/unknown 匹配、V3 caveat 或证据不足时，才添加“证据缺口”部分。
- 压缩包根目录只允许放 `result.txt` 和本次分析实际使用的目标进程 module 日志。
- 不要创建 `logs/`，不要创建 `manifest.txt`，也不要创建任何子目录。
- 复制日志时避免同名覆盖，文件名必须包含标签、module、slot、process_name，并在 PID 存在时包含 PID。
- 每份日志必须来自 `target_logs[*].log_path`；不得从 `output/` 另找或重建路径。

`result.txt` 最小结构：

```text
定位结论
<第一段直接给出明确结论；证据不足时写“当前证据不足以确认根因”。>

关键分析依据
1. <日志标签/路径/时间点/关键行或状态变化>
2. <跨进程或跨时间证据>

证据缺口
<仅在需要时出现。>
```

最终回复必须说明 `result.zip` 的路径。
