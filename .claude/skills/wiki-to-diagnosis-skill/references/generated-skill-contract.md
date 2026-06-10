# 生成出的定位 Skill 契约

本文件定义由 `wiki-to-diagnosis-skill` 生成的问题定位 skill 必须满足的行为。生成出的 skill 面向弱模型执行，必须使用固定阶段机：收集输入 -> 调用 `logparse-diagnose` -> 只读 `target_logs[*].log_path` -> 生成扁平 `result.zip`。

## 命名和 Frontmatter

- 路径必须是 `.claude/skills/diagnose-<english-topic-slug>/SKILL.md`。
- 目录名和 frontmatter `name` 必须完全一致。
- 名字只能包含英文小写字母、数字和短横线，长度少于 64 字符。
- 正文、运行时提问、分析结果和 `result.txt` 必须使用中文。
- 不要添加 Codex/OpenAI 专用的 `agents/openai.yaml`。
- 默认写 `effort: medium`，除非用户明确要求，不生成更高 effort。

Frontmatter 模板：

```yaml
---
name: diagnose-<english-topic-slug>
description: 用于<中文问题名>；必须先调用 logparse-diagnose skill 获取 target_logs，再只基于 target_logs[*].log_path 指定日志按 wiki 规则分析并生成扁平 result.zip。
effort: medium
module_name: <module_name>
---
```

## 必备章节

生成出的 skill 必须包含这些中文章节，顺序固定：

1. `问题范围`
2. `运行时输入`
3. `先调用 logparse-diagnose skill`
4. `证据收敛约束`
5. `Wiki 定位步骤`
6. `判断规则`
7. `Result.zip 交付物`
8. `最终回复`

## 运行时输入

全局输入：

- `input_path`：日志包或 `output/{task_id}` 预处理结果目录。
- `config_path`：repo 内 V3 配置文件路径，必须包含具体 YAML 文件名，例如 `config.yaml` 或 `configs/v3.yaml`，不要只传配置目录。`input_path` 是原始日志包时必填；已有 `output/{task_id}/result.json` 时不重新解析。
- `output_dir`：解析输出目录，例如 `output`；传给 `logparse-diagnose` 时必须明确。
- `problem_time`：问题发生的近似时间，保留用户提供的时区描述。

目标进程输入：

- 根据 wiki 定位流程收集 N 组目标进程信息。
- 生成 skill 的 frontmatter 必须包含固定 `module_name: <module_name>`；运行时不再向用户询问模块。
- 每组运行时目标必须包含 `slot`、`process_name`。
- 每组可选 `pid`；用户提供 PID 时只作为该组进程的严格匹配条件。
- 每组保留 wiki 标签，例如 `client`、`server`、`active`、`standby`。

必须写清楚：每组目标进程保持为同一条 `固定 module_name + slot + process_name + 可选 pid` 记录；不要把 `slot`、`process_name`、`pid` 拆成独立列表，也不要交叉组合不同目标进程字段。组装 targets[] 时必须使用 frontmatter 固定 module_name，并把该值写入每组 targets[].module，再合并每组运行时提供的 slot、process_name 和可选 pid。

## 日志获取阶段

生成出的定位 skill 必须包含语义等价内容：

```markdown
## 先调用 logparse-diagnose skill

`logparse-diagnose` 也是本项目里的一个 Claude skill，路径是 `.claude/skills/logparse-diagnose/SKILL.md`。

不要把 `logparse-diagnose` 当成 shell 命令、Python 模块或普通说明文字。必须先调用/加载这个 skill，让它完成：

调用时必须把 `input_path + config_path + output_dir + problem_time + targets[]` 一起交给 `logparse-diagnose`。如果 `input_path` 是原始日志包，不要省略配置文件路径，不要只传配置目录；预处理必须等价于 `python3.12 cli.py parse <package_path> -c <config_path> -o <output_dir>`。当前定位 skill 不要自行运行 parse。

1. 对 `input_path` 做预处理；
2. 根据每组 `固定 module_name + slot + process_name + 可选 pid` 生成 anchor；
3. 对每个 anchor 调用 `cli.py mech-target-logs`，由 logparse 确定性选择 lifecycle/cycle 并拼出目标日志路径；
4. 输出结构化 `target_logs` 清单，每个目标进程对应一个匹配结果。

组装 targets[] 时必须使用 frontmatter 固定 module_name，并把该值写入每组 targets[].module，再合并每组运行时提供的 slot、process_name 和可选 pid。

当前定位 skill 只分析 `logparse-diagnose` 返回的 `target_logs[*].log_path` 指定模块日志。
```

