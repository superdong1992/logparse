# 当前产品日志规格与支持情况

本说明描述代码目前能发现、提取、关联和查询的日志，不代替厂商的完整日志规范。实现核对基线为 `0ad92ff`，核对日期为 2026-10-12。`default`、`compact` 是配置中的产品键；仓内没有足够资料把它们对应到商业型号、软件版本或完整故障清单。

阅读代码先看 [AI 阅读入口](ai-reading-guide.md) 和 [架构说明](architecture.md)。配置是格式规则的来源，实现决定实际行为，测试说明已覆盖的场景；三者冲突时应核查并记录差异。

## 1. 支持范围与证据

本文使用三种证据状态：**合成验证**表示仓内测试或 mock 已覆盖；**实现依据**表示有代码或配置，但不等于真实产品验收；**待真实验证**表示尚缺真实 LAN 案例、历史 corpus 或厂商规格。两套产品均已覆盖“解析→正式产物→目标查询→DFX”的合成闭环，真实 LAN corpus 和真实大包验收未在本轮执行。

| 能力 | `default` | `compact` | 证据与状态 |
| --- | --- | --- | --- |
| 产品配置 | `configs/products/default.yaml` | `configs/products/compact.yaml` | [default 配置](../configs/products/default.yaml)、[compact 配置](../configs/products/compact.yaml)，实现依据 |
| 目录发现 | `ScannerPlugin`；`diag/`、`varlog/` | `CompactScannerPlugin`；`boards/`、`logs/` | [默认扫描器](../backend/extensions/products/current/scanner.py)、[compact 扫描器](../backend/extensions/products/compact/scanner.py)，合成验证 |
| 诊断日志机制 | `module1 / EXAMPLE / Module1Plugin`；另配置 `module2 / MODULE2 / Module2Plugin` | `ctrl / COMPACT / Module1Plugin` | 三项依次为 `module_key / module_name / 插件类`；配置与 [机制测试](../tests/test_module1_plugin.py)、[Module2 测试](../tests/test_module2_plugin.py) |
| 私有日志机制 | Module1 读取符合规则的 journal；Module2 只读取诊断日志 | `ctrl` 读取符合规则的 syslog，归一为 `source=journal` | [journal 扫描器](../backend/parsing/mech_journal_scanner.py)，合成验证 |
| 生命周期 | Module1 使用 V3；Module2 复用 Module1 周期 | `ctrl` 使用相同的 Module1 V3 实现 | [V3 测试](../tests/test_lifecycle_splitter_v3.py)、Module2 测试，合成验证 |
| 目标定位与 DFX | 按模块、slot、进程、可选 PID 和问题时间定位 | 使用 `--module ctrl` 或 `COMPACT` 定位 | [两产品闭环测试](../tests/test_parse_integration.py)，合成验证 |
| 主备与产品知识 | 主控关键字为空；进程映射、可靠进程、多实例进程列表为空 | 相同 | 配置占位状态，具体产品语义待真实验证 |
| 故障诊断知识 | 有生成与校验工具；无具体产品 `diagnose-*` 正文 | 相同 | 见第 7 节；不能宣称已有具体故障定位覆盖 |

`product` 选择整套发现、解析和机制配置；`module_key` 是该产品下的机制标识及依赖键；`module_name` 是业务模块名，也是证据目录名；插件类是实现。它们不是同一个概念。compact 复用 `Module1Plugin`，不表示其查询键变成了 `module1`。配置由根 [config.yaml](../config.yaml) 的 `$include` 加载；`parse` 未传 `--product` 时使用 `default`，不会自动识别日志所属产品。

默认 mock 只包含可命中的 Module1 数据，没有 Module2 数据。Module2 的提取、CPU 归属、PID fallback、unknown 和边界行为由独立合成测试覆盖；不能把配置中启用了 Module2 写成“该 mock 已验证 Module2 闭环”。

## 2. 输入、目录、文件与压缩

完整入口接受压缩包、原始目录或单个非压缩文件，但准备工作区成功不代表其中任意文件都能被发现。发现仍受产品目录和文件名规则限制。当前 `default` 配置不匹配任意 `.log` 文件，`compact` 则要求其目录中的 `debug_*.log`。依据为 [CurrentProductParseEngine._extract](../backend/extensions/products/current/engine.py) 和两套扫描器。

