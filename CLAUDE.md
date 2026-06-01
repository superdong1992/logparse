# CLAUDE.md

## Rule preflight is mandatory

Before repo analysis or code edits, run the local rule preflight for the files
you will inspect or change:

```bash
python scripts/rule_preflight.py --paths backend/parsing/lifecycle_splitter.py
python scripts/rule_preflight.py --changed
```

Read every returned rule source before making claims or edits, and mention the
rule ids you used in the final answer. In particular, do not infer CPU scope:
`CPU_Id=0 is board-level`; only non-zero CPU ids are nested CPU lifecycles.

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

日志解析维护工具，用于预处理产品设备的日志压缩包。支持多层解压，发现诊断日志和私有日志（varlog），通过可配置的机制模块优先判定主控，兜底通过目录+时间戳推断。输出结构化元数据供 AI agent 消费。

## 运行命令

```bash
# 安装依赖
pip install -r requirements.txt

# CLI 解析（默认使用 default 产品插件管道）
python cli.py parse <package_path> [-c config.yaml] [-o ./output] [--verbose] [--lifecycle-dfx errors|summary|decisions|full|off] [--product default|compact] [--debug-expand-gz]
python cli.py info <task_id>
python cli.py list-slots <task_id>
python cli.py query-diag <task_id> -s <slot_id>
python cli.py mech-slots <task_id> [-m <module_name>]
python cli.py mech-lifecycles <task_id> -s <slot_id> [-m <module_name>]
python cli.py mech-logs <task_id> -s <slot_id> -c <board_cycle_dir> -p <proc_name-pid> [-m <module_name>] [--cpu <cpu_id> --cpu-cycle <cpu_cycle_dir>]

# 调试工具
python cli.py check-config [-c config.yaml]            # 检查配置有效性
python cli.py test-pattern -m module1 -t diag "日志行"  # 测试正则匹配
python cli.py test-pattern -m module1 -t journal "日志行"

# 测试
python -m pytest tests/ -v

# 生成测试数据
python tests/generate_mock_data.py
python cli.py parse tests/mock_data/diagnostic_information_20260103.zip
```

### 调试能力

- **`check-config`**：检查所有正则可编译、glob 有效、模块配置完整、插件类继承基类、关键方法存在，错误和警告分开
- **`test-pattern`**：用配置正则测试实际日志行，显示提取字段、Stage1 预过滤结果、`line_pattern2_required_substrings` 命中结果、时间戳、主控关键字命中
- **错误隔离**：每一步失败不终止全流程，继续执行并在最后汇总所有错误
- **`--verbose`**：输出每步耗时、处理项数、机制模块 诊断/journal 条数对比、同名进程多实例检测；不展开生命周期聚合/切分详情
- **`--lifecycle-dfx`**：控制生命周期中文 DFX 输出，`parse` 默认 `errors`，`mech-lifecycles --show-boundaries` 默认 `summary`；`decisions/full` 展开 V3 候选切分和聚合原因
- **`--debug-expand-gz`**：调试用，将普通 `.gz` 日志就地展开（如 `journal.log.1.gz` → `journal.log.1.gz_extracted/`），正式批量解析不建议开启
- **`--module` / `-m`**：`mech-slots` / `mech-lifecycles` 不传时展示全部模块；`mech-logs` 不传时默认取第一个机制模块
- **`--cpu` / `--cpu-cycle`**：`mech-logs` 查询嵌套 CPU 周期日志时使用；路径为 `.../<board_cycle>/cpu_<id>/<cpu_cycle>/<proc>.log`
- **Windows 编码**：CLI 入口自动将 stdout/stderr 切换为 UTF-8，避免 GBK 编码下 Unicode 符号报错

## 架构

### 回答规则

- 回答问题必须基于代码事实，先读代码再回答，不能凭想当然
- 给出调试建议前，必须先确认相关代码的实际行为

### 数据流

