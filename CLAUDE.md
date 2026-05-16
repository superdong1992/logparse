# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

日志解析维护工具，用于预处理产品设备的日志压缩包。支持多层解压，发现诊断日志和私有日志（varlog），通过可配置的机制模块（module1）优先判定主控，兜底通过目录+时间戳推断。输出结构化元数据供 AI agent 消费。

## 运行命令

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 Web 服务
uvicorn backend.main:app --reload --port 8080

# CLI 解析
python cli.py parse <package_path> [-c config.yaml] [-o ./output] [--verbose]
python cli.py info <task_id>
python cli.py list-slots <task_id>
python cli.py query-diag <task_id> -s <slot_id>
python cli.py aaa-slots <task_id>
python cli.py aaa-lifecycles <task_id> -s <slot_id>
python cli.py aaa-logs <task_id> -s <slot_id> -c <cycle_dir> -p <proc_name>-<pid>

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
- **`--verbose`**：输出每步耗时、处理项数、AAA 总条数
- **Windows 编码**：CLI 入口自动将 stdout/stderr 切换为 UTF-8，避免 GBK 编码下 Unicode 符号报错

## 架构

### 数据流

```
外层压缩包 → Decompressor(仅解压外层, recursive=False) → Scanner(发现diag/varlog slot+文件)
→ Decompressor(解压内部.zip内容) → AaaParser(Stage1 大小写敏感预过滤→Stage2 正则提取)
→ LogParser(提取全部时间戳+构建ActivePeriod) → Identifier(AAA优先 + 目录+gap兜底判定)
→ MetadataGenerator(JSON输出)
```

关键设计决策：
- **解压与扫描分离**：外层包先解压一层，保留内部压缩包原样供 Scanner 收集元数据，之后再解压内容做解析
- **AAA 优先主控判定**：indicator 进程 PID 变化检测整板重启→切分周期，周期目录名用 indicator 进程条目的时间戳
- **时区对齐**：诊断日志时间戳含时区（如 `+08:00`），journal 不含。`_parse_one` 从 diag 条目中检测时区并应用到 journal 条目，确保混排排序正确

### 模块分工

| 模块 | 职责 |
|------|------|
| `backend/config.py` | YAML 配置加载，glob→regex 编译，时间戳提取（含时区），机制模块配置加载 |
| `backend/decompressor.py` | .zip/.tar.gz/.gz 多层递归解压，`extract_all(recursive=False)` 控制递归深度 |
| `backend/scanner.py` | `scan_diag()` 扫描 `diag/slot_*/` 诊断日志; `scan_private()` 解压 varlog.zip 到真实目录后扫描 journal |
| `backend/aaa_parser.py` | 遍历启用的机制模块，Stage1 模块名大小写敏感预过滤→Stage2 正则提取字段，整板重启检测（PID变化），丢号检测，三层落盘 |
| `backend/log_parser.py` | 提取日志内容全部时间戳，按 gap 阈值构建 ActivePeriod |
| `backend/identifier.py` | 兜底判定：有 ActivePeriod→ACTIVE，有日志无时段→STANDBY，无日志→UNKNOWN；倒换检测跳过重叠时段 |
| `backend/metadata.py` | 生成 `metadata.json`（含诊断、私有、全部模块 AAA 结果） |
| `backend/main.py` | FastAPI 应用，上传/状态/元数据/AAA API，静态文件挂载前端 |
| `cli.py` | Click CLI：parse/info/list-slots/query-diag/aaa-slots/aaa-lifecycles/aaa-logs |

### 核心模型 (`backend/models.py`)

- `SlotInfo` — 诊断日志槽位，含 `diagnostic_logs`、`private_logs`、`active_periods`、`role`
- `LogEntry` — 单个日志文件，含 `dump_time`(文件名转储时间)、`content_timestamps`(内容时间戳)
- `ActivePeriod` — 连续主控时段段 (start/end)
- `SwitchoverEvent` — 倒换事件 (from_slot/to_slot/time/evidence)
- `PrivateSlotInfo` + `JournalLogEntry` — 私有日志槽位和 journal 文件元数据
- `AaaResult` / `AaaSlotOutput` / `AaaBoardCycle` / `AaaProcessLifecycle` / `AaaLogEntry` — AAA 模块输出结构，进程按 `(process_name, pid, cpu_id)` 分组
- `ParseResult` — 顶层解析结果，含 `diagnostic_slots`、`private_slots`、`switchover_timeline`、`aaa_results`（全部模块列表）

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
    │           ├── cpdt_journal.log
    │           └── cpdt_journal.log.1.gz
    └── slot_1_cpu_0/              ← CPU 子卡
        └── varlog.zip
```

### 主控判定两层策略

1. **优先**：`mechanism_modules.module1` 配置的 `active_master_keyword`（正则）命中 Context → Slot 为主控
2. **兜底**：Identifier 通过诊断日志所在目录 + 日志内容时间戳 gap 推断

### AAA 日志输出结构

```
output/{task_id}/aaa/{module_name}/
├── slot_1/
│   ├── 20260430T103707-20260430T113708/    ← indicator 进程起止时间
│   │   ├── SERVICE-12345.log               ← cpu_0 直接放周期下
│   │   └── cpu_1/                          ← cpu≠0 时多一层
│   │       └── SERVICE-67890.log
│   └── 20260430T120000-20260430T130000/    ← 下次重启
│       └── ...
└── slot_2/

每篇 .log 格式：
[0001] [diagnostic|slot_1/diag.zip] Service=SERVICE; Slot=2; ...
[0002] [journal|slot_2/varlog.zip] SERVICE: No[2] xxx2 ...
       ↑ 序号      ↑ 来源+文件路径              ↑ 原始日志行
```

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