### default

典型包布局如下，内层压缩包由统一解压器准备，scanner 不自行解压：

```text
diagnostic_information_20260103.zip
├── diag/
│   ├── slot_1/
│   │   ├── diag.zip
│   │   └── diaglog_1_20260103070000.log.zip
│   └── slot_2/
│       └── diaglog_2_20260103070000.log.zip
└── varlog/
    ├── slot_1/varlog.zip
    ├── slot_1_cpu_0/varlog.zip
    └── slot_2/varlog.zip
```

- 在 `diag/slot_*/` 的直接子文件中匹配 `diag.zip`、`diaglog_*.log.zip`、`diaglog_*.zip`。读取内层压缩包时，使用统一解压产生的 `<文件名>_extracted/`，遍历其中的文件。
- `loose_diagnostics.enabled=true` 还会在整个工作区查找相同名称模式，登记到发现范围 `slot_loose`。机制条目的实际 slot 仍来自行内 `Slot`。已登记路径和解压后内容指纹用于避免重复发现。
- 在 `varlog/` 下匹配 `slot_*`、`slot_*_cpu_*`。扫描这些目录的子目录；如存在名称以 `varlog` 开头的后代目录，优先扫描这些目录，否则扫描该子目录。
- journal 文件名仅匹配 `journal.log`、`journal.log.*.gz`。`journal.log` 的文件轮转序号为 0，`journal.log.N.gz` 为 N；这与行内 `No[N]` 是两种序号。当前配置不匹配未压缩的 `journal.log.1`。
- 文件名中的 14 位时间按 `%Y%m%d%H%M%S` 解析为 `dump_time`，如 `20260103070000`。没有匹配或解析失败时为空；它不是行内事件时间，也不驱动生命周期切分。

依据：[ScannerPlugin.discover](../backend/extensions/products/current/scanner.py)、[文件名工具](../backend/utils.py)、[扫描器测试](../tests/test_scanner_plugin.py)。

### compact

```text
compact_package_20260103.zip
├── boards/
│   ├── slot_1/
│   │   ├── debug_main.log
│   │   └── debug_20260103.log
│   └── slot_2/debug_20260103.log
└── logs/
    ├── slot_1/syslog.log
    └── slot_2/syslog.log
```

- 在 `boards/slot_*/` 的直接子文件中匹配 `debug_*.log`；在 `logs/slot_*/` 下递归匹配 `syslog.log`。
- 同一个私有日志目录下，syslog 按文件名去重；多个不同子目录中的同名 `syslog.log` 不会全部登记。轮转序号固定为 0。
- 当前配置没有 syslog 轮转模式，也不匹配 `debug_*.log.gz` 或 `syslog.log.1.gz`。通用解压能力不等于这些文件已接入 compact 的发现规则。
- 文件名正则提取 8 位日期，但共享函数只接受 14 位时间；`debug_20260103.log` 当前得到 `dump_time=None`。行内时间仍正常参与解析，详见第 8 节待办。

依据：[CompactScannerPlugin](../backend/extensions/products/compact/scanner.py)、[compact 合成生成器](../tests/generate_compact_mock.py)、[compact golden](../tests/golden/compact.json)。

### 两产品共享的输入约束

两套 scanner 都先找工作区根目录下的目标目录，再找其一级子目录下的目标目录，不保证发现任意深度的包装目录。文件名 glob 匹配不区分大小写，行内正则和关键字过滤则按各自实现执行，不能推断为全部大小写不敏感。

两产品均启用递归解压，配置列出 `.gz`、`.zip`、`.tar.gz`、`.tgz`、`.tar`。普通内层 `.gz` 默认保留压缩态，由日志迭代器流式读取；`pipeline.debug_expand_gz` 默认关闭。扫描器只消费已经准备好的工作区。输入路径安全、归档安全和覆盖检查由 [产品引擎](../backend/extensions/products/current/engine.py) 与 [Decompressor](../backend/decompressor.py) 负责，验证见 [工作区测试](../tests/test_parse_workspace.py)、[解压测试](../tests/test_decompressor.py)。这些是通用能力；本节两套完整样例只覆盖 ZIP 包和 default 的 journal gzip。