```
外层压缩包 → Decompressor(按配置统一解压外层和内层归档；普通 .gz 默认保留)
→ DirectoryDiscoveryPlugin(发现 slot+文件，产品插件)
→ LogParserPlugin(时间戳→ActivePeriod→机制模块→角色判定，产品插件)
→ MechOutputWriter(板卡周期 + 嵌套 CPU 周期落盘) → MetadataGenerator(JSON输出)
```

关键设计决策：
- **解压与扫描分离**：`Pipeline` 在 Step 1 通过 `Decompressor.extract_all()` 统一处理外层和内层归档；`config.yaml` 默认 `recursive_extraction: true`。Scanner 插件只扫描统一解压后的工作区，不再承担中间内层解压阶段。
- **普通 `.gz` 流式读取**：默认不展开普通 `.gz` 日志（如 `journal.log.1.gz`），parser 直接流式读取；仅 `--debug-expand-gz` 或配置 `debug_expand_gz: true` 时才展开。`.tar.gz` / `.tgz` 归档不受此控制
- **机制模块优先主控判定**：indicator 进程 PID 变化 + 序号回绕反向扫描确定重启边界
- **时区对齐**：诊断日志时间戳含时区（如 `+08:00`），journal 不含。从全部条目中检测时区并归一化所有 naive timestamp
- **journal 双正则 fallback**：`line_pattern` 匹配完整元数据格式，`line_pattern2` 兜底匹配无元数据块格式；`line_pattern2_required_substrings` 可对 `line_pattern2` / 自动无序号 fallback 增加大小写敏感整行字符串约束
- **嵌套生命周期输出**：`MechBoardCycle` 是顶层板卡生命周期；`MechCpuCycle` 嵌套在对应板卡周期下，保存 CPU-local 重启周期、split trace 和进程生命周期。

### 模块分工

| 模块 | 职责 |
|------|------|
| `backend/decompressor.py` | .zip/.tar.gz/.gz 多层递归解压，含路径穿越、zip 炸弹、递归深度安全防护 |
| `backend/pipeline.py` | Pipeline 类：产品无关的 6 步管道编排器，按产品名加载插件对 |
| `backend/plugins/base.py` | 两个 ABC：`DirectoryDiscoveryPlugin`、`LogParserPlugin` |
| `backend/plugins/loader.py` | 动态加载插件：`instantiate_plugin(class_path, base, config)` |
| `backend/plugins/default/scanner.py` | ScannerPlugin：标准 diag/ + varlog/ 目录发现 |
| `backend/plugins/default/parser.py` | ParserPlugin：解析编排层，委托给 backend/parsing/ 各组件 |
| `backend/plugins/compact/scanner.py` | CompactScannerPlugin：boards/ + logs/ 布局发现 |
| `backend/config_validation.py` | 机制模块配置校验：正则合法性、命名组完整性、白名单冲突检测 |
| `backend/parsing/timestamp_extractor.py` | TimestampExtractor：从文本/文件/LogEntry 提取时间戳，处理 .gz 和 UTF-8/GBK |
| `backend/parsing/active_period_builder.py` | ActivePeriodBuilder：从时间戳序列构建连续主控时段段 |
| `backend/parsing/process_name_resolver.py` | ProcessNameResolver：诊断日志和 journal 的进程名/PID 解析与映射 |
| `backend/parsing/mech_diag_scanner.py` | MechDiagScanner：诊断日志流式逐行扫描，提取机制模块条目 |
| `backend/parsing/mech_journal_scanner.py` | MechJournalScanner：journal 日志流式逐行扫描，提取机制模块条目 |
| `backend/parsing/file_iter.py` | 流式文件读取迭代器：`iter_text_file_lines`、`iter_log_entry_lines` |
| `backend/parsing/cycle_detector.py` | CycleDetector：PID 变化 + 序号回绕反向扫描的重启周期检测，含 split trace |
| `backend/parsing/role_identifier.py` | RoleIdentifier：机制模块优先 + 保守兜底角色判定 |
| `backend/parsing/output_writer.py` | MechOutputWriter：slot/板卡周期/cpu_N/CPU周期 嵌套目录落盘 |
| `backend/query.py` | ResultQueryService：封装 result.json/metadata.json 的读取与查询 |
| `backend/metadata.py` | 生成 `metadata.json`（含 mech_results 键） |
| `backend/models.py` | Pydantic 数据模型（SlotInfo、ParseResult、MechResult 等） |
| `backend/utils.py` | 纯函数工具：glob_to_regex、时间戳提取、文件读取等 |
| `cli.py` | Click CLI：parse/info/list-slots/query-diag/mech-slots/mech-lifecycles/mech-logs/check-config/test-pattern，mech 查询命令支持 `--module` 按模块过滤，`mech-logs` 支持 `--cpu` / `--cpu-cycle` |

