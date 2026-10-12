# logparse 开发与交接指南

当前是 ADR-0006 批准的移交前精简窗口。用户确认 LAN 尚无后续修改，允许移除旧兼容层；已有授权覆盖本轮计划，无须重复确认。正式交接以后，以 LAN 仓库为唯一权威来源，禁止向外同步 LAN 代码、配置、fixture 和日志。

## 开始修改

使用 `.venv` 中的 Python 3.12。以下命令使用 POSIX 路径；原生 Windows 将 `.venv/bin/python` 替换为 `.venv\Scripts\python.exe`。新环境先用 Python 3.12 创建虚拟环境，再安装 `requirements-dev.txt` 的锁定依赖。

```bash
git status --short
.venv/bin/python scripts/rule_preflight.py --paths <planned-paths...>
.venv/bin/python scripts/change_gate.py --paths <planned-paths...>
```

阅读 preflight 返回的全部规则来源，复制 `governance/changes/change-record.template.yaml` 建立本次记录。禁止把原始日志或私密内容写入记录。

## 按风险分区实施

| 最高分区 | 修改范围与证据 |
| --- | --- |
| 绿区 | 产品拓扑、格式适配、诊断知识；聚焦测试和具体产品场景 |
| 黄区 | 生命周期及关联策略；真实案例、最小 fixture、历史 corpus 回归和 schema 结论 |
| 红区 | 契约、编排、安全、产物、CLI、DFX、治理；Accepted ADR、明确授权、契约/安全/冒烟验证和回滚 |

精确路径由 `governance/architecture-boundaries.toml` 定义。未知源文件默认红区，产品目录内的引擎、产物、查询和 DFX 也有红区例外。不能为了通过门禁调整风险级别；治理文件另有硬编码红区保护。

本次移交前精简不修改黄色算法语义。允许以合成 fixture 验证接口和导入清理，但必须写明无真实 case、真实 corpus 未执行及 ADR-0006 的授权依据。未来业务语义变更仍按黄区要求验收。

## 维护单一路径

`ParseService` 负责完整解析，产品引擎消费 schema v2 配置。新增机制只实现 `execute(MechanismContext) -> MechanismOutcome`，不恢复旧 `Pipeline`、v1 配置转换、可变 `parse(result)` 或旧导入 façade。

产品拓扑与格式放在产品扩展中，生命周期和关联放在对应策略层。原始日志不进入 compact `result.json`。解压安全、产物写入、目标选择和 DFX 限额仍由通用契约约束。

诊断 skill 的正文保存于 `.agents/skills/diagnose-*/`，`.claude/skills/diagnose-*/` 只保留发现入口；生成器会校验正文与入口的一致性。

## 验证与交付

先跑最接近改动的测试，再执行：

```bash
.venv/bin/python scripts/verify_delivery.py
.venv/bin/python scripts/rule_preflight.py --changed
.venv/bin/python scripts/change_gate.py --changed
.venv/bin/python scripts/change_gate.py --changed --enforce \
  --change-record governance/changes/<change-id>.yaml
```

`verify_delivery.py` 执行 Ruff、compileall、配置检查、完整 pytest 与覆盖率门槛。提交范围检查可用 `--base <revision> --head <revision>`。规则预检与门禁共用变更路径发现，包含暂存、未暂存、未跟踪以及重命名前后的路径；Git 失败必须停止检查。

性能对比同时核对文件数、行数、机制条目和代表性产物，再比较耗时。记录版本、Python/机器、运行次数、中位数、差异和结论。小型 mock 的表现不能替代真实 2GB 包与历史 corpus 验收，真实日志及原始性能产物留在批准的证据目录。

常用只读检查为 `doctor`、`explain-config`、`artifact-check`；用 `scaffold-extension` 创建扩展骨架后，仍须分类和测试。配置只支持显式 schema v2，旧配置须人工更新，不再提供 `migrate-config`。

交付时汇报实际修改、风险分区、测试结果、schema 影响及尚未执行的真实验收。未经用户明确授权，不提交、推送、打 tag 或外传材料。
