---
name: logparse-develop
description: 开发、重构或审查 logparse，按产品、业务策略和架构风险分区完成修改与验证。适用于本仓代码、配置、CLI、DFX、产物及技能维护。
---

# 开发 logparse

先读 `CLAUDE.md` 和 `docs/lan-development-guide.md`。运行 `.venv/bin/python scripts/rule_preflight.py --paths <planned-paths...>`，阅读返回的全部规则，再运行 `scripts/change_gate.py --paths <planned-paths...>`。原生 Windows 使用 `.venv\Scripts\python.exe`，始终使用 `.venv` 中的 Python 3.12。

当前处于 ADR-0006 批准的移交前精简窗口：用户确认 LAN 无后续修改，允许本轮破坏兼容及更新本技能。按已批准范围实施，不为已有授权重复询问。正式移交后，LAN 仓库是唯一权威来源，不向外同步代码、配置、fixture 或日志。

## 决定修改位置

以 `governance/architecture-boundaries.toml` 为准，未知路径默认红区。

- 绿区：产品拓扑、格式、诊断知识；验证具体场景及聚焦测试。
- 黄区：生命周期与 Module1/Module2 关联；改变语义须有真实 case、最小 fixture、历史 corpus 和 schema 结论。本轮仅迁移接口/导入，按 ADR-0006 记录合成回归与未执行的真实 corpus。
- 红区：契约、编排、安全、产物、CLI、DFX 和治理；核实 Accepted ADR、明确授权、完整验证和回滚。产品目录中的引擎、写入器、序列化、查询及 DFX 也是红区。

不得降级风险分类或削弱门禁。处理新需求时先确认既有授权是否覆盖，只有缺少必需授权时才停止相关修改。

## 保持关键约束

`ParseService` 是完整解析入口，产品运行时直接消费 schema v2；机制只用原生 `execute(MechanismContext) -> MechanismOutcome`。不要恢复旧配置、旧 Pipeline、可变结果插件或导入 façade。

解压器负责提取与路径安全，scanner 只读准备好的工作区。Module1 拥有 V3 生命周期，Module2 复用；空 CPU 和 CPU0 是板级值。`result.json` 只保存查询索引。独立 logparse 不调用模型；查询、诊断、CLI、DFX 或产物修改前再读 `docs/lan-dfx-operating-model.md`。

## 验证和记录

在 `governance/changes/` 建立如实的 change record。先跑聚焦测试，再跑 `scripts/verify_delivery.py`、`scripts/rule_preflight.py --changed`、`scripts/change_gate.py --changed`，最后执行 `scripts/change_gate.py --changed --enforce --change-record <record>`。

性能结论须同时对比扫描计数与业务产物。真实 corpus 未执行时明确说明，不用 mock 冒充。除非用户指定具体任务，不读取 `output/` 或 `outputs/`。未经明确授权不提交、推送、打 tag 或外传。