## 3. 日志行与字段语义

以下两行来自仓内合成生成器，用于解释格式，不是真实产品日志：

```text
2026-01-03 00:01:00.100000 Service=SERVICE; Slot=1; CPU-Id=0; ProcessName=SERVICE-12345; Context=No[1] EXAMPLE init ok)
2026-01-03T00:01:00 COMPACT svc_master proc=svc_master-100 slot 1 cpu 0 |No[1] init complete
```

### Module1 与 compact 的共用提取模型

| 字段 | 提取与归一化 | 缺失或限制 |
| --- | --- | --- |
| `timestamp` | 共用正则查找 `YYYY-MM-DD[T或空格]HH:mm:ss[.小数][±HH:mm]`，交给 `datetime.fromisoformat`；机制条目取首个可解析时间 | 无可解析时间时为 `None`；不使用文件名时间补齐。正则接受一位月/日，但最终是否接受仍取决于 `fromisoformat`，不能只看正则承诺格式支持 |
| 时区 | 明确的 `±HH:mm` 会保留；Module1 先用诊断条目中首个已知时区补 journal 的无时区时间，再用全体条目首个已知时区补其余无时区时间 | 补齐使用 `replace(tzinfo=...)`，不是跨时区换算；全部无时区时仍无时区。当前正则未捕获 `Z`，不能把它当作已识别的 UTC 标记 |
| `slot` | 诊断日志取行内 `Slot`；default 对应 `Slot=...`，compact 对应 `slot N`。journal/syslog 取私有日志目录解析出的 slot | 来源目录仅记录为 `source_file`，不能代替诊断行内 slot；不从正文猜测缺失 slot |
| `cpu_id` | 诊断日志取 `CPU_Id` 命名组；journal/syslog 取目录中的 CPU。空值与 `0` 统一成板级空字符串 | 非零 CPU 才产生嵌套 CPU 生命周期；发现元数据中的原始 `cpu_id="0"` 仍可能保留 |
| `process_name`、`pid` | 诊断 `ProcessName` 的最后一个 `-数字` 拆为 PID，如 `SERVICE-12345`。journal 先用正则 PID 组；未捕获 PID 时，仅将名字末尾至少 3 位数字的 `-数字` 拆出 | 无 PID 时保留空字符串。两种来源的拆分规则有差异；不得把任意带短横线的名字拆开 |
| `sequence` | 诊断按 `sequence_pattern` 搜索，当前为 `No[N]`；journal 从匹配组读取 | 缺失或不能解析时为 0。journal 在可推导出无序号模式且整行没有 `No[` 时，可使用无序号 fallback；不是所有坏格式都能恢复 |
| `context` | default 取 `Context=...` 到行末 `)` 前；compact 取 `|` 后内容；journal 取正文匹配组 | 只用于内存中的提取、归一化和判定，不写入 `result.json` |
| `raw`、来源 | `raw` 为 `line.strip()[:500]`；`source` 为 `diagnostic` 或 `journal`，`source_file` 记录发现来源 | 证据文件写出的原行最多 500 个字符，不能宣称是任意长原始行的完整副本 |
| `Service` | 两套诊断正则都包含该组 | 当前 `MechDiagScanner` 不读取该组，也没有把它作为进程或生命周期标识 |

配置的时间正则仅负责匹配，具体日期合法性由解析函数判断。时间覆盖与机制条目分别补齐时区：[MechanismExecutionService.scan](../backend/application/mechanism_execution.py) 先从全部诊断来源中找到首个已知时区，为各来源 `content_timestamps` 中的无时区时间补齐，然后才构建 `ActivePeriod`；机制条目另按上表的 Module1 规则处理。两层都只补无时区值，已有偏移保留原样，全部无时区时仍无时区。真实接入应明确日志时区与时间格式，不能套用运行机器的时区推断。

