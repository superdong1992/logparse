# 问题定位 Wiki 推荐模板

使用 `wiki-to-diagnosis-skill` 生成问题定位 skill 时，优先让 Markdown wiki 接近下面的结构。Wiki 不需要填写本次日志输入路径、配置文件路径、输出目录、问题时间、slot、process_name 或 PID；这些是生成出的定位 skill 在运行时向用户收集的输入。执行生成时必须提供 wiki 和这个定位 skill 固定适用的具体 logparse `module_name`，例如 `EXAMPLE` 或 `MODULE2`。

## Frontmatter

```yaml
title: 链路超时问题定位
skill_name: diagnose-link-timeout
module_name: EXAMPLE
```

- `title` 是中文显示名，用于生成 skill 正文。
- `skill_name` 可选；提供时必须是英文小写短横线，建议格式为 `diagnose-<english-topic-slug>`，生成路径为 `.claude/skills/<skill_name>/SKILL.md`。
- `module_name` 是生成 skill 的固定业务/输出模块名，会写入 `SKILL.md` frontmatter；运行时不再要求用户填写模块。

## 问题范围

描述这个 wiki 适用于什么问题、现象或告警。

示例：

```markdown
适用于客户端向服务端发起请求后长时间未收到响应，或者响应日志显示 timeout 的问题定位。
```

## 目标进程角色

列出定位流程需要几组目标进程信息。每一行代表生成出的定位 skill 运行时必须收集的一组 `slot + process_name`，`pid` 可选；module_name 由 skill frontmatter 固定。

```markdown
| 标签 | 说明 | 是否必需 |
| --- | --- | --- |
| client | 发起请求的客户端进程 | 是 |
| server | 处理请求的服务端进程 | 是 |
```

规则：

- 标签用于关联多份日志，例如 `client`、`server`、`active`、`standby`。
- 不要把 `slot` 和 `process_name` 写成独立列表；它们必须属于同一目标进程。
- 如果 wiki 知道推荐进程名，可以写在说明里作为提示；不要把 module_name 写成每行运行时字段。

## 定位步骤

按执行顺序描述如何分析 `logparse-diagnose` 返回的 `target_logs[*].log_path` 指定模块日志。生成出的定位 skill 只允许读取这些路径，不允许遍历 `output/`、重新选择 lifecycle/cycle 或重新拼接日志路径；生命周期选择必须来自 `logparse-diagnose` 调用的 `cli.py mech-target-logs`。如果运行时 `input_path` 是原始日志输入，生成出的定位 skill 必须收集包含具体 YAML 文件名的 `config_path` 和明确的 `output_dir`，并交给 `logparse-diagnose`；不要只传配置目录，不要自行运行 parse。原始日志输入可以是日志压缩包、单个非压缩诊断日志，或原始日志目录。

```markdown
1. 在 client 日志中查找问题时间附近的请求发送记录，记录 request_id、序号和发送时间。
2. 在 server 日志中查找相同 request_id 或相邻时间窗口内的接收记录。
3. 如果 client 有发送但 server 没有接收，结论倾向于请求未到达服务端。
4. 如果 server 已处理并返回，但 client 没有收到响应，结论倾向于响应链路异常。
5. 如果两端日志都缺失关键记录，结论写为当前证据不足以确认根因。
```

## 判断规则

写明能支撑结论的关键证据。

```markdown
- 看到同一个 request_id 的发送和接收记录，才能建立跨进程关联。
- 超时时间以问题时间前后 5 分钟内的日志为主。
- 不能只凭单侧 timeout 文案直接判定根因。
```

## 输出要求

写明 `result.txt` 里需要呈现的结论字段或证据字段。`result.zip` 必须是扁平结构，根目录只包含 `result.txt` 和本次实际使用的进程 module 日志，不创建 `logs/`，不创建 `manifest.txt`，也不创建任何子目录。生成出的 skill 应使用 `.claude/skills/wiki-to-diagnosis-skill/scripts/pack_result_zip.py` 打包。

```markdown
- 先输出明确定位结论。
- 再输出关键分析依据，包括日志文件、时间点、关键行摘要。
- 进程日志文件必须来自 `target_logs[*].log_path`，压缩包根目录中文件名使用安全扁平文件名，例如 `<label>__<module_name>__slot_<slot>__<process_name>[-pid].log`；当 `target_logs` 含 `cpu_id` 时使用 `<label>__<module_name>__slot_<slot>__cpu_<cpu_id>__<process_name>[-pid].log`。替换路径分隔符和 Windows 非法字符。
- 如果证据不足，明确写“当前证据不足以确认根因”，并说明缺失哪份日志或哪类关键记录。
```
