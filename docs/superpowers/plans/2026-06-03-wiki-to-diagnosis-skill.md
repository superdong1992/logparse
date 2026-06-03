# Wiki-to-Diagnosis-Skill 生成器

## Summary

创建 repo-local skill：`.agents/skills/wiki-to-diagnosis-skill`，用于把 Markdown 问题定位 wiki 转换成新的、中文的问题定位 skill。

生成出的定位 skill 运行时接收 `input_path`、问题时间、slot、可选 module/PID 等参数，把它们和 wiki 中定义的目标进程映射成 `logparse-diagnose` anchors。`input_path` 需要面向后续 logparse 能力设计：既可以是完整日志压缩包，也可以是诊断日志压缩包、单个诊断日志文件，或已经预处理好的 `output/{task_id}` 目录。

## Key Changes

- 新增 `wiki-to-diagnosis-skill/SKILL.md`，定义生成流程：
  - 读取 Markdown wiki；
  - 抽取目标进程、定位步骤、输出要求；
  - 生成 `.agents/skills/diagnose-<wiki-slug>/SKILL.md`；
  - 生成内容必须是中文，方便人工修改；
  - 校验生成出的 skill。
- 新增 `references/wiki-template.md`，定义推荐 wiki 结构：
  - 问题范围；
  - 运行时输入；
  - 目标日志/目标进程；
  - 定位流程；
  - 输出要求。
- 新增 `references/generated-skill-contract.md`，定义生成 skill 的中文规范和输入兼容策略：
  - `input_path` 表示“logparse 可处理的输入”，不限定为完整设备日志包；
  - 支持完整日志压缩包、诊断日志压缩包、单个诊断日志、预处理结果目录；
  - 生成出的 skill 不自己解析文件格式，只把输入交给 `logparse-diagnose`；
  - 如果当前 `logparse-diagnose`/logparse 版本尚未支持某类输入，生成出的 skill 用中文提示需要先升级/切换到支持版本。

## Generated Skill Contract

- 运行时输入：
  - `input_path`：完整日志包、诊断日志压缩包、单个诊断日志文件，或 `output/{task_id}`；
  - `problem_time`；
  - `slot`；
  - 可选 `module`；
  - 可选 per-target `pid`。
- Wiki 派生字段：
  - 目标进程标签；
  - 进程名；
  - wiki 中固定的 module；
  - 定位步骤；
  - 预期输出。
- 生成出的 skill 必须调用 `logparse-diagnose` 完成预处理和目标模块日志查询。
- 如果 module/slot/PID 不能唯一确定，必须用中文向用户补问，不能猜。
- 必须保留 `logparse-diagnose` 返回的日志路径、匹配状态、V3 caveat、缺失日志等证据。
- 除非日志证据直接支持，否则不能直接宣称根因，只能输出“证据指向/可疑点/需要继续确认”。

## Coordination With logparse-diagnose

- 后续 logparse 实现支持诊断日志压缩包和单个诊断日志后，需要同步更新 `.agents/skills/logparse-diagnose/SKILL.md`：
  - `Required Inputs` 中加入新的原始输入类型；
  - `Workflow` 中说明这些输入同样先进入 parse/preprocess，再从 `result.json` 和 `mech_modules/` 定位日志；
  - 保持 generated skill 只依赖 `logparse-diagnose` 的统一输入契约。
- `wiki-to-diagnosis-skill` 不直接绑定具体 parser 命令细节，避免后续 logparse 输入扩展时重复改每个生成出的定位 skill。

## Test Plan

- 用 Windows UTF-8 模式运行 `quick_validate.py` 校验新生成器 skill。
- 准备一个通用 Markdown wiki 样例。
- 按生成器流程产出一个样例 `diagnose-<wiki-slug>` skill。
- 校验样例生成 skill：
  - 正文为中文；
  - `input_path` 文档覆盖完整日志包、诊断日志压缩包、单个诊断日志、预处理目录；
  - 明确调用 `logparse-diagnose`；
  - 能把运行时输入转换成 anchors；
  - 对缺失字段、歧义、当前版本不支持的输入类型有中文提示。
- 用 `quick_validate.py` 校验样例生成 skill。

## Assumptions

- 新生成器 skill 放在 `.agents/skills/`。
- v1 只支持 Markdown wiki 文件，不支持 Confluence/web 页面抓取。
- Wiki 可以不包含本次查询参数；这些参数由用户运行生成出的定位 skill 时输入。
- 生成出来的问题定位 skill 面向人工维护，正文默认中文。
- 新输入类型的实际预处理能力由后续 logparse 和 `logparse-diagnose` 更新承接。
