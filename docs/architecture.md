# logparse 架构

logparse 是确定性的日志预处理与证据索引工具。通用核心描述 source、event、scope、result 和 diagnostic；slot、CPU、板卡、主备角色及 Module1/Module2 含义属于产品扩展。

## 分层与依赖

```text
presentation -> application -> ports/contracts
infrastructure -> ports/contracts
产品扩展 -> 公开契约、端口与应用服务
生命周期/关联策略 -> 产品模型与公开契约
```

通用应用、契约和基础设施不得导入具体产品实现。基础设施实现端口，组合边界负责加载扩展。

| 包 | 职责 |
| --- | --- |
| `backend/contracts/` | 版本化 DTO、诊断、通用 scope 和插件 API |
| `backend/ports/` | 解析、机制和产物接口 |
| `backend/application/` | 完整解析服务、机制依赖图与用例编排 |
| `backend/infrastructure/` | 产物路径、仓储及隔离基础设施 |
| `backend/domain/lifecycle/` | 当前产品生命周期策略 |
| `backend/domain/correlation/` | 周期身份、关联与分配策略 |
| `backend/extensions/products/` | 产品拓扑、发现、解析和产物投影 |
| `backend/extensions/mechanisms/` | Module1/Module2 机制实现 |
| `backend/extensions/diagnosis/` | 产品诊断知识 |
| `backend/presentation/cli/` | 命令、显示和组合入口 |

风险分类并不等同于目录名：产品引擎、写入器、metadata/result 序列化、查询和 DFX 是红区例外，保护失败语义、产物完整性、schema、错误码及上下文限额。

## 唯一解析链路

`ParseService` 是完整解析入口，负责运行状态与产物会话；产品引擎管理工作区，完成产品发现、共享扫描、机制执行与结果组织，解析插件在扫描前验证机制依赖。根 `cli.py` 只启动命令层，不保留旧 Pipeline 或导入 façade。

```text
原始输入 -> 安全工作区 -> 产品发现 -> 诊断与私有日志集合
        -> 共享 DiagnosticScanBatch -> 按依赖排序的机制
        -> MechanismOutcome -> 产品产物投影 -> 原子写入与 manifest
```

- `ParseRequest` 描述输入、输出、产品和运行参数，`ParseRun` 返回状态、阶段、诊断与产物清单。
- `DirectoryDiscoveryPlugin.discover(workspace)` 返回产品自有的诊断日志集合与私有日志集合；不再保留没有独立消费者的通用发现 DTO。
- `DiagnosticScanBatch` 保存共享扫描结果和计数。
- 机制声明 API 版本、依赖、能力和配置 schema，只支持 `execute(MechanismContext) -> MechanismOutcome`。上下文包含明确的产品输入、依赖结果与扫描批次，不暴露可变 `ParseResult`，不保留 pre-v1 适配。
- `ScopeRef` 是通用层级身份；slot/CPU 由产品投影。`CycleRef` 是稳定周期身份，不用 Python 对象 `id()` 或目录名代替。

缺失、禁用或成环的依赖在扫描前失败。Module2 声明 Module1 依赖，从 `dependency_results` 消费生命周期；YAML 顺序不定义执行关系。

## 配置、提取与扫描

唯一配置格式为显式 schema v2：`pipeline` 保存运行控制，产品条目分别定义 `archive`、`discovery`、`parser`、`mechanisms`。根 `config.yaml` 为红区索引，产品内容保存在绿区 `configs/products/`，通过相对 `$include` 引用。

include 拒绝绝对路径、目录逃逸、内联与引用混用、缺失文件和非对象内容。引擎直接消费 v2；v1 迁移、旧结构投影和 `migrate-config` 已移除。

解压器独占归档展开和压缩内容安全校验；引擎将原始目录复制到工作区，拒绝符号链接、目录联接和输入/任务输出重叠。发现与机制 scanner 只检查准备好的工作区。普通 `.gz` 默认流式读取，完整展开仅用于显式调试。诊断日志尽量扫描一次并共享；性能验证须同时比较文件数、行数、机制条目及业务产物。

## 生命周期与产物

当前产品层级为 `slot -> board lifecycle -> 非零 CPU -> CPU lifecycle`。空 CPU 和 CPU0 属于板级。Module1 只使用 V3 `interval_v3`；Module2 复用其板级/CPU 周期，不独立切分。PID/时间 fallback、midpoint、unknown、扩边和 clamp 是受保护策略，语义变更必须由真实 LAN 证据支持。

`ArtifactLayout` 统一构造路径，`ArtifactRepository` 原子写入正式 JSON：

| 产物 | 职责 |
| --- | --- |
| `parse_manifest.json` | 状态、阶段、契约版本、hash、计数、诊断与工作区保留信息 |
| `metadata.json` | 发现与扫描覆盖 |
| `result.json` | 紧凑查询索引，禁止原始文本、上下文或逐行 `logs[]` |
| `mech_modules/` | 产品路径下的机制证据 |
| `performance.json` | 可选 profile 数据 |
| `dfx_report.json`、`dfx_summary.txt` | 确定性报告与单行摘要 |
| `dfx_context/` | 深度 DFX 的实际窗口及 manifest，没有窗口就不创建 |

提取工作区默认临时使用，正式产物不能依赖它继续存在。独立预处理不调用模型。诊断技能生成器和 `result.zip` 打包作为独立集成工具保留，不能改变解析/目标选择行为，详见 `docs/lan-dfx-operating-model.md`。

同一任务重跑时，先检查既有 `parse_manifest.json` 的任务归属，再清理上述保留产物路径及 `extracted/`，避免旧证据、旧 DFX 或旧 profile 混入新结果。任务目录中的其他文件保留。没有有效归属记录的冲突路径、链接或输入/输出重叠会被拒绝，此时不覆盖既有清单。启用工作区保留后，即使解析失败，失败清单也会记录实际保留的工作区。

## 治理

`governance/architecture-boundaries.toml` 定义绿/黄/红路径，未知路径默认红区，门禁校验每次变更的证据。ADR-0006 授权本轮移交前兼容层清理，不降低校验强度，不修改黄色算法语义；没有真实案例或 corpus 时如实说明。正式交接后，LAN 仍是唯一权威仓库。