依据：[MechDiagScanner.scan_line](../backend/parsing/mech_diag_scanner.py)、[MechJournalScanner.scan](../backend/parsing/mech_journal_scanner.py)、[JournalPatternMatcher](../backend/parsing/mech_journal_pattern.py)、[ProcessNameResolver](../backend/parsing/process_name_resolver.py)、[TimestampExtractor](../backend/parsing/timestamp_extractor.py)。已有测试覆盖 [进程名解析](../tests/test_process_name_resolver.py)、[有无时区时间](../tests/test_timestamp_extractor.py)、[无 No 的条目](../tests/test_module1_plugin.py) 和 [journal 匹配](../tests/test_mech_journal_pattern.py)。

### 过滤、读取与去重

- Module1 诊断行先要求整行含 `module_name.upper()`，再匹配 `diag_pattern`。此处关键字检查区分大小写。default 必须含 `EXAMPLE`，只有 `MASTER_CONFIRMED` 的行不会被提取。
- journal/syslog 先要求整行含大写模块名，再检查小写化后的 `identifying_keyword`，然后尝试 `line_pattern`、`line_pattern2`。`line_pattern2_required_substrings` 非空时，第二种模式还须命中列表中的至少一个子串；两产品当前均为空。
- UTF-8 普通文件读取使用 `errors="ignore"`，gzip 使用 `errors="replace"`，不提供任意编码自动检测。journal 读取失败时记录 `LP_JOURNAL_READ_FAILED`，保留已读到的条目；不能把未读取部分当作没有相关日志。
- 机制条目按时间、slot、CPU、进程、PID、Context、序号、主控信号与截断后的 raw 去重，保留首条；`source` 和 `source_file` 不参与去重。因此只有业务文字相似而时间或 raw 不同的条目不会被合并。文件发现时的内容指纹去重是另一层规则。

依据：[文件迭代器](../backend/parsing/file_iter.py)、[dedupe_mech_entries](../backend/parsing/mech_entry_dedup.py)、[去重测试](../tests/test_mech_entry_dedup.py)、[journal 读取错误测试](../tests/test_module1_plugin.py)。

### Module2 的差异

default 的 Module2 只扫描诊断行，先匹配区分大小写的 `identifying_keyword=xxx`，再提取 `Slot`、`CPU_Id`、`ProcessName`、带引号的 `Context`。slot 为 `frame/slot` 形式时取最后一段；进程格式为 `name[PID]`，不沿用 Module1 的 `name-PID` 规则；序号固定为 0。CPU 0 仍按板级处理，raw 同样最多 500 个字符。

Module2 先用自身条目的已知时区，缺少时才参考上游 Module1 周期；只补无时区时间。没有时间戳的条目保留在 unknown，不因 PID 相同就补造时间归属。依据：[Module2Plugin._scan_line、_normalize_timezones](../backend/extensions/mechanisms/module2.py) 与 [Module2 测试](../tests/test_module2_plugin.py)。`xxx` 是当前配置值，仓内没有资料证明它代表某个真实产品的完整模块标识。

## 4. 从时间覆盖到最终生命周期

### ActivePeriod 不是机制生命周期

`ParserPlugin.parse` 的共享扫描从发现的诊断文件提取时间戳，同时收集各机制的条目。`ActivePeriodBuilder` 按 slot 汇总全部诊断时间戳，相邻时间间隔**大于**阈值才断开：default 阈值为 300 秒，compact 为 7200 秒。这表示诊断文件的连续时间覆盖，也参与角色兜底，不是进程重启边界。

主控信号优先来自机制配置的 `active_master_keyword`。两产品当前都未配置；没有机制信号时，只有一个 slot 有 ActivePeriod 才被兜底判为 active，多个 slot 都有时保持 unknown。样例文字中的 `ACTIVE`、生成器打印的预期角色都不能替代当前判定结果。依据：[ParserPlugin.parse](../backend/extensions/products/current/parser.py)、[ActivePeriodBuilder](../backend/parsing/active_period_builder.py)、[RoleIdentifier.fallback_roles](../backend/parsing/role_identifier.py) 和 [角色测试](../tests/test_role_identifier.py)。