### 核心模型 (`backend/models.py`)

- `SlotInfo` — 诊断日志槽位，含 `diagnostic_logs`、`active_periods`、`role`、`board_type`
- `LogEntry` — 单个日志文件，含 `dump_time`(文件名转储时间)、`content_timestamps`(内容时间戳)
- `ActivePeriod` — 连续主控时段段 (start/end)
- `PrivateSlotInfo` + `JournalLogFile` — 私有日志槽位和 journal 文件元数据
- `MechResult` / `MechSlotOutput` / `MechBoardCycle` / `MechCpuCycle` / `MechProcessLifecycle` / `MechLogEntry` — 机制模块输出结构，进程按 `(process_name, pid, cpu_id)` 分组；CPU 进程嵌套在 `MechBoardCycle.cpu_cycles[]`
- `MechCycleSplitTrace` — 重启周期切分原因追踪（reason、old_pid、new_pid、timestamp）
- `MechBoundaryIssue` — 生命周期边界诊断，记录 overlap、unsafe split、调整方向和影响范围；`MechSlotOutput.lifecycle_reliable=false` 时必须关注
- `ParseResult` — 顶层解析结果，含 `diagnostic_slots`、`private_slots`、`mech_results`、`errors`

### 日志包结构

```
diagnostic_information_xxx.zip     ← 外层包
├── diag/                          ← 诊断日志目录
│   ├── slot_1/                    ← 槽位
│   │   ├── diag.zip
│   │   └── diaglog_1_20260103000000.log.zip
│   └── slot_2/
└── varlog/                        ← 私有日志目录
    ├── slot_1/
    │   └── varlog.zip             ← 内部含 varlog/ 子目录
    │       └── varlog/
    │           ├── journal.log
    │           └── journal.log.1.gz
    └── slot_1_cpu_1/              ← CPU 子卡（编号从 1 开始，无 cpu_0）
        └── varlog.zip
```

### 主控判定两层策略

1. **优先**：`active_master_keyword`（正则）命中 Context → Slot 为主控（`RoleIdentifier.apply_mech_roles`）
2. **兜底**：`RoleIdentifier.fallback_roles` — 保守策略：唯一 ActivePeriod 候选 → ACTIVE；多候选 → 不武断判 ACTIVE，保持 UNKNOWN；有日志无时段 → STANDBY；无日志 → UNKNOWN

### 机制模块日志输出结构

```
output/{task_id}/mech_modules/{module_name}/
├── slot_1/
│   ├── 20260430T103707-20260430T113708/    ← 周期起止时间
│   │   ├── SERVICE-12345.log               ← 板卡级进程（cpu_id=""）直接放周期下
│   │   └── cpu_1/                          ← CPU 子卡进程放 cpu_N/ 子目录
│   │       ├── 20260430T103800-20260430T104500/
│   │       │   └── SERVICE-67890.log        ← 嵌套 CPU 周期内的进程日志
│   │       └── unknown/
│   │           └── WORKER-777.log           ← 匹配到板卡周期但未匹配到 CPU 周期
│   └── 20260430T120000-20260430T130000/
└── slot_2/

每篇 .log 格式：
[0001] [diagnostic|slot_1/diag.zip] Service=SERVICE; Slot=2; ...
[0002] [journal|slot_2/varlog.zip] SERVICE: No[2] xxx2 ...
       ↑ 序号      ↑ 来源+文件路径              ↑ 原始日志行
```

### 重启周期切分规则（CycleDetector）

