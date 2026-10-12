# AI 阅读入口

面向在仓库内分析代码、维护产品适配或诊断日志的代理，包括 GLM5.2。实现核对基线为 `0ad92ff`（2026-10-12）；后续变更须重新核对所涉结论。先完整阅读 [CLAUDE.md](../CLAUDE.md)，按其中的 preflight 和风险分区要求工作。本页提供导航，不替代开发与数据边界。

## 按任务阅读

| 当前任务 | 阅读顺序 | 应拿到的依据 |
| --- | --- | --- |
| 解释代码逻辑或找修改位置 | [架构](architecture.md) → 真实调用链中的实现符号 → 对应测试 | 谁调用谁、输入输出、失败状态、规则归属 |
| 判断现有产品是否支持某类日志 | [产品日志规格](product-log-specification.md) → 所选产品配置 → scanner / parser → 测试 | 发现文件、提取条目、生成周期、输出证据、目标定位分别支持到哪一步 |
| 接入新产品或调整格式 | 产品日志规格 → [产品接入指南](product-onboarding-agent-guide.md) → [开发指南](lan-development-guide.md) | 已知格式与待确认语义；候选配置验证不能代替完整产品验收 |
| 定位某次故障的进程日志 | [DFX 边界](lan-dfx-operating-model.md) → [logparse-diagnose](../.agents/skills/logparse-diagnose/SKILL.md) → `mech-target-logs` 返回值 | 明确任务、产品、问题时间、目标锚点；只用确定性返回的路径 |
| 解释生命周期或关联 | 产品日志规格 → [生命周期 DFX](lifecycle-dfx-guide.md) → [生命周期实现](../backend/domain/lifecycle/splitter_v3.py) / [Module2](../backend/extensions/mechanisms/module2.py) | 候选段、合并决定、最终周期、归属与查询各自的证据 |
| 编写产品诊断知识 | [wiki 生成器](../.claude/skills/wiki-to-diagnosis-skill/SKILL.md) → [生成技能契约](../.claude/skills/wiki-to-diagnosis-skill/references/generated-skill-contract.md) | 一份诊断正文、发现入口、目标日志交接及证据打包边界 |

代码分析从当前跟踪的源文件开始；缓存中的旧 `.pyc`、Git 历史和旧回答都不能证明某接口仍存在。先限定问题所涉链路，再读相关文件，不必把整仓说明一次性装入上下文。

## 先确认名称与配置

`product` 选择产品配置；`module_key` 是该配置中的机制键；`module_name` 是业务模块名，也是证据目录名；插件类决定实现。例如 compact 对应 `ctrl / COMPACT / Module1Plugin`，不能因为类名含 Module1 就把查询键写死为 `module1`。

在仓库根目录运行以下只读命令核对环境和生效配置。以下为 Windows 命令；POSIX 使用 `.venv/bin/python`。

```powershell
.venv\Scripts\python.exe cli.py doctor -c config.yaml --json
.venv\Scripts\python.exe cli.py explain-config -c config.yaml -p default --json
.venv\Scripts\python.exe cli.py explain-config -c config.yaml -p compact --json
```

读取真实任务前，必须已有用户指定的任务范围。原始输入需要预处理时，显式选择产品，例如 `parse <input_path> -c <config_path> -o <output_dir> --product compact`。现有诊断技能及生成模板的预处理示例未传产品参数；CLI 会默认选择 `default`，不会凭 `config_path` 或日志内容自动选中 compact。详见[产品日志规格](product-log-specification.md)中的独立待办。

## 分析结论怎么写

- 先回答具体行为，再给“文件路径＋类/函数或配置键＋相关测试”。引用函数时核对函数体，不能只根据名称、注释或架构图推断实现。
- 区分三种证据：代码有此分支、合成测试已覆盖、真实 LAN corpus 已验证。当前两套产品有合成闭环验证，真实 corpus 验收未完成。
- 将实现事实、产品配置、算法策略和业务推断分开。空进程映射、空可靠进程表、`EXAMPLE` 或 `xxx` 等示例值不能补写成真实产品知识。
- 不足以判断时，写明缺少哪项依据。`nearest`、`unknown` 和 `lifecycle_reliable=false` 必须保留限制；`missing`、`ambiguous` 不能用相关日志替代。
- 文档与实现不一致时先定位差异并标明版本，不把旧解释当作要求去恢复旧接口。修改遵循现有风险分区；本入口不授权修改算法。

建议每次回答包含：**结论 → 配置/实现/测试依据 → 未验证项 → 最小核对方法**。代码理解任务引用实现符号即可；日志诊断只引用被选中的证据，不把原始正文放入索引、摘要或外发材料。

## 固定核对题

在 LAN 的 GLM5.2 会话中使用下表。先提供问题列，要求模型按上述格式回答，再由审阅者用参考列核对。若比较补文档前后的效果，使用同一代码版本、模型和参数，在两个新会话中分别不提供与提供本阅读入口，避免前一轮答案污染结果。每题同时满足“行为正确、依据可定位、没有扩大支持范围”才算通过。