### V3 候选、合并与可靠性

Module1 总是执行 `LifecycleSplitterV3`，产物中标识为 `interval_v3`：

1. 按配置归一进程名，只用有时间戳的机制条目构建时间段。板级候选覆盖该 slot 的全部机制条目；非零 CPU 在所属板级生命周期内继续划分。
2. 相邻机制条目间隔**大于等于 30 秒**时建立候选边界，得到 `candidate_segments`。这与 300/7200 秒 ActivePeriod 阈值无关，也不表示已经确认重启。
3. 检查相邻候选：可靠进程的正序号从大变小，或合并后可靠单实例进程出现多个 PID，会阻断合并；否则允许合并。No 回绕比较使用归一后的进程名，可跨 diagnostic/journal，忽略 PID。板级判定只用板级条目的相关证据。
4. `lifecycles` 才是最终区间；`merge_decisions`、`journal_evidence` 说明依据，`issues` 和 `lifecycle_reliable` 记录冲突。最终区间内的可靠 PID 冲突会报问题，不会偷偷再次切分。
5. 按最终周期和 `(process_name, pid, cpu_id)` 组织进程日志。同一周期、同一进程及 CPU 下，只有唯一带 PID 的目标时，才把无 PID 的 journal 并入该进程文件；多个候选时保持独立。`missing_sequences` 只列出已观察最小到最大正序号之间的缺口，不推断序号 1 以前的缺失或真实丢包原因。

两产品的 `process_name_mapping`、`reliable_processes`、`multi_instance_processes` 目前为空。因此样例中的长时间静默候选会重新合并；`lifecycle_reliable=true` 只表示已配置规则未发现错误，不表示产品进程知识已完备。无时间戳条目可能已计入机制提取数量，但不进入 Module1 最终周期与进程证据，不能用提取数直接推断证据文件行数。

依据：[LifecycleSplitterV3](../backend/domain/lifecycle/splitter_v3.py)、[生命周期公共规则](../backend/domain/lifecycle/common.py)；重点测试为 [V3 测试](../tests/test_lifecycle_splitter_v3.py) 中的 `test_gap_30_seconds_initially_splits_then_merges_without_reliable_pid_conflict`、`test_reliable_pid_conflict_keeps_candidate_split`、`test_build_board_cycles_merges_pidless_journal_after_process_name_mapping`。

### Module2 关联与查询是两次不同的选择

Module2 在解析时复用配置依赖的 Module1 板级与 CPU 周期，没有独立 V3 切分器。归属会参考时间窗口、PID、相邻周期及已知进程；PID fallback 可能扩展 Module2 自己的输出边界，但须受相邻周期限制，不能跨越相邻周期或制造重叠。unknown 后续可能按唯一目标或最近且允许的目标归并，距离相同等无法确定的情况保留 unknown。完整细节以 [Module2 实现](../backend/extensions/mechanisms/module2.py) 的 `assignment_decisions` 及 [对应测试](../tests/test_module2_plugin.py) 为准。

`mech-target-logs` 则在解析完成后，从已有查询索引选择用户请求的进程文件。提供 PID 时必须严格匹配，不能因同名进程存在而忽略 PID。唯一时间命中返回 exact；没有命中时，唯一最近的带时间候选可返回 nearest；没有带时间候选且只有一个无时间候选时才返回 unknown。多个命中、最近距离相同或多个无时间候选均为 ambiguous；目标或证据文件缺失为 missing。必须保留 `caveats`；缺失或歧义时不能让模型用相关日志替代目标。路径由查询层返回，不能按目录名自行拼接。依据：[ResultQueryService.resolve_target_logs](../backend/extensions/products/current/query.py)、[目标选择策略](../backend/domain/correlation/target_selection.py) 和 [查询测试](../tests/test_query.py)。

## 5. 每种产物回答什么问题

