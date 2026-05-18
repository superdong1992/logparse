# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

日志解析维护工具，用于预处理产品设备的日志压缩包。支持多层解压，发现诊断日志和私有日志（varlog），通过可配置的机制模块（module1）优先判定主控，兜底通过目录+时间戳推断。输出结构化元数据供 AI agent 消费。

## 运行命令

```bash
# 安装依赖
pip install -r requirements.txt

# CLI 解析
python cli.py parse <package_path> [-c config.yaml] [-o ./output] [--verbose] [--product <name>]
python cli.py info <task_id>
python cli.py list-slots <task_id>
python cli.py query-diag <task_id> -s <slot_id>
python cli.py mech-slots <task_id>
python cli.py mech-lifecycles <task_id> -s <slot_id>
python cli.py mech-logs <task_id> -s <slot_id> -c <cycle_dir> -p <proc_name>-<pid>

# 调试工具（无法联网时自助排查）
python cli.py check-config [-c config.yaml]            # 检查配置有效性
python cli.py test-pattern -m module1 -t diag "日志行"  # 测试正则匹配
python cli.py test-pattern -m module1 -t journal "日志行"

# 生成测试数据
python tests/generate_mock_data.py
python cli.py parse tests/mock_data/diagnostic_information_20260103.zip
```

### 调试能力

- **`check-config`**：检查所有正则可编译、glob 有效、模块配置完整，错误和警告分开
- **`test-pattern`**：用配置正则测试实际日志行，显示提取字段、Stage1 预过滤结果、时间戳、主控关键字命中
- **错误隔离**：每一步失败不终止全流程，继续执行并在最后汇总所有错误
- **`--verbose`**：输出每步耗时、处理项数、机制模块 诊断/journal 条数对比、同名进程多实例检测
- **`test-pattern`**：支持 `line_pattern` 和 `line_pattern2` 双正则 fallback 测试
- **Windows 编码**：CLI 入口自动将 stdout/stderr 切换为 UTF-8，避免 GBK 编码下 Unicode 符号报错

## 架构

### 数据流

```
外层压缩包 → Decompressor(递归解压全部, recursive=True) → Scanner(发现diag/varlog slot+文件)
→ Decompressor(解压内部.zip内容到_extracted子目录) → MechParser(Stage1 大小写敏感预过滤→Stage2 正则提取)
→ LogParser(提取全部时间戳+构建ActivePeriod) → Identifier(机制模块优先 + 目录+gap兜底判定)
→ MetadataGenerator(JSON输出)
```

关键设计决策：
- **解压与扫描分离**：外层包递归解压全部内容到 `extracted/`，内部压缩包保留原样供 Scanner 收集元数据，之后再解压内容到 `_extracted` 子目录做解析。`extracted/` 是唯一可搜索的全部日志目录树
- **机制模块 优先主控判定**：indicator 进程 PID 变化 + 序号回绕反向扫描确定重启边界，周期目录名用 indicator 进程条目的时间戳
- **时区对齐**：诊断日志时间戳含时区（如 `+08:00`），journal 不含。`_parse_one` 从**全部条目**（诊断+journal）中检测时区并归一化所有 naive timestamp
- **journal 三种格式**：`line_pattern` 匹配完整元数据格式，`line_pattern2` 兜底匹配无元数据块格式（含同名进程 PID 后缀），双正则 fallback 保证兼容性

### 模块分工

| 模块 | 职责 |
|------|------|
| `backend/config.py` | YAML 配置加载，glob→regex 编译，时间戳提取（含时区），机制模块配置加载 |
| `backend/decompressor.py` | .zip/.tar.gz/.gz 多层递归解压，`extract_all(recursive=)` 控制递归深度。含路径穿越、文件大小、压缩比等安全防护 |
| `backend/scanner.py` | `scan_diag()` 扫描 `diag/slot_*/` 诊断日志; `scan_private()` 优先检测已解压 `varlog/` 目录，兜底解压 varlog.zip |
| `backend/mech_parser.py` | 遍历启用的机制模块，Stage1 大小写敏感预过滤→Stage2 双正则 fallback，板卡重启层级（PID变化+序号回绕），丢号检测，条数校验，同名进程检测，三层落盘 |
| `backend/log_parser.py` | 提取日志内容全部时间戳，按 gap 阈值构建 ActivePeriod |
| `backend/identifier.py` | 兜底判定：有 ActivePeriod→ACTIVE，有日志无时段→STANDBY，无日志→UNKNOWN |
| `backend/metadata.py` | 生成 `metadata.json`（含诊断、私有、全部模块 机制模块 结果） |
| `backend/utils.py` | 纯函数工具：glob_to_regex、时间戳提取、文件读取等，插件和核心框架共用 |
| `backend/pipeline.py` | Pipeline 类：产品无关的通用管道编排器，按产品名加载插件对 |
| `backend/plugins/base.py` | 两个 ABC：DirectoryDiscoveryPlugin、LogParserPlugin |
| `backend/plugins/loader.py` | 动态加载插件：instantiate_plugin(class_path, base, config) |
| `backend/plugins/default/` | 默认产品插件（待实现：ScannerPlugin、ParserPlugin），将迁移当前 Scanner/MechParser/LogParser/Identifier 逻辑 |
| `cli.py` | Click CLI：parse/info/list-slots/query-diag/mech-slots/mech-lifecycles/mech-logs |

