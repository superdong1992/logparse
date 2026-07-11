# logparse 交接前架构重构与 LAN-only 交付设计

状态：外部重构已完成，等待整体导入 LAN 后执行真实 corpus 验收。

## 1. 目的与权威边界

本次重构把通用架构、当前产品知识、受保护业务策略和模型诊断知识分开，
使 LAN 内的 Claude Code + GLM5.1 能在明确边界内继续演进，而不会因修改
slot/CPU/日志格式而误伤解压安全、产物契约、CLI 或 issue-locator 集成。

导入后：

- LAN 仓库是唯一权威版本；外部仓库冻结。
- 不存在 LAN 到外部的代码、diff、fixture、配置或日志同步。
- standalone logparse 始终确定性运行，不调用 Claude CLI 或 GLM5.1。
- 外部可见结论最多为单行 `ERROR_CODE: 中文结论`，不得包含原始日志。

## 2. 处理链路与分层

```text
原始日志
-> 产品拓扑与格式适配
-> 通用事件、范围和插件契约
-> 生命周期与关联策略
-> 查询和确定性 DFX
-> GLM5.1 诊断知识
```

通用 contracts/application/infrastructure 不认识 slot、CPU、Module1 或
Module2。当前产品通过 `ScopeRef`、`CycleRef` 和兼容 projection 保留现有
issue-locator/diagnosis 行为。

主要公共接口：

- `ParseRequest -> ParseRun`
- `DiscoveryContext -> DiscoveryResult`
- `DiagnosticScanBatch`
- `MechanismDescriptor`
- native `MechanismContext -> MechanismOutcome`
- `ScopeRef`、`CycleRef`
- `ArtifactLayout`、`ArtifactRepository`

新机制插件没有可变 `ParseResult`；只有 pre-v1 LAN 插件可经显式
`LegacyMechanismContext` 适配。Module2 通过拓扑图声明 Module1 依赖并消费
`dependency_results`，不依赖 YAML 顺序。

## 3. GLM5.1 修改权限

机器可读真相是 `governance/architecture-boundaries.toml`；红色优先于黄色，
黄色优先于绿色，未分类源文件默认红色。

### 绿色：LAN 日常产品区

```text
configs/products/**
backend/extensions/products/**
backend/extensions/diagnosis/**
tests/extensions/**
.agents/skills/diagnose-*/**
```

GLM5.1 可根据真实日志修改 slot/CPU/板卡拓扑、目录和文件名、glob/regex、
字段及进程映射、产品诊断知识，并补 focused tests 和 LAN 场景。

不能只根据父目录判断权限。当前产品的 engine、artifact writer、
metadata/result schema、legacy Pipeline、query 和 DFX 文件是 TOML 中列出的
红色例外；它们保护失败语义、正式产物、issue-locator、错误码和 DFX 预算。

### 黄色：真实证据驱动的业务策略区

```text
backend/domain/lifecycle/**
backend/domain/correlation/**
backend/extensions/mechanisms/**
.agents/skills/logparse-diagnose/**
```

这里包含 V3 生命周期、Module1/Module2、exact/nearest/unknown/tie、PID/time
fallback、midpoint、扩边和 clamp。只有真实 LAN case 证明现有规则错误时才改；
change record 必须记录 case id、最小 fixture、历史 corpus、schema 结论。

### 红色：冻结架构区

```text
backend/contracts/**
backend/ports/**
backend/application/**
backend/infrastructure/**
backend/presentation/**
governance/**
scripts/change_gate.py
scripts/rule_preflight.py
scripts/verify_delivery.py
cli.py
```

另有 TOML 中明确列出的兼容 façade、正式产物实现和仓库规则文件。红区修改
需要 Accepted ADR、人工批准、contract/security/smoke 全量验证和回滚方案。
不得修改门禁或分类来绕过当前变更。

## 4. 配置和扩展

`config.yaml` 是小型红色 schema-v2 索引；产品配置通过安全的相对
`$include` 放在绿色 `configs/products/`。include loader 拒绝绝对路径、目录
逃逸、inline/include 混用、缺失文件和非 object YAML。

