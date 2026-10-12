# logparse Agent Instructions

当前处于移交前精简阶段。所有者已确认 LAN 无后续修改，并批准本轮破坏兼容的重构及 `logparse-develop` 更新，依据见 `docs/adr/0006-pre-handoff-simplification.md`。正式移交后以 LAN 仓库为唯一权威来源；不得将 LAN 代码、diff、fixture、配置或日志同步到外部副本。

## 开始工作

1. 完整阅读 `CLAUDE.md`。
2. 运行 `.venv/bin/python scripts/rule_preflight.py --paths <planned-paths...>`，阅读返回的全部规则来源。
3. 实施前阅读 `docs/lan-development-guide.md`；涉及诊断、查询、DFX、CLI 或产物时，再读 `docs/lan-dfx-operating-model.md`。

使用 `.venv` 中的 Python 3.12。原生 Windows 命令为 `.venv\Scripts\python.exe`，不要使用裸 `python`。

## 修改边界

`governance/architecture-boundaries.toml` 是路径分类的唯一依据，未知源文件默认红区。

- 绿区：产品拓扑、格式适配器、诊断知识及对应测试。slot、CPU、板卡和主备角色属于产品知识。
- 黄区：生命周期、Module1/Module2 关联策略。改变业务语义须有真实 LAN 案例、最小 fixture 和历史 corpus 回归。本轮仅清除兼容层，不改变算法，按 ADR-0006 如实记录合成回归与未执行的真实验收。
- 红区：契约、编排、文件系统安全、产物、CLI、治理。须有 Accepted ADR、明确授权、完整验证和回滚方案。已有授权可直接执行，不重复询问。

不得降低 `change_gate.py`、`rule_preflight.py` 或边界 TOML 的校验强度来通过门禁。

## 交付

```bash
.venv/bin/python scripts/verify_delivery.py
.venv/bin/python scripts/rule_preflight.py --changed
.venv/bin/python scripts/change_gate.py --changed
.venv/bin/python scripts/change_gate.py --changed --enforce \
  --change-record governance/changes/<change-id>.yaml
```

独立 logparse 必须保持确定性，不调用 Claude CLI 或模型。模型只读取结构化 DFX 与明确限量的上下文。原始日志不得进入 `result.json`、摘要或外发材料。除非用户要求检查具体任务，不访问 `output/` 或 `outputs/`。提交、推送、打 tag 和外传仍须用户明确授权。
