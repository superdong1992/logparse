# 确定性 DFX 与 LAN 诊断边界

## 仓库与数据

当前移交前重构按 ADR-0006 执行。正式交接后，LAN 仓库是唯一权威来源，禁止向外同步 LAN 代码、diff、fixture、产品配置或日志。

真实日志和完整诊断留在 LAN。批准向外传递的人工结论最多为一行 `ERROR_CODE: 中文结论`，不得包含原始日志、路径、私密标识或摘录。

## 预处理与诊断各自负责什么

独立 logparse 是确定性基础设施：准备安全工作区、发现日志、产出版本化索引与机制证据、选择目标生命周期/进程、生成错误码和限量 DFX。它不调用 Claude CLI、GLM5.1 或其他模型，模型可用性不影响解析和查询。

诊断模型只读取确定性步骤返回的材料：`dfx_report.json`、必要的 `parse_manifest.json`、`mech-target-logs` 返回的 `target_logs`，以及 `dfx_context/manifest.json` 明确列出的窗口。不遍历上传目录或整棵输出树，不自行选择生命周期或猜测日志路径，不用相关日志替代缺失目标。

仓内 `wiki-to-diagnosis-skill` 生成诊断知识，并提供扁平 `result.zip` 打包工具。这是模型侧的诊断集成流程，可供 issue-locator 使用；它独立于预处理和 DFX 运行时。生成技能只读取 `target_logs[*].log_path`，将中文结论和实际使用的目标日志打包。`result.zip` 含证据，留在批准的诊断环境，不能作为对外摘要。logparse 不承担模型调度。

诊断正文统一放在 `.agents/skills/diagnose-*/`，Claude 的 `.claude/skills/diagnose-*/` 只负责发现和加载同一正文。

## 默认 DFX 与深度 DFX

默认 DFX 读取结构化产物，并读取证据文件计算完整性哈希，但不会把正文放入诊断报告。提取诊断上下文属于深度 DFX，须显式开启；真实日志处理只在 LAN 内执行：

- 先确定性选中目标，再读取内容；优先围绕 `problem_time`，无法匹配时标明确定性 fallback。
- 最多 5 个窗口，每个最多 48 行，总量最多 80 KiB。
- manifest 记录相对路径、行范围、hash、选择原因、截断情况和 caveat。
- 没有有效窗口时不创建 `dfx_context/`。

`dfx_summary.txt` 始终为单行 `ERROR_CODE: 中文结论`，不包含原始证据。

## 入口与失败

当前诊断入口为 `parse`、`mech-target-logs`、`dfx-output` 和 `.agents/skills/logparse-diagnose/`。根 `cli.py` 负责启动命令层。本轮保留这些能力，但删除旧配置及 Python 兼容接口；不要求运行时保留已废弃调用方式。

配置无效、不安全解压、发现失败和产物写入失败会终止对应任务并生成结构化诊断。机制失败可允许独立机制继续，其依赖者须明确跳过。未启用 profile 时缺少性能文件是正常状态。目标缺失或歧义必须保持原状态，不由模型补造；模型超时、响应无效或错误码不匹配时，由调用方回退到确定性 `dfx_summary.txt`。
