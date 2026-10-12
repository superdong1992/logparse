# logparse 开发契约

本文件是仓库分析与开发入口。当前处于移交前精简阶段：所有者确认 LAN 无后续修改，并批准本轮破坏兼容的重构及技能更新。范围与约束见 ADR-0006；正式交接后，LAN 仓库仍是唯一权威来源，外部副本冻结，不接收 LAN 代码、配置、fixture 或日志。

## 开始与交付

先运行 `.venv/bin/python scripts/rule_preflight.py --paths <planned-paths...>`，阅读全部返回规则。实施前阅读 `docs/lan-development-guide.md`；涉及查询、诊断、CLI、DFX、产物时阅读 `docs/lan-dfx-operating-model.md`。

使用 `.venv` 中的 Python 3.12；Windows 使用 `.venv\Scripts\python.exe`。交付前运行：

```bash
.venv/bin/python scripts/verify_delivery.py
.venv/bin/python scripts/rule_preflight.py --changed
.venv/bin/python scripts/change_gate.py --changed
.venv/bin/python scripts/change_gate.py --changed --enforce \
  --change-record governance/changes/<change-id>.yaml
```

变更记录来自 `governance/changes/change-record.template.yaml`。测试、授权和案例均须如实记录，不能修改校验器或伪造记录绕过要求。不得把 mock 验证写成真实 LAN 验收。

## 按任务阅读

完成预检后，按 [AI 阅读入口](docs/ai-reading-guide.md) 选择所需材料。代码职责与真实调用链统一见 [架构](docs/architecture.md)；产品支持矩阵、字段语义和处理示例统一见 [产品日志规格](docs/product-log-specification.md)。分析结论须引用当前配置、实现符号或测试，并区分合成验证、真实 LAN 验收与尚未确认的业务语义。该要求适用于 GLM5.2 等局域网代理。

## 风险分区

精确路径以 `governance/architecture-boundaries.toml` 为准，红优先于黄、黄优先于绿，未知源文件默认红区。

- 绿区承载产品拓扑、格式、进程映射、诊断知识及相关测试；需聚焦测试和具体场景。
- 黄区承载生命周期及 Module1/Module2 关联，包括 PID/时间 fallback、unknown、midpoint、扩边和 clamp；业务语义变更须有真实案例、最小 fixture、历史 corpus 和 schema 结论。本轮无算法语义变更，按 ADR-0006 使用合成回归并明确真实 corpus 未执行。
- 红区承载契约、端口、编排、基础设施、CLI、产物与治理；需 Accepted ADR、明确授权、契约/安全/冒烟验证和回滚。用户已有明确授权时直接在该范围内执行。

产品目录并不全是绿区：引擎、产物写入器、metadata/result 序列化、查询和 DFX 是明确的红区例外。治理文件有不可降级的硬编码红区保护。

## 架构

处理链路：原始输入 → 安全工作区 → 产品发现与扫描 → 生命周期/关联 → 正式产物 → 确定性查询与限量 DFX → 诊断 skill。

依赖方向：`presentation -> application -> ports/contracts`，`infrastructure -> ports/contracts`。产品扩展依赖公开契约和应用服务；黄区策略可依赖产品模型。通用契约、应用和基础设施不得导入具体产品实现。

- `ParseService` 是唯一完整解析入口；根 `cli.py` 仅启动命令层。
- 运行时直接消费显式 schema v2 配置，不保留 v1 自动迁移、旧 Pipeline 或旧导入 façade。
- 机制声明 API 版本及依赖，只使用 `execute(MechanismContext) -> MechanismOutcome`。Module2 明确依赖 Module1。
- 解压器独占提取与路径安全职责；scanner 只检查已准备的工作区，普通 `.gz` 日志默认流式读取。
- `ArtifactLayout` 统一产物路径，正式 JSON 原子写入。

完整设计见 `docs/architecture.md`；架构决策保存在 `docs/adr/`。

## 业务与产物

Module1 只使用 `LifecycleSplitterV3`，算法标识为 `interval_v3`。空 CPU id 和 `CPU_Id=0` 是板级值，非零 CPU 才产生嵌套生命周期。Module2 复用 Module1 的板级与 CPU 生命周期，不独立切分。

V3 输出包含 `candidate_segments`、`merge_decisions`、`lifecycles`、`journal_evidence`、`issues` 和 `lifecycle_reliable`，不恢复 V2 行为。

正式产物为 `parse_manifest.json`、`metadata.json`、`result.json`、`mech_modules/`；性能分析另生成 `performance.json`；DFX 生成 `dfx_report.json`、`dfx_summary.txt`，深度模式有有效窗口时才生成 `dfx_context/`。

`metadata.json` 表示扫描覆盖，`result.json` 是紧凑查询索引，`mech_modules/` 保存证据。`extracted/` 是临时工作区。索引不得包含原始文本、上下文或逐行 `logs[]`；DFX 摘要只允许单行 `ERROR_CODE: 中文结论`。独立 logparse 不调用模型。

## 验证与数据边界

先运行聚焦测试，再运行完整交付检查。性能对比同时核对扫描文件、行数、机制条目和业务产物，不能把漏扫当成加速。真实日志留在 LAN，真实 corpus 和 2GB 包验收不能由小型样例替代。

除非用户指定具体任务，不检查 `output/` 或 `outputs/`。不把原始日志写入变更记录或外发材料。未经明确授权，不提交、推送、打 tag 或复制材料到仓库边界外。