| 产物 | 适合回答的问题 | 内容边界 |
| --- | --- | --- |
| `parse_manifest.json` | 本次执行了哪些阶段，是否成功，正式产物是否完整 | 阶段状态、诊断、计数、产物登记和 hash；不是原始日志 |
| `metadata.json` | 发现了哪些文件，哪些 slot 和时间范围被覆盖 | 产品键、发现范围、文件属性、ActivePeriod、角色与错误；保存稳定的包内逻辑路径，临时工作区清理后不能当成可直接读取的路径 |
| `result.json` | 有哪些模块、周期、进程和归属依据 | 紧凑查询索引，含 V3/Module2 结构化结果与计数；不保存 `raw`、`context`、逐行 `logs[]` 或提取工作区 |
| `mech_modules/` | 目标进程实际保留了哪些机制证据 | 按模块名、slot、板周期、可选 CPU 周期和进程写 `.log`；每行含序号与来源标记，随后是最多 500 字符的 raw |
| `dfx_report.json`、`dfx_summary.txt` | 工具和产物是否存在确定性错误 | 报告为结构化信息；摘要只能是单行 `ERROR_CODE: 中文结论`，不包含原始证据 |
| `dfx_context/` | 深度 DFX 明确允许模型读取哪些窗口 | 只有显式深度模式且有有效窗口时创建；manifest 记录窗口路径、行范围、hash、选择原因和截断情况 |

`metadata.json`、`result.json` 当前各自的 `schema_version` 为 2；通用 `artifact_contract_version` 为 1，不能把这些版本混写。`performance.json` 仅在启用 profile 时生成。默认不保留 `extracted/`；需要检查工作区时显式使用 `--keep-workspace`。

证据文件由 [MechOutputWriter](../backend/extensions/products/current/artifacts.py) 写入，序号有值时显示 `[0001]`，无序号时显示 `[....]`。例如 `[diagnostic|slot_1/diag.zip]` 表示来自哪个发现条目，不一定精确到内层压缩包中的文件名和原始行号。路径投影见 [CurrentProductEvidenceLayout](../backend/extensions/products/current/evidence_layout.py)，索引边界见 [result_serializer](../backend/extensions/products/current/result_serializer.py)、[MetadataGenerator](../backend/extensions/products/current/metadata.py) 与 [闭环测试](../tests/test_parse_integration.py)。

默认 DFX 会读取证据文件计算完整性 hash，并非完全不接触正文；它不把正文作为诊断上下文输出。`--deep` 才会提取确定性选中的目标日志窗口，最多 5 个，每个最多 48 行，总计最多 80 KiB。依据：[DFX 实现](../backend/extensions/products/current/dfx.py)、[DFX 测试](../tests/test_dfx.py)、[LAN DFX 边界](lan-dfx-operating-model.md)。

## 6. 两套可复核的合成闭环

以下命令从仓库根目录执行，仅使用仓内 mock，产物放在 `.pytest_tmp/`。Windows 使用 `.venv\Scripts\python.exe`；POSIX 环境将其替换为 `.venv/bin/python`。使用现有 ZIP 即可，不必重跑会重建样例目录的生成器。`--product` 属于 parse 参数；后续查询读取已生成任务，不需要重复传产品。

若执行环境禁止访问系统临时目录，可先在 PowerShell 中把临时目录设为仓内可写位置，再执行两个样例；这是环境设置，不改变日志规则：

```powershell
New-Item -ItemType Directory -Force .pytest_tmp/docs-smoke-temp | Out-Null
$env:TEMP = (Resolve-Path .pytest_tmp/docs-smoke-temp).Path
$env:TMP = $env:TEMP
```

### default：SERVICE-12345

```powershell
.venv\Scripts\python.exe cli.py parse tests/mock_data/diagnostic_information_20260103.zip -c config.yaml --product default -o .pytest_tmp/docs-smoke-default
.venv\Scripts\python.exe cli.py mech-target-logs diagnostic_information_20260103 -o .pytest_tmp/docs-smoke-default --problem-time 2026-01-03T00:01:30 --module module1 --slot 1 --process-name SERVICE --pid 12345 --explain
.venv\Scripts\python.exe cli.py dfx-output .pytest_tmp/docs-smoke-default/diagnostic_information_20260103
```

从输入到结果的核对点：

