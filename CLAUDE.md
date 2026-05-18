# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

日志解析维护工具，用于预处理产品设备的日志压缩包。支持多层解压，发现诊断日志和私有日志（varlog），通过可配置的机制模块优先判定主控，兜底通过目录+时间戳推断。输出结构化元数据供 AI agent 消费。

## 运行命令

```bash
# 安装依赖
pip install -r requirements.txt

# CLI 解析（默认使用 default 产品插件管道）
python cli.py parse <package_path> [-c config.yaml] [-o ./output] [--verbose] [--product default|compact]
python cli.py info <task_id>
python cli.py list-slots <task_id>
python cli.py query-diag <task_id> -s <slot_id>
python cli.py mech-slots <task_id>
python cli.py mech-lifecycles <task_id> -s <slot_id>
python cli.py mech-logs <task_id> -s <slot_id> -c <cycle_dir> -p <proc_name>-<pid>

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

- **`check-config`**：检查所有正则可编译、glob 有效、模块配置完整，错误和警告分开
- **`test-pattern`**：用配置正则测试实际日志行，显示提取字段、Stage1 预过滤结果、时间戳、主控关键字命中
- **错误隔离**：每一步失败不终止全流程，继续执行并在最后汇总所有错误
- **`--verbose`**：输出每步耗时、处理项数、机制模块 诊断/journal 条数对比、同名进程多实例检测
- **Windows 编码**：CLI 入口自动将 stdout/stderr 切换为 UTF-8，避免 GBK 编码下 Unicode 符号报错

## 架构

### 数据流

```
外层压缩包 → Decompressor(解压外层, recursive=False)
→ DirectoryDiscoveryPlugin(发现 slot+文件，产品插件)
→ Decompressor(解压内层 .zip 到 _extracted 子目录)
→ LogParserPlugin(时间戳→ActivePeriod→机制模块→角色判定，产品插件)
→ MechOutputWriter(三层落盘) → MetadataGenerator(JSON输出)
```

关键设计决策：
- **解压与扫描分离**：外层包 `recursive=False` 解压到 `extracted/`，内部压缩包保留原样供 ScannerPlugin 收集元数据，之后再解压内容到 `_extracted` 子目录做解析
- **机制模块优先主控判定**：indicator 进程 PID 变化 + 序号回绕反向扫描确定重启边界
- **时区对齐**：诊断日志时间戳含时区（如 `+08:00`），journal 不含。从全部条目中检测时区并归一化所有 naive timestamp
- **journal 双正则 fallback**：`line_pattern` 匹配完整元数据格式，`line_pattern2` 兜底匹配无元数据块格式

### 模块分工

| 模块 | 职责 |
|------|------|
| `backend/decompressor.py` | .zip/.tar.gz/.gz 多层递归解压，含路径穿越、zip 炸弹、递归深度安全防护 |
| `backend/pipeline.py` | Pipeline 类：产品无关的 6 步管道编排器，按产品名加载插件对 |
| `backend/plugins/base.py` | 两个 ABC：`DirectoryDiscoveryPlugin`、`LogParserPlugin` |
| `backend/plugins/loader.py` | 动态加载插件：`instantiate_plugin(class_path, base, config)` |
| `backend/plugins/default/scanner.py` | ScannerPlugin：标准 diag/ + varlog/ 目录发现 |
| `backend/plugins/default/parser.py` | ParserPlugin：解析编排层，委托给 backend/parsing/ 四个组件 |
| `backend/plugins/compact/scanner.py` | CompactScannerPlugin：boards/ + logs/ 布局发现 |
| `backend/parsing/timestamp_extractor.py` | TimestampExtractor：从文本/文件/LogEntry 提取时间戳，处理 .gz 和 UTF-8/GBK |
| `backend/parsing/cycle_detector.py` | CycleDetector：PID 变化 + 序号回绕反向扫描的重启周期检测 |
| `backend/parsing/role_identifier.py` | RoleIdentifier：机制模块优先 + 兜底（ActivePeriod/日志存在性）角色判定 |
| `backend/parsing/output_writer.py` | MechOutputWriter：slot/周期/cpu_N 三层目录落盘 |
| `backend/metadata.py` | 生成 `metadata.json`（含 mech_results 键） |
| `backend/models.py` | Pydantic 数据模型（SlotInfo、ParseResult、MechResult 等） |
| `backend/utils.py` | 纯函数工具：glob_to_regex、时间戳提取、文件读取等 |
| `cli.py` | Click CLI：parse/info/list-slots/query-diag/mech-slots/mech-lifecycles/mech-logs/check-config/test-pattern |

### 核心模型 (`backend/models.py`)

- `SlotInfo` — 诊断日志槽位，含 `diagnostic_logs`、`active_periods`、`role`、`board_type`
- `LogEntry` — 单个日志文件，含 `dump_time`(文件名转储时间)、`content_timestamps`(内容时间戳)
- `ActivePeriod` — 连续主控时段段 (start/end)
- `PrivateSlotInfo` + `JournalLogFile` — 私有日志槽位和 journal 文件元数据
- `MechResult` / `MechSlotOutput` / `MechBoardCycle` / `MechProcessLifecycle` / `MechLogEntry` — 机制模块输出结构，进程按 `(process_name, pid, cpu_id)` 分组
- `ParseResult` — 顶层解析结果，含 `diagnostic_slots`、`private_slots`、`mech_results`

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
2. **兜底**：`RoleIdentifier.fallback_roles` — 有 ActivePeriod → ACTIVE，有日志无时段 → STANDBY，无日志 → UNKNOWN

### 机制模块日志输出结构

```
output/{task_id}/mech_modules/{module_name}/
├── slot_1/
│   ├── 20260430T103707-20260430T113708/    ← 周期起止时间
│   │   ├── SERVICE-12345.log               ← 板卡级进程（cpu_id=""）直接放周期下
│   │   └── cpu_1/                          ← CPU 子卡进程放 cpu_N/ 子目录
│   │       └── SERVICE-67890.log
│   └── 20260430T120000-20260430T130000/
└── slot_2/

