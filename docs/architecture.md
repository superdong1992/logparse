# logparse 架构

logparse 是确定性的日志预处理与证据索引工具。通用核心描述 source、event、scope、result 和 diagnostic；slot、CPU、板卡、主备角色及 Module1/Module2 含义属于产品扩展。

本文按 `0ad92ff` 核对，日期为 2026-10-12，是仓库结构与调用链的统一说明。按任务选择阅读材料见 [AI 阅读指南](ai-reading-guide.md)，具体日志字段和支持范围见 [产品日志规格](product-log-specification.md)。链接指向当前实现及已有测试；后续修改调用链、接口或产物时须同步核对。

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
| `backend/presentation/cli/` | 命令、显示和组合入口 |
| `backend/parsing/` | 当前格式的逐行读取、字段提取、去重、ActivePeriod 和进程构造 |
| `backend/plugins/` | 产品发现/解析基类、插件加载；不是旧导入转发目录 |
| `configs/products/` | 产品的发现规则、字段格式和机制配置 |

`backend/extensions/diagnosis/` 是治理规则预留的产品知识位置，当前没有该目录。已有诊断入口是 [.agents/skills/logparse-diagnose/](../.agents/skills/logparse-diagnose/SKILL.md)，知识生成工具是 [wiki-to-diagnosis-skill](../.claude/skills/wiki-to-diagnosis-skill/SKILL.md)。生成后的业务知识正文应放在 `.agents/skills/diagnose-*/`，Claude 发现入口放在 `.claude/skills/diagnose-*/`；当前尚无具体业务的 `diagnose-*` 正文，不能把生成工具视为已具备产品故障知识。

风险分类并不等同于目录名：产品引擎、写入器、metadata/result 序列化、查询和 DFX 是红区例外，保护失败语义、产物完整性、schema、错误码及上下文限额。

## 唯一解析链路

`ParseService` 是完整解析入口，负责运行状态与产物会话；产品引擎管理工作区，完成产品发现、共享扫描、机制执行与结果组织，解析插件在扫描前验证机制依赖。根 [cli.py](../cli.py) 只启动命令层，不保留旧 Pipeline 或导入 façade。

```text
CLI parse -> build_parse_application -> ParseService.run
  -> CurrentProductParseEngine.execute
     -> 安全工作区 -> discovery.discover -> ParseResult
     -> ParserPlugin.parse
        -> 机制依赖检查 -> 共享 DiagnosticScanBatch -> ActivePeriod
        -> MechanismExecutionService.execute -> 各机制 execute
        -> 接收 MechanismOutcome -> 角色补全
     -> ParserPlugin.write_output -> metadata -> 清理工作区 -> 可选 profile
  -> result_to_dict -> result.json -> parse_manifest.json -> ParseRun
```

| 入口或边界 | 实际输入和输出 | 实现与验证依据 |
| --- | --- | --- |
| CLI `parse`、`build_parse_application` | 加载并校验配置，选择显式产品，组装 `ParseRequest` 与产品引擎；未传 `--product` 时使用 `default` | [parse](../backend/presentation/cli/parse.py)、[composition](../backend/presentation/cli/composition.py)；[闭环测试](../tests/test_parse_integration.py) |
| `ParseService.run` | `ParseRequest` → `ParseEngineOutcome` → `ParseRun`；应用层将产品状态视为不透明对象，调用注入的序列化器和产物会话 | [ParseService](../backend/application/parse_service.py)、[运行 DTO](../backend/contracts/runtime.py)；[服务测试](../tests/test_parse_service.py) |
| `CurrentProductParseEngine.execute` | 原始输入与 v2 产品配置 → 安全工作区、发现集合、产品解析结果；最后返回阶段、诊断和计数 | [产品引擎](../backend/extensions/products/current/engine.py)；[引擎测试](../tests/test_product_engine.py)、[工作区测试](../tests/test_parse_workspace.py) |
| `DirectoryDiscoveryPlugin.discover` | 准备好的工作区 → 产品自有的诊断槽位和私有日志集合 | [产品接口](../backend/plugins/base.py)、[default 发现](../backend/extensions/products/current/scanner.py)、[compact 发现](../backend/extensions/products/compact/scanner.py)；[扫描测试](../tests/test_scanner_plugin.py) |
| 产品 `ParserPlugin.parse` | 接收并原地更新可变 `ParseResult`，返回同一产品状态；共享扫描、ActivePeriod、机制编排、角色投影在此衔接 | [ParserPlugin](../backend/extensions/products/current/parser.py)、[产品模型](../backend/extensions/products/current/models.py)；[解析插件测试](../tests/test_parser_plugin.py) |
| `MechanismExecutionService.scan/execute` | 产品输入适配器、机制依赖结果、`DiagnosticScanBatch` → 各机制的 `MechanismOutcome`；产品解析器接收可用结果并写回产品状态 | [机制编排](../backend/application/mechanism_execution.py)、[CurrentMechanismInput](../backend/extensions/products/current/mechanism_input.py)；[机制执行测试](../tests/test_mechanism_execution.py) |
| 证据、metadata、result 与 manifest | `MechOutputWriter.write` 写机制证据；`MetadataGenerator.generate` 写扫描覆盖；服务调用 `result_to_dict` 写紧凑索引，再收集 hash 和清单 | [证据写入](../backend/extensions/products/current/artifacts.py)、[metadata](../backend/extensions/products/current/metadata.py)、[result](../backend/extensions/products/current/result_serializer.py)、[产物会话](../backend/infrastructure/parse_artifact_session.py)；[产物契约测试](../tests/test_artifacts.py) |