1. [合成源](../tests/generate_mock_data.py) 中，`diag/slot_1/diag.zip` 内的 `slot_1/diag_raw.log` 含第 3 节的 SERVICE 行。行内时间为 `00:01:00.100000`，归一后 slot 为 `1`、CPU 为板级、进程为 `SERVICE`、PID 为 `12345`、序号为 1。
2. 全包发现 3 个诊断条目、4 个 journal 文件，诊断时间戳共 106 个。机制结果为 3 条 diagnostic 加 3 条 journal；生成器里两条只有 `MASTER_CONFIRMED`、没有 `EXAMPLE` 的诊断行被机制预过滤，仍可贡献文件时间戳。当前未产生 Module2 结果。
3. slot 1 的 ActivePeriod 延伸到 `06:25:00.654321`，而 Module1 最终周期为 `00:01:00` 至 `00:02:00.200000`。两个候选段因没有可靠进程冲突而合并；同周期无 PID 的 SERVICE journal 并入唯一 PID `12345` 的进程文件，共 4 行。
4. 证据相对任务路径为 `mech_modules/EXAMPLE/slot_1/20260103000100-20260103000200/SERVICE-12345.log`。查询返回 `module_key=module1`、`module_name=EXAMPLE`、`match_status=exact`、`error_code=LP_TARGET_OK` 和实际绝对 `log_path`；CPU 周期为空。
5. DFX 返回 `LP_DFX_OK`。这表示产物结构检查通过，不是产品故障已排除。两 slot 的角色均为 unknown，不采用生成器末尾陈旧的 ACTIVE 预期文字。

数量与路径依据：[default golden](../tests/golden/default.json)；闭环测试为 [test_parse_service_artifact_query_and_dfx_closed_loop](../tests/test_parse_integration.py)。

### compact：svc_master-100

```powershell
.venv\Scripts\python.exe cli.py parse tests/mock_data_compact/compact_package_20260103.zip -c config.yaml --product compact -o .pytest_tmp/docs-smoke-compact
.venv\Scripts\python.exe cli.py mech-target-logs compact_package_20260103 -o .pytest_tmp/docs-smoke-compact --problem-time 2026-01-03T00:01:30 --module ctrl --slot 1 --process-name svc_master --pid 100 --explain
.venv\Scripts\python.exe cli.py dfx-output .pytest_tmp/docs-smoke-compact/compact_package_20260103
```

从输入到结果的核对点：

1. [合成源](../tests/generate_compact_mock.py) 的 `boards/slot_1/debug_main.log` 含第 3 节的 compact 行，归一为 `svc_master`、PID `100`、板级 CPU、序号 1。`logs/slot_1/syslog.log` 中同名无 PID 日志作为 journal 条目参与处理。
2. 全包发现 3 个诊断条目、2 个 syslog 文件，诊断时间戳共 24 个；`ctrl` 机制得到 5 条 diagnostic 加 3 条 journal。
3. slot 1 的 ActivePeriod 为 `00:00:00` 至 `06:25:00`；最终机制周期为 `00:01:00` 至 `03:00:00`。7200 秒只影响前者，V3 的 30 秒候选仍会产生并合并。该进程文件含 3 条 diagnostic 和 2 条 journal，共 5 行。
4. 证据相对任务路径为 `mech_modules/COMPACT/slot_1/20260103000100-20260103030000/svc_master-100.log`。查询返回 `module_key=ctrl`、`module_name=COMPACT`、`match_status=exact`、`error_code=LP_TARGET_OK` 和实际绝对 `log_path`，不是 `module1` 查询键。
5. DFX 返回 `LP_DFX_OK`。两 slot 的角色均为 unknown；`debug_20260103.log` 的 `dump_time` 为空，但行内时间及目标定位有效。

数量与路径依据：[compact golden](../tests/golden/compact.json)；同一 [两产品闭环测试](../tests/test_parse_integration.py) 验证此场景。2026-10-12 按以上命令复核，两组 parse、目标查询、默认 DFX 均通过；这是仓内合成验证，不是 GLM5.2 回答质量评估或真实 LAN corpus 验收。

## 7. 诊断知识、模型输入与结果打包

独立 logparse 不调用 Claude、GLM5.2 或其他模型。上述能力只负责确定性处理、定位与受限证据输出；具体故障判断需要产品知识和目标日志证据。

