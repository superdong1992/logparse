# 生成出的定位 Skill 契约

本文件定义由 `wiki-to-diagnosis-skill` 生成的问题定位 skill 必须满足的行为。生成出的 skill 面向人工维护，正文、运行时提问、分析结果和 `result.txt` 必须使用中文。

## 命名

- skill 目录名和 frontmatter `name` 使用 `diagnose-<english-topic-slug>`。
- 名字只能包含英文小写字母、数字和短横线，长度少于 64 字符。
- 中文问题名保存在正文中，例如“中文显示名：链路超时问题定位”。

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

必须使用 `logparse-diagnose` 返回的结果作为后续分析输入，包括：

- 模块日志路径；
- 日志内容或问题时间附近窗口；
- board/cpu cycle 匹配状态；
- exact/nearest/unknown 状态；
- V3 lifecycle caveat；
- 缺失日志或解析错误。

生成出的定位 skill 不得绕过 `logparse-diagnose` 自己选择 lifecycle、cycle 或输出路径。

## Wiki 分析阶段

生成出的定位 skill 必须读取并分析 `logparse-diagnose` 返回的目标模块日志。

分析要求：

- 按 wiki 定位步骤检查日志中的状态、错误、请求响应、超时、序号、PID、时间顺序和跨进程关联证据。
- 多个目标进程按 wiki 定义的标签和顺序关联分析。
- 如果某个目标日志缺失，先报告缺失证据，不能用相关日志替代目标日志。
- 除非日志证据直接支持，否则不能宣称根因；使用“证据指向”“可疑点”“仍需确认”等保守表述。

如果证据不足，定位结论必须明确写：

```text
当前证据不足以确认根因。
```

随后说明缺失的日志、匹配 caveat 或需要补充的目标进程信息。

## Result.zip 交付物

分析完成后必须生成 `result.zip`。

推荐目录结构：

```text
result.zip
├── result.txt
├── logs/
│   ├── <label>__<module>__slot_<slot>__<process_name>[-<pid>].log
│   └── ...
└── manifest.txt
```

要求：

- `result.txt` 必须使用中文。
- `result.txt` 第一段必须是明确的定位结论。
- `result.txt` 第二部分给出关键分析依据。
- 只有存在日志缺失、nearest/unknown 匹配、V3 caveat 或证据不足时，才添加“证据缺口”部分。
- `logs/` 只放本次分析实际使用的目标进程日志。
- 复制日志时避免同名覆盖，文件名优先包含标签、module、slot、process_name 和 PID。
- `manifest.txt` 建议记录每份日志的标签、module、slot、process_name、pid、原始路径和匹配状态。

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