在 Claude 中使用时，优先通过项目级 Claude skill `$logparse-diagnose` / `/logparse-diagnose` 调用该能力；如果 Claude 没有自动加载该 skill，则读取 `.claude/skills/logparse-diagnose/SKILL.md` 并按其说明执行。

`target_logs[*].log_path` 是唯一允许读取的目标模块日志来源。如果某个目标的 `log_path` 缺失，必须把该目标日志视为缺失证据。

生成出的定位 skill 不允许直接运行 `cli.py parse` 或绕过 `logparse-diagnose` 处理原始日志包。

## Wiki 分析阶段

生成出的定位 skill 必须包含语义等价内容：

```markdown
## 证据收敛约束

禁止发散分析。只允许基于 `logparse-diagnose` 返回的 `target_logs[*].log_path` 目标模块日志和本 wiki 的定位规则给结论。
不要补充 wiki 未要求的排查方向。
不要分析无关模块、无关进程或无关代码。
不要遍历 `output/`，不要重新选择 lifecycle/cycle，不要重新拼接日志路径，不要用相关日志替代缺失的目标日志。
不要根据经验猜根因。
没有日志证据时，定位结论必须写“当前证据不足以确认根因”。
```

分析只按 wiki 的步骤和判断规则执行。除非日志证据直接支持，否则不能宣称根因；使用“证据指向”“可疑点”“仍需确认”等保守表述。

## Result.zip 交付物

分析完成后必须生成扁平 `result.zip`：

```text
result.zip
├── result.txt
├── <label>__<module_name>__slot_<slot>__<process_name>[-<pid>].log
├── <label>__<module_name>__slot_<slot>__cpu_<cpu_id>__<process_name>[-<pid>].log
└── ...
```

要求：

- `result.txt` 必须使用中文。
- `result.txt` 第一段直接给出定位结论；证据不足时写“当前证据不足以确认根因”。
- 只有存在日志缺失、nearest/unknown 匹配、V3 caveat 或证据不足时，才添加“证据缺口”。
- 每份日志必须来自 `target_logs[*].log_path`。
- 复制日志时使用安全扁平文件名，替换路径分隔符和 Windows 非法字符。
- 当 `target_logs` 含 `cpu_id` 时，zip 内日志文件名必须包含 `cpu_<cpu_id>`。
- 不要创建 `logs/`，不要创建 `manifest.txt`，也不要创建任何子目录。

确定性打包命令：

```bash
python3.12 .claude/skills/wiki-to-diagnosis-skill/scripts/pack_result_zip.py <临时目录> <result.zip路径>
```

脚本会拒绝子目录和 `manifest.txt`，并只把临时目录根目录下的文件打入 zip。

## Contract Validator

生成或修改 skill 后运行：

```bash
python3.12 -X utf8 <quick_validate.py> .claude/skills/<skill_name>
python3.12 -X utf8 .claude/skills/wiki-to-diagnosis-skill/scripts/validate_generated_skill.py .claude/skills/<skill_name>
```

如果生成了样例 `result.zip`，继续运行：

```bash
python3.12 -X utf8 .claude/skills/wiki-to-diagnosis-skill/scripts/validate_generated_skill.py .claude/skills/<skill_name> --result-zip <result.zip路径>
```

`validate_generated_skill.py` 必须检查：

- `effort: medium`。
- frontmatter 包含固定 `module_name`。
- 必备中文章节。
- `config_path` 和 `output_dir` 作为运行时输入。
- `config_path` 明确为包含具体 YAML 文件名的配置文件路径，不要只传配置目录。
- 原始日志包预处理通过 `logparse-diagnose` 使用 `-c <config_path>`。
- 每组目标输入使用固定 `module_name + slot + process_name`，`pid` 可选；module_name 来自 frontmatter，不来自用户运行时输入，并写入 `targets[].module`。
- `logparse-diagnose` 是另一个 Claude skill。
- `cli.py mech-target-logs` 负责目标日志选择。
- `target_logs[*].log_path` 是唯一日志来源。
- 明确禁止遍历 `output/`、重新选择 lifecycle/cycle、重新拼接日志路径、用相关日志替代缺失目标日志。
- 明确要求安全扁平日志文件名、替换 Windows 非法字符，并在有 `cpu_id` 时包含 `cpu_<cpu_id>`。
- 明确要求扁平 `result.zip`、`result.txt`、`pack_result_zip.py`。