### ⚠ 前端和 Web API 已移除

前端目录（`frontend/`）和 Web API（`backend/main.py`）已删除，后续统一实现。当前仅保留 CLI 入口。

### 核心模型 (`backend/models.py`)

- `SlotInfo` — 诊断日志槽位，含 `diagnostic_logs`、`active_periods`、`role`、`board_type`
- `LogEntry` — 单个日志文件，含 `dump_time`(文件名转储时间)、`content_timestamps`(内容时间戳)
- `ActivePeriod` — 连续主控时段段 (start/end)
- `PrivateSlotInfo` + `JournalLogFile` — 私有日志槽位和 journal 文件元数据
- `MechResult` / `MechSlotOutput` / `MechBoardCycle` / `MechProcessLifecycle` / `MechLogEntry` — 机制模块 模块输出结构，进程按 `(process_name, pid, cpu_id)` 分组
- `ParseResult` — 顶层解析结果，含 `diagnostic_slots`、`private_slots`、`mech_results`（全部模块列表）

### 日志包结构

```
diagnostic_information_xxx.zip     ← 外层包
├── diag/                          ← 诊断日志目录
│   ├── slot_1/                    ← 槽位（主控板/接口板都有，但有诊断日志的才是主控）
│   │   ├── diag.zip
│   │   └── diaglog_1_20260103000000.log.zip
│   └── slot_2/
└── varlog/                        ← 私有日志目录
    ├── slot_1/
    │   └── varlog.zip             ← 内部含 varlog/ 子目录
    │       └── varlog/
    │           ├── journal.log
    │           └── journal.log.1.gz
    └── slot_1_cpu_0/              ← CPU 子卡
        └── varlog.zip
```

### 主控判定两层策略

1. **优先**：`mechanism_modules.module1` 配置的 `active_master_keyword`（正则）命中 Context → Slot 为主控
2. **兜底**：Identifier 通过诊断日志所在目录 + 日志内容时间戳 gap 推断

### 机制模块 日志输出结构

```
output/{task_id}/mech_modules/{module_name}/
├── slot_1/
│   ├── 20260430T103707-20260430T113708/    ← 周期起止时间
│   │   ├── SERVICE-12345.log               ← 板卡级进程（cpu_id=None）直接放周期下
│   │   └── cpu_1/                          ← CPU 子卡进程（cpu_id=1/2/...）放 cpu_N/ 子目录
│   │       └── SERVICE-67890.log
│   └── 20260430T120000-20260430T130000/    ← 下次重启
│       └── ...
└── slot_2/

每篇 .log 格式：
[0001] [diagnostic|slot_1/diag.zip] Service=SERVICE; Slot=2; ...
[0002] [journal|slot_2/varlog.zip] SERVICE: No[2] xxx2 ...
       ↑ 序号      ↑ 来源+文件路径              ↑ 原始日志行
```

### 重启周期切分规则

1. **indicator PID 变化**（`board_restart_indicator` 配置）：同一 (slot, cpu) 组内 indicator 进程的 PID 变化时触发切分
2. **序号回绕反向扫描**：PID 变化后向前扫描，找最早出现的序-号回绕点（同一进程的 No[N] 从大值突然变小），前移切分边界。回绕判定阈值 `SEQ_ROLLBACK_THRESHOLD=3`
3. **层级传播**：板卡级（cpu_key=""）PID 变化 → 所有子 cpu 组同步切分；cpu 级 PID 变化 → 仅该 cpu 组切分

### 可靠性校验（--verbose）

- **条数对比**：`诊断:N + journal:M = X → 输出:K`，不一致时标 `⚠ 条数不一致`
- **同名进程多实例**：同一周期内同一进程名出现多个 PID 时打印 `[DEBUG] _make_cycles multi_instance`

### 配置驱动

所有匹配规则在 `config.yaml` 中配置，代码不做硬编码：
- `package.diagnostic_dir` / `package.private_dir` — 目录名
- `boards.main_control.dir_pattern` — slot 目录匹配 (glob)
- `diagnostic_files.patterns` — 诊断日志文件名匹配 (glob)
- `log_content.timestamp_regex` — 日志行时间戳提取正则（含可选时区偏移 `([+-]\d{2}:\d{2})?`）
- `log_content.active_period_gap_threshold` — ActivePeriod 切分阈值（秒）
- `private_logs.dir_patterns` — varlog 目录匹配
- `private_logs.journal_files` — journal 日志文件名匹配 + 序号提取
- `mechanism_modules` — 机制模块配置（module_name 用于 Stage1 大小写敏感预过滤、正则、主控关键字等）
  - `journal.line_pattern` — 格式1（完整元数据块）
  - `journal.line_pattern2` — 格式2/3（无元数据块，PID 可选）
  - `board_restart_indicator` — 板卡重启标识进程名
  - `sequence_pattern` — 序号提取正则 `No\[(\d+)\]`，用于丢号检测和重启序号回绕判定