需要区分两层插件接口：产品解析接口仍是 `LogParserPlugin.parse(state)`，当前实现使用可变 `ParseResult`；精简时删除的是**机制插件**的旧可变 `parse(result)`。机制仅实现 `execute(MechanismContext) -> MechanismOutcome`，上下文包含受限的产品输入、明确的依赖结果和共享扫描批次，不暴露整个可变 `ParseResult`。对应验证为 `test_native_plugin_receives_no_mutable_parse_state_and_returns_full_outcome`。

缺失、禁用或成环的依赖在扫描前失败。Module2 声明 Module1 依赖，从 `dependency_results` 消费生命周期；YAML 顺序不定义执行关系。机制排序见 [ordered_mechanism_specs](../backend/application/plugin_graph.py) 及 [依赖图测试](../tests/test_plugin_graph.py)。[ScopeRef / CycleRef](../backend/contracts/scopes.py) 分别描述通用层级身份和周期身份；slot/CPU 由产品投影，[cycle_ref_for_interval](../backend/domain/correlation/identities.py) 生成稳定周期标识，不用 Python 对象 `id()` 或目录名代替。

## 阶段、失败与可信范围

下面的 `pipeline.*` 是当前引擎真实使用的阶段名，与 `pipeline` 配置段一样仍然有效；它们不表示仓内还有旧 `Pipeline` 类。引擎阶段见 [CurrentProductParseEngine._execute_in_workspace](../backend/extensions/products/current/engine.py)，服务的最后一步见 [ParseService.run](../backend/application/parse_service.py)。

| 阶段 | 输入 → 输出 | 失败代码或行为 |
| --- | --- | --- |
| `pipeline.prepare` | 检查任务归属和路径，再清理同一任务上次保留的产物 | 拒绝不安全路径时为 `LP_UNSAFE_TASK`，保留原任务；清理失败为 `LP_TASK_RESET_FAILED`。成功准备不单独写入阶段列表 |
| `pipeline.extract` | 目录、单文件或归档 → 准备好的工作区 | `LP_EXTRACTION_FAILED` |
| `pipeline.discovery` | 加载发现/解析插件，扫描工作区 → 诊断与私有日志集合 | `LP_DISCOVERY_FAILED` |
| `pipeline.parse` | `ParseResult` → 共享扫描、时间、机制结果及角色 | 抛出异常时为 `LP_PARSE_FAILED`；已捕获的文件或机制错误保留结构化诊断，并可能使整次运行变为 `partial` |
| `pipeline.write_output` | 每个机制结果 → `mech_modules/` 下的证据 | 每个有结果的机制记一条同名阶段；失败为 `LP_EVIDENCE_WRITE_FAILED` |
| `pipeline.metadata` | 已更新的产品状态 → `metadata.json` | `LP_METADATA_WRITE_FAILED` |
| `pipeline.cleanup` | 删除临时工作区 | `LP_WORKSPACE_CLEANUP_FAILED`；显式保留工作区时为 `skipped` |
| `pipeline.profile` | 性能记录 → `performance.json` | 只在启用 profile 时出现；写入失败为 `LP_ARTIFACT_WRITE_FAILED` |
| `artifacts` | 序列化产品结果 → `result.json`，随后完成 manifest | result 序列化或写入失败为 `LP_ARTIFACT_WRITE_FAILED`；该阶段不代表此前每个机制都成功 |

运行状态只有 `success`、`partial`、`failed`；运行时阶段成功值为 `succeeded`，写入 manifest 时映射成 `success`。`pipeline.parse` 成功只说明产品解析方法正常返回：文件读取或机制执行错误可以被捕获，所以阶段成功与整次运行 `partial` 可以同时出现。机制 `failed`、`empty` 或 `skipped` 没有可用依赖结果，下游跳过，独立机制仍可继续；显式 `partial` 且有结果时，该结果可供下游使用。依据是 [状态 DTO](../backend/contracts/runtime.py)、[产物会话映射](../backend/infrastructure/parse_artifact_session.py)、[机制依赖失败测试](../tests/test_mechanism_execution.py) 和 [部分失败传播测试](../tests/test_parser_error_reporting.py)。

读取产物时先看 manifest 的 `status`、`stages`、`diagnostics`，再看扫描覆盖和目标证据。hash 用于检查文件是否与清单一致，不证明日志齐全或生命周期判断正确。`partial` 可能仍有可用证据，但须说明受影响的来源和机制；`failed` 可能只保留失败前已写出的文件，不能因目录存在就视为完整解析。配置加载/校验在 CLI 前置失败时尚未创建本次清单；未知产品和不安全任务路径也不会覆盖原清单。已进入引擎的致命错误通常由服务写失败清单；若清单本身或其完整性收集发生 I/O 错误，不能保证失败清单能落盘。

