# Wiki-to-Diagnosis-Skill 生成器

## Summary

创建 Claude project skill：`.claude/skills/wiki-to-diagnosis-skill`，用于把 Markdown 问题定位 wiki 转换成新的、中文的问题定位 skill。

生成出的定位 skill 运行时分两阶段工作：先把用户输入的目标进程信息转换成 `logparse-diagnose` anchors，由 `logparse-diagnose` 完成预处理并输出 wiki 指定进程对应模块的日志文件；再由定位 skill 读取这些模块日志，按 wiki 中的定位步骤分析问题。分析完成后必须生成 `result.zip`，其中包含本次分析使用的进程日志和 `result.txt`。

## Key Changes

- 新增 `.claude/skills/wiki-to-diagnosis-skill/SKILL.md`，定义生成流程：
  - 读取 Markdown wiki；
  - 抽取定位步骤、目标进程数量、进程标签、判断规则、输出要求；
  - 生成 `.claude/skills/diagnose-<english-topic-slug>/SKILL.md`；
  - 生成内容必须是中文，方便人工修改；
  - 校验生成出的 skill。
- 新增 `references/wiki-template.md`，要求 wiki 描述定位流程需要几个目标进程，例如“客户端进程”“服务端进程”“主控进程”。
- 新增 `references/generated-skill-contract.md`，定义生成 skill 的职责和交付物：
  - `logparse-diagnose` 负责预处理输入、匹配目标进程、输出对应模块日志；
  - 生成出的定位 skill 负责消费这些模块日志，并按 wiki 规则分析问题；
  - 定位 skill 分析完毕后必须产出 `result.zip`。
- 新增 `.claude/skills/logparse-diagnose/SKILL.md` 作为 Claude project-skill wrapper，指向 repo 中已有的 canonical `.agents/skills/logparse-diagnose/SKILL.md`。
- 移除 `wiki-to-diagnosis-skill` 的 Codex/OpenAI 专用 `agents/openai.yaml`，避免 Claude skill 中混入无关元信息。
- 更新 `.gitignore`，继续忽略 `.claude` 下的本地配置，但允许提交 `.claude/skills/**` 项目级 Claude skills。
- 生成出的定位 skill 必须明确说明 `logparse-diagnose` 也是一个 Claude skill，并要求先调用/加载它；不能让弱模型误以为它是 shell 命令、Python 模块或普通提示词。

## Skill Naming Contract

- 生成器 skill 名固定为 `wiki-to-diagnosis-skill`。
- 生成出的定位 skill 名使用 `diagnose-<english-topic-slug>`。
- 生成器和生成出的定位 skill 都必须放在 `.claude/skills/`，以适配 Claude Code project skills。
- skill 目录名和 frontmatter `name` 必须使用英文小写、数字和短横线，少于 64 字符，并与目录名完全一致。
- 如果 wiki 标题是中文，生成器优先要求用户提供英文 `skill_name`；如果未提供，再从标题翻译/归一化成英文 slug，并在生成 skill 正文里保留中文显示名。
- 生成 skill 的正文、运行时提问、结果说明和 `result.txt` 必须使用中文。

## Generated Skill Contract

- 运行时全局必填输入：
  - `input_path`：完整日志包、诊断日志压缩包、单个诊断日志文件，或 `output/{task_id}`；
  - `problem_time`。
- 运行时目标进程输入：
  - 根据 wiki 定位实际需要收集 N 组目标进程信息；
  - 每组必须包含 `module`、`slot`、`process_name`；
  - 每组可选 `pid`；
  - 每组可带 wiki 派生标签，例如 `client`、`server`、`active`、`standby`。
- 日志获取阶段：
  - 每组目标进程信息转换成一个 `logparse-diagnose` anchor；
  - 在生成出的定位 skill 中先写清楚：`logparse-diagnose` 是另一个 Claude skill，路径为 `.claude/skills/logparse-diagnose/SKILL.md`；
  - 必须先调用/加载 `logparse-diagnose` skill，再做当前 wiki 的问题分析；
  - `module`、`slot`、`process_name` 必须来自同一组输入；
  - `slot` 和 `process_name` 不能拆成两个独立列表分别收集或交叉组合；
  - `pid` 只约束对应那一组进程；
  - `logparse-diagnose` 返回每个目标进程对应的模块日志路径、日志内容窗口、匹配状态和 V3 caveat。