三步切分算法（详见 `docs/superpowers/specs/2026-05-22-cycle-split-algorithm-design.md`）：

1. **检测板卡重启**：indicator 进程（`board_restart_indicator`，非独立重启）PID 变化 → 判定板卡重启
2. **安全切分点**：仅参考白名单进程（`board_restart_whitelist`，不重名、不支持独立重启）的 PID 信息：
   - `old_pid_end` = 白名单内所有进程旧 PID 最后一条时间戳的最大值
   - `new_pid_start` = 白名单内所有进程新 PID 第一条时间戳的最小值
   - 切分点 = 安全候选中的最早值，保证同 PID 段不被拆断
3. **Journal 序号前移**：对白名单内进程，从诊断日志获取旧 PID 最后 No，在全部条目（含 journal）中找序号跳变（从大号跳到小号），尝试前移切分点（受安全约束限制）
4. **层级传播**：板卡级 PID 变化 → 所有子 cpu 组同步切分；cpu 级 PID 变化 → 仅该 cpu 组切分
5. **嵌套输出**：板卡日志进入 `MechBoardCycle.processes`；CPU 日志进入对应 `MechBoardCycle.cpu_cycles[].processes`。找不到可用板卡周期时使用 `unknown`，找不到 CPU 周期时使用 `cpu_<id>/unknown`。

### 配置驱动

所有匹配规则在 `config.yaml` 的 `products.{name}` 下配置，代码不做硬编码：
- `pipeline.recursive_extraction` — 外层包是否递归解压
- `pipeline.debug_expand_gz` — 是否调试展开普通 `.gz` 日志
- `discovery.config.diagnostic_dir` / `private_dir` — 目录名
- `discovery.config.slot_dir_pattern` — slot 目录匹配 (glob)
- `discovery.config.diag_file_patterns` — 诊断日志文件名匹配 (glob)
- `log_parser.config.timestamp_regex` — 时间戳提取正则
- `log_parser.config.active_period_gap_threshold` — ActivePeriod 切分阈值（秒）
- `log_parser.config.mechanism_modules` — 机制模块配置
  - `journal.line_pattern` / `line_pattern2` — 双正则 fallback
  - `journal.line_pattern2_required_substrings` — 可选字符串列表，仅约束 `line_pattern2` 及其自动无序号 fallback，按原始整行大小写敏感匹配
  - `board_restart_indicator` — 板卡重启标识进程名（非独立重启）
  - `board_restart_whitelist` — 切分计算白名单进程列表（不重名、不支持独立重启）
  - `sequence_pattern` — 序号提取正则

### 解压安全规范

- **必须通过 `Decompressor` 类**：不要在业务代码中直接调用 `zipfile`/`tarfile`/`gzip`
- **解压前校验**：路径穿越、单文件大小上限（500MB）、压缩比（100x）
- **递归深度限制**：最多 10 轮递归扫描
- **异常必须记日志**：`logging.warning()` 记录并继续，不允许静默 `except: pass`

### 板卡重启层级

```
slot_1/          ← 板卡本身（cpu_id = None）
slot_1_cpu_1/    ← slot_1 的 1 号 CPU 子卡
slot_1_cpu_2/    ← slot_1 的 2 号 CPU 子卡
```

- 板卡重启 → 所有 cpu 子卡同步切分
- CPU 子卡重启 → 仅该 cpu 组切分
- `CycleDetector` 按 `(slot, cpu_key)` 分组，`cpu_key = cpu_id or ""`

### 插件化架构

支持多产品/多布局的日志包，目录发现和日志解析可自由组合。

```
Source Archive
  → [Decompressor]              通用
  → [DirectoryDiscoveryPlugin]   产品插件：找到 slot、文件
  → [No middle extraction]       内层归档已由统一解压阶段处理；普通 .gz 由 parser 流式读取
  → [LogParserPlugin]            产品插件：解析内容、构建周期、判定角色
  → [MechOutputWriter]           通用：板卡周期 + 嵌套 CPU 周期落盘
  → [MetadataGenerator]          通用：输出 metadata.json
```

插件注册：YAML 中声明 `plugin: "module.path.ClassName"`，runtime 动态加载。