针对这些边界的已有测试包括 [test_parse_service_records_artifact_failure](../tests/test_parse_service.py)、[test_fatal_stage_has_structured_diagnostic_and_cleans_workspace](../tests/test_product_engine.py)、[test_failed_repeat_drops_old_success_and_records_actual_workspace](../tests/test_parse_workspace.py) 和 [test_dfx_output_detects_manifest_integrity_mismatch](../tests/test_dfx.py)。

## 版本号各自约束什么

| 版本 | 约束对象与依据 |
| --- | --- |
| 配置 schema v2 | 根 `schema_version: 2`，由 [validate_config](../backend/config_validation.py) 验证；与输出 JSON 的版本独立 |
| 机制插件 API v1 | [PLUGIN_API_VERSION](../backend/contracts/plugins.py) 为 1，约束 `MechanismDescriptor`、`MechanismContext` 和 `MechanismOutcome`；不是旧产品配置 v1 |
| 产物契约 v1、manifest schema v1 | [ARTIFACT_CONTRACT_VERSION / PARSE_MANIFEST_SCHEMA_VERSION](../backend/contracts/artifacts.py) 都为 1，描述正式产物集合和运行清单 |
| metadata/result schema v2 | [METADATA_SCHEMA_VERSION](../backend/extensions/products/current/metadata.py) 与 [RESULT_SCHEMA_VERSION](../backend/extensions/products/current/result_serializer.py) 都为 2 |
| target_logs schema/API v1 | [TARGET_LOGS_SCHEMA_VERSION / TARGET_LOGS_API_VERSION](../backend/extensions/products/current/query.py) 都为 1，约束目标日志查询的结构化交接 |
| 生命周期 V3 | [LifecycleSplitterV3](../backend/domain/lifecycle/splitter_v3.py) 的算法标识 `interval_v3`；不是配置、插件或 JSON schema 的统一版本号 |

## 配置、提取与扫描

唯一配置格式为显式 schema v2：`pipeline` 保存运行控制，产品条目分别定义 `archive`、`discovery`、`parser`、`mechanisms`。根 `config.yaml` 为红区索引，产品内容保存在绿区 `configs/products/`，通过相对 `$include` 引用。

include 拒绝绝对路径、目录逃逸、内联与引用混用、缺失文件和非对象内容。引擎直接消费 v2；v1 迁移、旧结构投影和 `migrate-config` 已移除。

具体入口为 [load_config_file / runtime_config / explain_config](../backend/application/configuration.py)，include 和依赖顺序的验证见 [配置服务测试](../tests/test_configuration_services.py)。`product` 是配置选择键；`module_key` 是机制注册及依赖键；`module_name` 是产品日志里的模块名及产物命名依据，三者不能混用。两套配置如何复用插件见 [产品支持矩阵](product-log-specification.md)。

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

提取工作区默认临时使用，正式产物不能依赖它继续存在。独立预处理不调用模型。诊断技能生成器和 `result.zip` 打包作为独立集成工具保留，不能改变解析/目标选择行为，详见 [确定性 DFX 与 LAN 诊断边界](lan-dfx-operating-model.md)。

解析完成后的目标选择由 [ResultQueryService.resolve_target_logs](../backend/extensions/products/current/query.py) 完成；DFX 由 [build_dfx_output](../backend/extensions/products/current/dfx.py) 完成。二者消费正式产物，不重新执行机制或自行让模型搜索日志。默认 DFX 会读取证据计算完整性 hash，只有深度模式才提取限量正文；`dfx_context/manifest.json` 列出实际窗口。模型侧仅使用确定性返回的 `target_logs[*].log_path` 或获准的 DFX 窗口。`result.zip` 含诊断实际使用的证据，须留在批准的诊断环境内。对应依据为 [DFX 测试](../tests/test_dfx.py) 中的 `test_default_dfx_hashes_evidence_contents_without_deep_context`、`test_dfx_output_deep_writes_bounded_context_without_summary_leak` 及 [生成技能契约](../.claude/skills/wiki-to-diagnosis-skill/references/generated-skill-contract.md)。

同一任务重跑时，先检查既有 `parse_manifest.json` 的任务归属，再清理上述保留产物路径及 `extracted/`，避免旧证据、旧 DFX 或旧 profile 混入新结果。任务目录中的其他文件保留。没有有效归属记录的冲突路径、链接或输入/输出重叠会被拒绝，此时不覆盖既有清单。启用工作区保留后，即使解析失败，失败清单也会记录实际保留的工作区。

## 治理

`governance/architecture-boundaries.toml` 定义绿/黄/红路径，未知路径默认红区，门禁校验每次变更的证据。ADR-0006 授权本轮移交前兼容层清理，不降低校验强度，不修改黄色算法语义；没有真实案例或 corpus 时如实说明。正式交接后，LAN 仍是唯一权威仓库。