- Wiki 分析阶段：
  - 定位 skill 必须读取并使用 `logparse-diagnose` 返回的模块日志；
  - 按 wiki 中的步骤检查日志中的状态、错误、请求响应、超时、序号、跨进程关联等证据；
  - 多个目标进程按 wiki 顺序关联分析；
  - 如果任一目标日志缺失，先用中文报告缺失证据，不能用相关日志替代目标日志。
- 交付物阶段：
  - 生成 `result.zip`；
  - 压缩包内必须包含 `result.txt`；
  - 压缩包内必须包含本次问题分析实际使用的目标进程日志；
  - `result.txt` 必须使用中文，内容顺序固定为：先给出明确定位结论，再给出关键分析依据；
  - 如果证据不足，结论也必须明确写成“当前证据不足以确认根因”，然后列出缺失证据和已观察到的关键依据。
- 除非日志证据直接支持，否则不能直接宣称根因，只能输出“证据指向/可疑点/需要继续确认”。

## Result Artifact Contract

- `result.zip` 推荐结构：
  - `result.txt`：中文分析结果；
  - `logs/`：本次分析使用的进程日志，文件名保留或补充目标标签，避免同名覆盖；
  - 可选 `manifest.txt`：记录每个日志对应的 `module`、`slot`、`process_name`、`pid`、原始路径和匹配状态。
- `result.txt` 最小结构：
  - `定位结论`：第一段给出明确结论；
  - `关键分析依据`：列出来自哪些日志、哪些时间点、哪些关键行或状态变化；
  - `证据缺口`：仅在日志缺失、匹配近似、V3 caveat 或证据不足时出现。
- 生成出的 skill 必须在最终回复中说明 `result.zip` 的路径。

## Coordination With logparse-diagnose

- 后续 logparse 支持诊断日志压缩包和单个诊断日志后，需要同步更新 `.agents/skills/logparse-diagnose/SKILL.md` 的输入类型说明。
- `wiki-to-diagnosis-skill` 和生成出的定位 skill 依赖 Claude project skill `$logparse-diagnose` / `/logparse-diagnose` 提供统一的预处理与目标日志输出契约。
- 生成出的定位 skill 可以分析模块日志内容，但不能绕过 `logparse-diagnose` 自己选择 lifecycle、cycle 或输出路径。

## Test Plan

- 用 Windows UTF-8 模式运行 `quick_validate.py` 校验新生成器 skill。
- 准备一个包含两个目标进程和明确分析步骤的 Markdown wiki 样例。
- 生成样例 `diagnose-<english-topic-slug>` skill。
- 校验样例生成 skill：
  - skill 名和目录名为英文小写短横线；
  - skill 路径位于 `.claude/skills/`；
  - 正文为中文；
  - 明确按目标进程记录输入 `module + slot + process_name`；
  - 不允许把 `slot` 和 `process_name` 拆成独立列表；
  - 能转换多组目标进程为多个 `logparse-diagnose` anchors；
  - 明确告诉执行者 `logparse-diagnose` 也是一个 Claude skill，必须先调用/加载它；
  - 明确把 `logparse-diagnose` 输出作为后续问题分析输入；
  - 明确要求生成 `result.zip`；
  - `result.zip` 包含 `result.txt` 和本次分析使用的进程日志；
  - `result.txt` 先给出明确定位结论，再给出关键分析依据；
  - 对缺失整组信息、目标日志缺失、当前版本不支持的输入类型有中文提示。
- 用 `quick_validate.py` 校验样例生成 skill。

## Assumptions

- 新生成器 skill 放在 `.claude/skills/`，供 Claude Code 作为 project skill 发现和执行。
- v1 只支持 Markdown wiki 文件，不支持 Confluence/web 页面抓取。
- Wiki 决定需要几个目标进程信息；用户运行生成出的 skill 时按这些目标提供对应组数。
- 生成出来的问题定位 skill 面向人工维护，正文默认中文。
- 新输入类型的实际预处理能力由后续 logparse 和 `logparse-diagnose` 更新承接。