每篇 .log 格式：
[0001] [diagnostic|slot_1/diag.zip] Service=SERVICE; Slot=2; ...
[0002] [journal|slot_2/varlog.zip] SERVICE: No[2] xxx2 ...
       ↑ 序号      ↑ 来源+文件路径              ↑ 原始日志行
```

### 重启周期切分规则（CycleDetector）

1. **indicator PID 变化**（`board_restart_indicator` 配置）：同一 (slot, cpu) 组内 indicator 进程的 PID 变化时触发切分
2. **序号回绕反向扫描**：PID 变化后向前扫描，找最早的序号回绕点，前移切分边界。阈值 `SEQ_ROLLBACK_THRESHOLD=3`
3. **层级传播**：板卡级 PID 变化 → 所有子 cpu 组同步切分；cpu 级 PID 变化 → 仅该 cpu 组切分

### 配置驱动

所有匹配规则在 `config.yaml` 的 `products.{name}` 下配置，代码不做硬编码：
- `discovery.config.diagnostic_dir` / `private_dir` — 目录名
- `discovery.config.slot_dir_pattern` — slot 目录匹配 (glob)
- `discovery.config.diag_file_patterns` — 诊断日志文件名匹配 (glob)
- `log_parser.config.timestamp_regex` — 时间戳提取正则
- `log_parser.config.active_period_gap_threshold` — ActivePeriod 切分阈值（秒）
- `log_parser.config.mechanism_modules` — 机制模块配置
  - `journal.line_pattern` / `line_pattern2` — 双正则 fallback
  - `board_restart_indicator` — 板卡重启标识进程名
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
  → [Inner Extraction]           通用：解压内层压缩包
  → [LogParserPlugin]            产品插件：解析内容、构建周期、判定角色
  → [MechOutputWriter]           通用：三层落盘
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

93 个单元测试，覆盖：
- `tests/test_utils.py` — utils 纯函数（glob、slot 提取、时间戳等）
- `tests/test_decompressor.py` — 解压安全（路径穿越、zip 炸弹）和提取逻辑
- `tests/test_parser_plugin.py` — ParserPlugin 编排层（ActivePeriod、进程名解析）
- `tests/test_plugin_loader.py` — 动态插件加载
- `tests/test_scanner_plugin.py` — ScannerPlugin 目录发现
- `tests/test_timestamp_extractor.py` — TimestampExtractor（文本/文件/gz/LogEntry）
- `tests/test_cycle_detector.py` — CycleDetector（PID 变化切分、CPU 隔离）
- `tests/test_role_identifier.py` — RoleIdentifier（mech 优先 + 兜底）
- `tests/test_output_writer.py` — MechOutputWriter（目录结构、日志内容）

### 最近变更摘要

| 日期 | 变更 |
|------|------|
| 2026-05-18 | **P0-P3 重构完成**：93 个单元测试、新管道默认化、删除旧管道 5 模块 (-1123 行)、ParserPlugin 拆为 4 组件 |
| 2026-05-18 | **Phase 2+3 完成**：ScannerPlugin + ParserPlugin 创建，config.yaml products 段 |
| 2026-05-18 | 修复 encoding/decompressor/pipeline 多个问题 |
| 2026-05-17 | 修复 5 Critical + 8 Important + 7 Minor 安全/质量问题 |
| 2026-05-17 | 全仓命名清理：AAA→Mech、docue→EXAMPLE、cpdt→journal 等 |
| 2026-05-17 | 移除前端（frontend/）和 Web API（backend/main.py） |
| 2026-05-17 | 创建插件框架：utils.py、pipeline.py、plugins/ |