当前已有 [wiki-to-diagnosis-skill](../.claude/skills/wiki-to-diagnosis-skill/SKILL.md)、[生成契约与模板](../.claude/skills/wiki-to-diagnosis-skill/references/generated-skill-contract.md)、[validate_generated_skill.py](../.claude/skills/wiki-to-diagnosis-skill/scripts/validate_generated_skill.py) 和 [pack_result_zip.py](../.claude/skills/wiki-to-diagnosis-skill/scripts/pack_result_zip.py)。截至本次基线，`.agents/skills/` 中没有具体产品的 `diagnose-*` 诊断正文；有生成工具不等于已经接入产品 wiki 或覆盖故障规则。

生成后的诊断正文只放在 `.agents/skills/diagnose-*/SKILL.md`，同名 `.claude/skills/diagnose-*/SKILL.md` 只负责发现并加载同一正文。它们的名称、描述、effort 和模块元数据必须一致，不能另维护两套诊断流程。已有 [生成技能契约测试](../tests/test_generated_skill_contract.py) 校验这一点。

调用链是“加载 [logparse-diagnose](../.agents/skills/logparse-diagnose/SKILL.md) → 确定性查询 → 返回 `target_logs` → 按 wiki 规则解释目标日志”。生成技能只能读取 `target_logs[*].log_path` 指定证据，不遍历任务树、不重新选周期、不用相关日志补位。GLM5.2 如需通过宿主接入同一流程，仍须遵守 [LAN DFX 运行边界](lan-dfx-operating-model.md) 的结构化 DFX 和限量上下文约束；仓内没有 GLM5.2 调度器或已执行的模型评估记录。

`result.zip` 属于模型侧诊断交付物，不是 parse 自动生成的产物。根目录只放中文 `result.txt` 和本次实际使用的目标进程日志，日志使用安全扁平文件名；不建立 `logs/`、其他子目录或 `manifest.txt`。打包器校验扁平结构并使用固定 ZIP 时间，但文件是否来自 `target_logs` 仍须调用方按契约保证。它含日志证据，只能留在批准的诊断环境，不是可对外传递的单行 DFX 摘要。

## 8. 已知限制与后续接入清单

以下记录待确认项与修复状态。`LOGSPEC-002` 已补齐技能产品参数契约，CLI 默认行为保持不变。

| 项目 | 当前事实与影响 | 验证与后续要求 |
| --- | --- | --- |
| `LOGSPEC-001`：compact 文件名日期 | 配置捕获 8 位 `YYYYMMDD`，但 [extract_dump_time](../backend/utils.py) 使用 14 位 `%Y%m%d%H%M%S`；当前样例 `dump_time=None` | 先明确真实产品文件名规范及日期应代表的时间，再为适配补聚焦测试；不能未经规格确认就补零当作午夜 |
| `LOGSPEC-002`：诊断技能缺少产品参数（已修复） | [logparse-diagnose](../.agents/skills/logparse-diagnose/SKILL.md) 及生成模板已要求原始输入明确提供 `product`，并在预处理命令中传入 `--product`；缺失时先询问，已有结果直接复用 | 技能输入、交接字段及命令均受契约校验；CLI 仍默认 `default`，不自动识别产品。验证范围与合成回归结果见 [ADR-0008](adr/0008-diagnosis-product-selection.md) 及[变更记录](../governance/changes/2026-10-12-diagnosis-product-selection.yaml) |

真实产品接入还需补齐：型号与版本范围、实际目录及轮转样本、字段和时区约定、可靠与多实例进程列表、进程别名、主控信号、可确认的重启/倒换案例、历史 corpus 和具体故障 wiki。当前配置中的示例模块名及空列表不能由模型自行补成产品事实。

当产品配置、发现/提取规则、生命周期或关联策略、查询与产物契约变化时，同步更新本说明相应章节、证据链接、核对基线和样例预期。模型核对题放在 [AI 阅读入口](ai-reading-guide.md)，维护时同时检查；真实验收、模型评估和合成测试分别记录，不能互相替代。