| 编号与问题 | 参考要点与核对依据 |
| --- | --- |
| 1. 完整解析从哪里进入，谁写 result 和 manifest？ | `ParseService.run` 调用产品引擎，再通过产物会话写 result / finalize；根 CLI 仅启动命令。见[架构](architecture.md)、[ParseService](../backend/application/parse_service.py)。 |
| 2. schema v2 是否意味着插件和产物也都是 v2？ | 配置为 v2；插件 API、产物总契约和 manifest schema 为 1；metadata/result 各自的 schema 为 2；target_logs schema/API 为 1；生命周期标识为 `interval_v3`。见[插件契约](../backend/contracts/plugins.py)、[产物契约](../backend/contracts/artifacts.py)、[metadata](../backend/extensions/products/current/metadata.py)、[result 序列化](../backend/extensions/products/current/result_serializer.py)、[查询契约](../backend/extensions/products/current/query.py)。 |
| 3. 为什么仍能看到 `parse(result)` 和 `pipeline`？ | 产品 `ParserPlugin.parse` 仍有效；机制仅支持 `execute`。配置段及阶段名可用 `pipeline`，旧 Pipeline 类已删除。见[产品 parser](../backend/extensions/products/current/parser.py)、[产品引擎](../backend/extensions/products/current/engine.py)。 |
| 4. compact 为什么用 `ctrl` 查询？ | 配置键为 `ctrl`，模块名为 `COMPACT`，实现为 `Module1Plugin`；键、名字和类不是同一概念。见[compact 配置](../configs/products/compact.yaml)、[双产品闭环测试](../tests/test_parse_integration.py)。 |
| 5. 只提供根配置就会自动识别 compact 吗？ | 不会；CLI `--product` 默认 `default`，需要显式选择产品。诊断技能及生成模板要求原始输入明确提供 `product`，预处理显式传 `--product`；缺失时先询问，已有结果无需补传或重新解析。见[parse 命令](../backend/presentation/cli/parse.py)、[诊断技能](../.agents/skills/logparse-diagnose/SKILL.md)。 |
| 6. CPU0 是独立 CPU 生命周期吗？ | CPU0 和空 CPU 是板级值，只有非零 CPU 嵌套。见[诊断行扫描器](../backend/parsing/mech_diag_scanner.py)、[Module1 测试](../tests/test_module1_plugin.py)。 |
| 7. 30 秒、300 秒、7200 秒分别代表什么？ | 30 秒参与 V3 候选段切分，不直接证明重启；后两者是两套产品 ActivePeriod 的间隔配置。见[生命周期实现](../backend/domain/lifecycle/splitter_v3.py)、[ActivePeriodBuilder](../backend/parsing/active_period_builder.py)、[产品日志规格](product-log-specification.md)。 |
| 8. Module2 会独立切周期吗？ | 不会；它消费 Module1 结果，归属、扩边和查询选择属于不同步骤。见[Module2](../backend/extensions/mechanisms/module2.py)、[Module2 测试](../tests/test_module2_plugin.py)。 |
| 9. 指定 PID 找不到时能换成同名其他进程吗？ | 不能；PID 是严格附加条件。缺失或歧义保留原状态；nearest 仅在匹配的候选中选择并附 caveat。见[查询实现](../backend/extensions/products/current/query.py)的 `_process_matches` / `resolve_target_logs`、[目标选择策略](../backend/domain/correlation/target_selection.py)。 |
| 10. 有 wiki 生成器是否说明已有产品故障知识库？ | 只证明有生成/校验工具；核对基线未跟踪任何产品 `diagnose-*` 正文。正文应放在 `.agents/skills/diagnose-*/`，Claude 包装入口加载同一正文。见[生成技能契约](../.claude/skills/wiki-to-diagnosis-skill/references/generated-skill-contract.md)。 |
| 11. 默认 DFX、深度 DFX 和 result.zip 有什么区别？ | 默认校验结构与证据哈希；深度模式提取限量窗口；result.zip 包含诊断结论与使用过的目标日志，属于独立诊断集成，不能当作对外单行摘要。见[DFX 边界](lan-dfx-operating-model.md)。 |
| 12. 合成闭环通过是否证明所有产品日志都受支持？ | 不证明；需要区分文件发现、条目提取、证据输出和真实 corpus 验收。compact 的 8 位文件名日期仍不能被 14 位解析格式处理。见[产品日志规格](product-log-specification.md)、[extract_dump_time](../backend/utils.py)、[闭环测试](../tests/test_parse_integration.py)。 |

评估记录至少包括日期、提交、模型标识与参数、输入问题、每题结论/证据/未知项是否合格，以及误答对应的实现符号。真实日志不进入评估记录。发布本页不代表已经运行 GLM5.2 评估；实际结果记录在本轮 [change record](../governance/changes/2026-10-12-ai-reading-docs.yaml)。

## 维护入口

配置、插件接口、日志字段、生命周期、目标选择、产物或技能交接变化时，同步更新对应专题、核对题及基线。结构与职责统一在[架构](architecture.md)维护，日志事实统一在[产品日志规格](product-log-specification.md)维护，操作命令见[使用说明](usage.md)；不要复制出第二套规则。维护流程见[开发指南](lan-development-guide.md)，本次文档范围见 [ADR-0007](adr/0007-ai-documentation-evidence.md)。