### 已有产品插件

| 产品 | Scanner | Parser | 目录布局 | 压缩 |
|------|---------|--------|---------|------|
| `default` | ScannerPlugin | ParserPlugin | diag/ + varlog/ | 内层 zip |
| `compact` | CompactScannerPlugin | ParserPlugin (复用) | boards/ + logs/ | 无压缩 |

新增产品的步骤：
1. 创建 DirectoryDiscoveryPlugin 子类（或复用已有）
2. 在 `config.yaml` 的 `products:` 下添加配置段
3. `python cli.py parse <pkg> --product <name>`

### 测试

当前 `pytest --collect-only -q` 收集到 240 个测试，覆盖：
- `tests/test_utils.py` — utils 纯函数（glob、slot 提取、时间戳等）
- `tests/test_decompressor.py` — 解压安全（路径穿越、Windows 绝对路径、UNC 路径、zip 炸弹）和提取逻辑
- `tests/test_config_validation.py` — 机制模块配置校验（正则合法性、命名组、白名单冲突）
- `tests/test_parser_plugin.py` — ParserPlugin 编排层（ActivePeriod、进程名解析）
- `tests/test_plugin_loader.py` — 动态插件加载
- `tests/test_pipeline.py` — Pipeline 管道编排（debug_expand_gz 配置透传）
- `tests/test_scanner_plugin.py` — ScannerPlugin 目录发现
- `tests/test_timestamp_extractor.py` — TimestampExtractor（文本/文件/gz/LogEntry）
- `tests/test_cycle_detector.py` — CycleDetector（PID 变化切分、白名单安全切分、journal 序号前移、CPU 隔离、split trace）
- `tests/test_process_name_resolver.py` — ProcessNameResolver（diag 名解析、journal 映射、PID 拆分阈值）
- `tests/test_role_identifier.py` — RoleIdentifier（mech 优先 + 保守兜底、多候选不武断）
- `tests/test_output_writer.py` — MechOutputWriter（目录结构、日志内容）
- `tests/test_query.py` — ResultQueryService（mech-slots/lifecycles/logs 查询）

### 最近变更摘要

| 日期 | 变更 |
|------|------|
| 2026-05-29 | **生命周期嵌套输出**：`MechCpuCycle`、`lifecycle_reliable`、`boundary_issues` 进入模型和元数据；CPU 日志落盘到 `slot/<board_cycle>/cpu_N/<cpu_cycle>/`；`module2` 按 `slot + cpu_id + timestamp` 优先匹配嵌套 CPU 周期；`mech-logs` 支持 `--cpu` / `--cpu-cycle`；240 个测试可收集 |
| 2026-05-24 | **v0.3.1**：`--module`/`-m` 参数支持按机制模块过滤查询结果；`--debug-expand-gz` 控制普通 `.gz` 展开；默认流式读取 `.gz`；`check-config` 新增插件类继承和方法校验；167 个单元测试 |
| 2026-05-24 | **v0.2 演进完成**：9 步渐进式重构，123 个单元测试。修复解压安全路径 bug、配置校验前置化、CycleDetector split trace、ParserPlugin 拆为 5 组件、流式文件读取、保守角色判定、查询服务从 CLI 提取 |
| 2026-05-18 | **P0-P3 重构完成**：93 个单元测试、新管道默认化、删除旧管道 5 模块 (-1123 行)、ParserPlugin 拆为 4 组件 |
| 2026-05-18 | **Phase 2+3 完成**：ScannerPlugin + ParserPlugin 创建，config.yaml products 段 |
| 2026-05-18 | 修复 encoding/decompressor/pipeline 多个问题 |
| 2026-05-17 | 修复 5 Critical + 8 Important + 7 Minor 安全/质量问题 |
| 2026-05-17 | 全仓命名清理：AAA→Mech、docue→EXAMPLE、cpdt→journal 等 |
| 2026-05-17 | 移除前端（frontend/）和 Web API（backend/main.py） |
| 2026-05-17 | 创建插件框架：utils.py、pipeline.py、plugins/ |