`migrate-config` 用于 v1 到 v2 迁移；对 v2 根配置执行时保留 `$include`，
不会把绿色产品内容内联回红色根文件。`scaffold-extension --kind mechanism`
生成 native outcome 插件，不生成新的 `parse(ParseResult)` 插件。

## 5. 正式产物与实际消费者

```text
output/<task_id>/
├── parse_manifest.json
├── metadata.json
├── result.json
├── mech_modules/
├── performance.json       # 仅 --profile
├── dfx_report.json        # 仅 dfx-output
├── dfx_summary.txt        # 仅 dfx-output
└── dfx_context/           # 仅 --deep 且实际产生窗口
```

| 产物 | 生成条件 | 当前用途 |
| --- | --- | --- |
| `parse_manifest.json` | 始终 | 阶段状态、诊断、计数、路径/大小/hash；后续稳定任务入口 |
| `metadata.json` | 成功解析 | 扫描覆盖、产品拓扑和发现文件摘要；现有 info/slot 查询兼容 |
| `result.json` | 成功解析 | compact 查询索引；target resolution 和 issue-locator 兼容入口 |
| `mech_modules/` | 始终建目录 | 目标进程证据；diagnosis skill 的实际日志来源 |
| `performance.json` | `--profile` | 阶段耗时和扫描计数；manifest 校验并由 DFX 实际消费 |
| `dfx_report.json` | `dfx-output` | 确定性结构/索引/性能/目标诊断 |
| `dfx_summary.txt` | `dfx-output` | 单行错误码和中文结论 |
| `dfx_context/` | deep 且有目标 | problem_time 附近的受限窗口及相对路径/hash/行号 manifest |

已删除或降级的产物/模式：

- `extracted/` 不是正式产物；默认临时并清理，仅 `--keep-workspace` 保留。
- 不再有 full result、逐行 `logs[]`、raw/context/payload。
- 不再创建空 `dfx_context/`。
- `inner_extraction`、`output_base_dir`、`generate_metadata`、
  `result_json_mode` 已从 schema v2 删除。
- `result.zip` 仍由 issue-locator 管理，不属于 logparse。

所有正式 JSON、机制证据和 DFX 窗口通过 `ArtifactRepository` 原子写入；
当前产品的 slot/CPU 层级只由产品 evidence layout projection 生成。

## 6. 失败和兼容策略

- 配置、解压、发现、顶层解析、正式证据/metadata/result 写盘失败是 fatal，
  并生成带具体阶段和错误码的 failed manifest。
- 被隔离的机制失败可使任务为 partial；独立机制继续，依赖方不猜测结果。
- 插件缺失、禁用依赖、自依赖和循环依赖在扫描前失败。
- 查询拒绝显式未知 artifact schema，返回 `LP_SCHEMA_UNSUPPORTED`。
- `target_logs` envelope 的 schema/API version 均为 1。
- 旧插件类路径、根 `cli.py`、`parse`、`mech-target-logs`、`dfx-output` 和
  `logparse-diagnose` 入口保留。

## 7. 已完成的外部验收

- Python 3.12.13 与精确依赖版本。
- default/compact 两套 mock 的 ParseService、artifact-check、query、CLI、DFX
  和 determinism 闭环。
- 481 tests；overall line 87.98%，branch 73.90%，architecture core 92.03%。
- 10+10 次交错性能采样：基线 0.016062s，候选 0.017630s，回退 9.76%；
  files/lines/timestamps/Module1/Module2 计数全部一致。
- Ruff、compileall、配置检查、skill validators、rule preflight 和 enforced
  change gate 均通过。

精确命令和证据见
`governance/changes/2026-07-11-lan-handoff-refactor.yaml`。

## 8. LAN 导入后必须完成

- 用真实 corpus 验证产品拓扑、Module1、Module2、CPU0、嵌套 CPU、PID
  fallback、unknown 和扩边。
- midpoint 等未决黄色规则只由真实案例定案。
- 2GB 级真实包目标不超过 240 秒，且扫描计数不得因漏扫下降。
- 切换内部 remote，并在至少两份批准的 LAN 存储中保留副本。
- 任何黄色结论写入内部 change record；任何红色修改先走 ADR 和人工确认。

这些 LAN 项不能在外部用 mock 冒充已验证，也不要求把真实日志或 diff 回传。