### 解压安全规范

- **必须通过 `Decompressor` 类**：所有压缩包解压必须走 `Decompressor.extract_all()`，不要在业务代码中直接调用 `zipfile`/`tarfile`/`gzip`
- **解压前校验**：路径穿越（`..`、绝对路径）、单文件大小上限（500MB）、压缩比（100x，防 zip 炸弹）
- **递归深度限制**：`extract_all` 最多 10 轮递归扫描，防止无限循环
- **异常必须记日志**：解压失败用 `logging.warning()` 记录并继续，不允许静默 `except: pass`
- **空文件跳过**：`stat().st_size == 0` 时跳过不解压

### 板卡重启层级

目录结构体现板卡层级关系，cpu 编号从 1 开始，没有 cpu_0。

```
slot_1/          ← 板卡本身（cpu_id = None），不是 "cpu_0"
slot_1_cpu_1/    ← slot_1 的 1 号 CPU 子卡
slot_1_cpu_2/    ← slot_1 的 2 号 CPU 子卡
```

**重启传播规则：**
- **板卡重启** → 该 slot 下所有 cpu 子卡一起重启 → `_build_cycles` 中板卡级 indicator PID 变化会传播到所有子 cpu 组同步切分
- **CPU 子卡重启** → 仅该 cpu 重启，板卡和其他 cpu 不受影响 → cpu 级 indicator PID 变化仅切分该 cpu 组

**分组规则：**
- `_build_cycles` 按 `(slot, cpu_key)` 分组，`cpu_key = cpu_id or ""`
- `cpu_key = ""` 为板卡本身，`cpu_key = "1"/"2"` 为 CPU 子卡
- journal 条目的 `cpu_id` 直接从 `PrivateSlotInfo.cpu_id` 取值（None/None/"1"/"2"），不设默认 "0"

### 插件化架构（Phase 2+3 已完成）

目标：支持多产品/多布局的日志包，目录发现和日志解析可自由组合。

```
Source Archive
  → [Decompressor]          通用
  → [DirectoryDiscovery]     产品插件：找到 slot、文件
  → [Inner Extraction]       通用：解压内层压缩包
  → [LogParser]              产品插件：解析内容、构建周期、判定角色
  → [MetadataGenerator]      通用：输出 metadata.json
```

插件注册方式：YAML 中声明 `plugin: "module.path.ClassName"`，runtime 动态加载。

**当前状态**：默认插件（ScannerPlugin + ParserPlugin）已完成，`--product default` 可走新管道。旧管道（`--product` 不指定时）保留向后兼容。

### 新管道 vs 旧管道

| 方面 | 旧管道 (cli.py _parse_legacy) | 新管道 (Pipeline.run()) |
|------|-------------------------------|------------------------|
| Step 1 解压 | recursive=True（内层 zip 被误删） | recursive=False（正确） |
| 配置 | ConfigLoader (Pydantic 模型) | 纯 dict（产品段） |
| 扫描 | Scanner（硬编码） | DirectoryDiscoveryPlugin（可替换） |
| 解析 | MechParser+LogParser+Identifier（分散） | LogParserPlugin（统一） |
| 插件 | 不支持 | YAML+Python 动态加载 |

### 遗留代码

以下模块保留向后兼容，待插件系统完成后废弃：
- `backend/scanner.py` / `mech_parser.py` / `log_parser.py` / `identifier.py` — 旧管道，cli.py 默认仍走此路径
- `backend/config.py` (ConfigLoader) — 旧配置加载，旧管道依赖

### 最近变更摘要

| 日期 | 变更 |
|------|------|
| 2026-05-18 | **Phase 2+3 完成**：ScannerPlugin + ParserPlugin 创建，config.yaml products 段，cli.py --product 标志 |
| 2026-05-18 | 修复 encoding 问题：base.py/loader.py/utils.py/pipeline.py 的 GBK→UTF-8 |
| 2026-05-18 | 修复 decompressor.py:101 self.config.is_compressed → self.is_compressed（config=None 时 NPE） |
| 2026-05-18 | 修复 Pipeline Step 1 recursive=False（防止内层 zip 提前解压删除） |
| 2026-05-17 | 修复 5 Critical + 8 Important + 7 Minor 安全/质量问题 |
| 2026-05-17 | 全仓命名清理：AAA→Mech、docue→EXAMPLE、cpdt→journal 等 |
| 2026-05-17 | 移除倒换检测（SwitchoverEvent），职责迁移到产品 skill |
| 2026-05-17 | 移除前端（frontend/）和 Web API（backend/main.py） |
| 2026-05-17 | 创建插件框架：utils.py、pipeline.py、plugins/、decompressor 可配置初始化 |
