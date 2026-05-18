# ailogparse 使用文档

## 目录

- [简介](#简介)
- [安装](#安装)
- [快速开始](#快速开始)
- [CLI 命令详解](#cli-命令详解)
  - [parse — 解析日志压缩包](#parse--解析日志压缩包)
  - [info — 查看任务元数据](#info--查看任务元数据)
  - [list-slots — 列出所有槽位](#list-slots--列出所有槽位)
  - [query-diag — 查询槽位诊断日志](#query-diag--查询槽位诊断日志)
  - [mech-slots — 机制模块槽位概况](#mech-slots--机制模块槽位概况)
  - [mech-lifecycles — 周期与进程](#mech-lifecycles--周期与进程)
  - [mech-logs — 查看原始日志](#mech-logs--查看原始日志)
  - [check-config — 检查配置](#check-config--检查配置)
  - [test-pattern — 测试正则匹配](#test-pattern--测试正则匹配)
- [配置文件详解](#配置文件详解)
  - [pipeline 段](#pipeline-段)
  - [products 段](#products-段)
  - [discovery 配置](#discovery-配置)
  - [log_parser 配置](#log_parser-配置)
  - [mechanism_modules 配置](#mechanism_modules-配置)
- [输出结构说明](#输出结构说明)
- [插件化扩展](#插件化扩展)
  - [新增产品插件](#新增产品插件)
  - [插件接口说明](#插件接口说明)
- [解压安全机制](#解压安全机制)
- [测试](#测试)
- [常见问题](#常见问题)

---

## 简介

ailogparse 是一个日志压缩包预处理工具，面向 AI agent 消费。核心功能：

- **多层解压**：支持 .zip / .tar.gz / .gz 格式的递归解压，含路径穿越、zip 炸弹等安全防护
- **目录发现**：自动扫描 diag/ 和 varlog/ 目录，识别槽位（slot）和日志文件
- **时间戳提取**：从日志内容中提取时间戳，含时区归一化
- **重启周期检测**：通过 indicator 进程 PID 变化 + 序号回绕反向扫描，自动切分重启周期
- **主控判定**：机制模块关键字优先，兜底通过 ActivePeriod 和日志存在性判定
- **结构化输出**：生成 metadata.json、result.json、分进程日志文件

## 安装

```bash
# 克隆仓库
git clone <repo-url>
cd ailogparse

# 安装依赖（Python 3.10+）
pip install -r requirements.txt
```

依赖列表：
- `pyyaml>=6.0` — YAML 配置解析
- `pydantic>=2.0.0` — 数据模型
- `click>=8.1.0` — CLI 框架
- `pytest>=7.0` — 测试框架

## 快速开始

```bash
# 1. 生成模拟数据
python tests/generate_mock_data.py

# 2. 解析日志压缩包
python cli.py parse tests/mock_data/diagnostic_information_20260103.zip --verbose

# 3. 查看结果
python cli.py info diagnostic_information_20260103
python cli.py list-slots diagnostic_information_20260103
```

---

## CLI 命令详解

### parse — 解析日志压缩包

主命令，执行完整的 6 步解析管道。

```bash
python cli.py parse <package_path> [选项]
```

**参数：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `PACKAGE_PATH` | 日志压缩包路径（必填） | — |
| `-o, --output` | 输出目录 | `./output` |
| `-v, --verbose` | 详细输出（每步耗时、条数统计、条数一致性校验） | 关闭 |
| `-p, --product` | 产品名，决定使用哪套插件 | `default` |
| `-c, --config` | 配置文件路径 | `config.yaml` |

**示例：**

```bash
# 基础解析
python cli.py parse /path/to/diagnostic_information_20260103.zip

# 详细输出
python cli.py parse /path/to/diagnostic_information_20260103.zip --verbose

# 使用 compact 产品插件
python cli.py parse /path/to/compact_package.zip --product compact

# 指定输出目录
python cli.py parse /path/to/package.zip -o ./my_output --verbose
```

**输出内容（--verbose 模式示例）：**

```
  [1/6] 解压 diagnostic_information_20260103.zip [OK] (8 文件, 0.2s)
    解压文件数: 8
  [2/6] 扫描 diag/ [OK] (2.0s)
    诊断日志槽位: 2 (3 文件)
    私有日志槽位: 3 (4 文件)
  [3/6] 解压诊断日志内容 [OK] (0.1s)
  [4/6] 日志解析 (时间戳+周期+机制模块+角色) [OK] (0.3s)
    提取时间戳: 156 条
    slot_1: 1 个 ActivePeriod, 角色=active
    slot_2: 1 个 ActivePeriod, 角色=active
  [5/6] 落盘 EXAMPLE [OK] (0.0s)
    [EXAMPLE] 诊断:5 + journal:2 = 7 -> 输出:7
  [6/6] 元数据生成 [OK] (0.0s)
    元数据: output/diagnostic_information_20260103/metadata.json
```

---

### info — 查看任务元数据

```bash
python cli.py info <task_id> [-o ./output]
```

显示指定任务的 `metadata.json` 内容。

**示例：**

```bash
python cli.py info diagnostic_information_20260103
```

---

### list-slots — 列出所有槽位

```bash
python cli.py list-slots <task_id> [-o ./output]
```

列出任务中发现的所有诊断日志槽位，包含角色和 ActivePeriod 信息。

**示例输出：**

```
slot_1 [active] 诊断日志: 2, 主控时段: 1 段
slot_2 [active] 诊断日志: 1, 主控时段: 1 段
```

---

### query-diag — 查询槽位诊断日志

```bash
python cli.py query-diag <task_id> -s <slot_id> [-o ./output]
```

查询指定槽位的诊断日志详细信息，包括文件名、大小、压缩状态、时间戳数量等。

**示例：**

```bash
python cli.py query-diag diagnostic_information_20260103 -s 1
```

---

### mech-slots — 机制模块槽位概况

```bash
python cli.py mech-slots <task_id> [-o ./output]
```

列出机制模块解析结果中各槽位的周期数、进程数、日志条数。

**示例输出：**

```
slot_1: 1 周期, 1 进程, 5 条日志
slot_2: 1 周期, 1 进程, 2 条日志
```

---

### mech-lifecycles — 周期与进程

```bash
python cli.py mech-lifecycles <task_id> -s <slot_id> [-o ./output]
```

列出指定槽位的机制模块周期和每个周期内的进程信息，含丢号检测。

**示例输出：**

```
20260103T000100-20260103T060000
  SERVICE-12345: 3 条
  dhcp-9881: 50 条 丢号:[12, 34]
```

---

### mech-logs — 查看原始日志

```bash
python cli.py mech-logs <task_id> -s <slot_id> -c <cycle_dir> -p <proc_name>-<pid> [-o ./output]
```

查看指定进程批次的机制模块原始日志。周期目录名和进程名-pid 可从 `mech-lifecycles` 命令获取。

**示例：**

```bash
python cli.py mech-logs diagnostic_information_20260103 \
  -s 1 \
  -c 20260103T000100-20260103T060000 \
  -p SERVICE-12345
```

**输出格式：**

```
[0001] [diagnostic|slot_1/diag.zip] 2026-01-03 00:01:00.100000 Service=SERVICE; ...
[0002] [diagnostic|slot_1/diag.zip] 2026-01-03 00:02:00.200000 Service=SERVICE; ...
       ↑ 序号      ↑ 来源+文件路径                              ↑ 原始日志行
```

---

### check-config — 检查配置

```bash
python cli.py check-config [-c config.yaml]
```

验证配置文件有效性，检查项包括：
- YAML 语法正确性
- `products` 段存在
- glob 模式（slot_dir_pattern、diag_file_patterns）可编译
- 正则表达式（timestamp_regex、diag_pattern、journal.patterns、sequence_pattern）可编译
- 机制模块配置完整性（module_name 非空、diag_pattern 命名组齐全）

**示例输出：**

```
✓ 配置加载成功
✓ 配置检查通过
```

---

### test-pattern — 测试正则匹配

```bash
python cli.py test-pattern -m <module_key> -t <diag|journal> "日志行" [-c config.yaml]
```

用配置文件中的正则测试一条实际日志行，验证匹配效果。显示：
- 是否匹配（diag_pattern 或 journal.line_pattern/line_pattern2）
- 提取的字段值（Service、Slot、CPU-Id、ProcessName、Context）
- 序号
- Stage1 模块名预过滤结果
- 主控关键字命中情况

**示例：**

```bash
# 测试诊断日志行
python cli.py test-pattern -m module1 -t diag \
  "2026-01-03 00:01:00.100000 Service=SERVICE; Slot=1; CPU-Id=0; ProcessName=SERVICE-12345; Context=No[1] EXAMPLE init ok)"

# 测试 journal 日志行
python cli.py test-pattern -m module1 -t journal \
  "2026-01-03T00:01:00 SERVICE: No[1] EXAMPLE journal entry 1"
```

**示例输出：**

```
✓ 匹配 diag_pattern
  模块名预过滤: EXAMPLE ✓
  Service: SERVICE
  Slot: 1
  CPU_Id: 0
  ProcessName: SERVICE-12345
  Context: No[1] EXAMPLE init ok)
  序号: 1
```

---

## 配置文件详解

配置文件为 `config.yaml`，分为两段：`pipeline`（管道全局配置）和 `products`（产品插件配置）。

### pipeline 段

```yaml
pipeline:
  recursive_extraction: true     # 是否递归解压外层压缩包
  inner_extraction: true         # 是否解压诊断日志内层压缩包
  generate_metadata: true        # 是否生成 metadata.json
  output_base_dir: "./output"    # 输出根目录
```

### products 段

每个产品定义一对插件（discovery + log_parser）及其配置：

```yaml
products:
  <产品名>:
    discovery:
      plugin: "模块路径.类名"
      config: { ... }
    log_parser:
      plugin: "模块路径.类名"
      config: { ... }
```

### discovery 配置

目录发现插件的配置项：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `diagnostic_dir` | 诊断日志目录名 | `diag` |
| `private_dir` | 私有日志目录名 | `varlog` |
| `slot_dir_pattern` | 槽位目录匹配（glob） | `slot_*` |
| `diag_file_patterns` | 诊断日志文件名匹配（glob 列表） | `["diag.zip", "diaglog_*.log.zip"]` |
| `filename_timestamp_regex` | 文件名时间戳提取正则 | `".*_(\\d{14})\\..*"` |
| `private_dir_patterns` | 私有日志目录匹配（glob 列表） | `["slot_*", "slot_*_cpu_*"]` |
| `archive_name` | 内层压缩包名 | `varlog.zip` |
| `journal_file_patterns` | journal 文件名匹配（glob 列表） | `["journal.log", "journal.log.*.gz"]` |
| `journal_sequence_regex` | journal 序号提取正则 | `"journal\\.log(?:\\.(\\d+))?(?:\\.gz)?"` |
| `compressed_extensions` | 压缩文件扩展名列表 | `[".gz", ".zip", ".tar.gz"]` |

### log_parser 配置

日志解析插件的配置项：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `timestamp_regex` | 日志行时间戳提取正则（含可选时区） | 见 config.yaml |
| `active_period_gap_threshold` | ActivePeriod 切分阈值（秒） | `300` |
| `mechanism_modules` | 机制模块配置字典 | 见下文 |

### mechanism_modules 配置

每个机制模块的配置项：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `module_name` | 日志中实际出现的模块名（全大写），用于 Stage1 预过滤 | `"EXAMPLE"` |
| `enabled` | 是否启用 | `true` |
| `diag_pattern` | 诊断日志匹配正则（需含命名组 Slot/CPU_Id/ProcessName/Context） | 见 config.yaml |
| `active_master_keyword` | 主控判定关键字（正则），命中 Context 时该 Slot 为 ACTIVE | `"MASTER_ACTIVE"` |
| `board_restart_indicator` | 板卡重启标识进程名，PID 变化触发周期切分 | `"dhcp"` |
| `process_name_mapping` | 进程名映射（诊断名 → journal 名） | `{"DHCP": "dhcpd"}` |
| `sequence_pattern` | 序号提取正则 | `"No\\[(\\d+)\\]"` |
| `journal.line_pattern` | journal 格式1正则（完整元数据块，4 个捕获组） | 见 config.yaml |
| `journal.line_pattern2` | journal 格式2正则（无元数据块，PID 可选，4 个捕获组） | 见 config.yaml |
| `journal.identifying_keyword` | journal Stage1 关键字预过滤 | `"EXAMPLE"` |

**journal 正则捕获组约定：**

| 组号 | 内容 | 格式1 示例 | 格式2 示例 |
|------|------|-----------|-----------|
| 1 | 进程名 | `SERVICE` | `SERVICE` |
| 2 | PID | `12345` | `12345` 或空 |
| 3 | 序号 | `1` | `1` |
| 4 | 上下文 | `msg content` | `msg content` |

---

## 输出结构说明

解析完成后，输出目录结构如下：

```
output/{task_id}/
├── metadata.json                    # 结构化元数据（诊断/私有/机制模块信息）
├── result.json                      # 完整解析结果（Pydantic 序列化）
├── extracted/                       # 外层解压产物
│   ├── diag/
│   │   ├── slot_1/
│   │   └── slot_2/
│   └── varlog/
│       ├── slot_1/
│       └── slot_1_cpu_1/
├── contents/                        # 内层解压产物（诊断日志 .zip 内容）
│   ├── slot_1/
│   │   ├── diag/
│   │   └── diaglog_1_20260103070000/
│   └── slot_2/
└── mech_modules/                    # 机制模块分进程日志
    └── {module_name}/
        ├── slot_1/
        │   ├── 20260103T000100-20260103T060000/    ← 周期目录（起止时间）
        │   │   ├── SERVICE-12345.log               ← 板卡级进程
        │   │   └── cpu_1/                          ← CPU 子卡进程
        │   │       └── SERVICE-67890.log
        │   └── 20260103T060100-20260103T120000/    ← 下一个重启周期
        └── slot_2/
```

**metadata.json 关键字段：**

```json
{
  "task_id": "diagnostic_information_20260103",
  "package_name": "diagnostic_information_20260103.zip",
  "diagnostic_slots": [
    {
      "slot_id": "1",
      "name": "slot_1",
      "role": "active",
      "active_periods": [
        { "start": "2026-01-03T00:00:00", "end": "2026-01-03T06:25:00" }
      ]
    }
  ],
  "mech_results": [
    {
      "module_name": "EXAMPLE",
      "active_master_slots": ["1"],
      "slots": [...]
    }
  ]
}
```

---

## 插件化扩展

### 新增产品插件

1. **创建 ScannerPlugin 子类**

在 `backend/plugins/<产品名>/scanner.py` 中继承 `DirectoryDiscoveryPlugin`：

```python
from backend.plugins.base import DirectoryDiscoveryPlugin

class MyScannerPlugin(DirectoryDiscoveryPlugin):
    def __init__(self, config, decompressor=None):
        super().__init__(config, decompressor)
        # 编译配置中的 glob/regex

    def discover(self, extracted_root):
        # 扫描目录，返回 (list[SlotInfo], list[PrivateSlotInfo])
        return diag_slots, private_slots
```

2. **在 config.yaml 中注册**

```yaml
products:
  my_product:
    discovery:
      plugin: "backend.plugins.my_product.scanner.MyScannerPlugin"
      config:
        diagnostic_dir: "my_diag"
        # ... 自定义配置
    log_parser:
      plugin: "backend.plugins.default.parser.ParserPlugin"  # 复用默认解析器
      config:
        timestamp_regex: "..."
        mechanism_modules: { ... }
```

3. **使用**

```bash
python cli.py parse <package> --product my_product
```

### 插件接口说明

**DirectoryDiscoveryPlugin（目录发现）：**

```python
class DirectoryDiscoveryPlugin(ABC):
    def __init__(self, config: dict, decompressor=None):
        self.config = config
        self.decompressor = decompressor  # 可选，用于解压内层压缩包

    @abstractmethod
    def discover(self, extracted_root: Path) -> tuple[list[SlotInfo], list[PrivateSlotInfo]]:
        """扫描 extracted_root，返回发现的 slot 信息。"""
```

**LogParserPlugin（日志解析）：**

```python
class LogParserPlugin(ABC):
    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def parse(self, result: ParseResult) -> ParseResult:
        """解析日志内容，填充时间戳、周期、角色等。"""

    @abstractmethod
    def write_output(self, mech_result: MechResult, output_dir: Path) -> Path:
        """落盘机制模块日志。"""
```

---

## 解压安全机制

`Decompressor` 类内置多层安全防护：

| 防护项 | 说明 | 参数 |
|--------|------|------|
| 路径穿越 | 拒绝 `../`、绝对路径的压缩包内文件 | 自动 |
| zip 炸弹 | 检测压缩比异常 | 阈值 100x |
| 文件大小 | 单文件解压后上限 | 500MB |
| 递归深度 | 最多递归扫描轮次 | 10 轮 |
| 编码容错 | UTF-8 优先，GBK 兜底 | 自动 |

**重要：所有解压必须通过 `Decompressor.extract_all()`，不要在业务代码中直接调用 `zipfile`/`tarfile`/`gzip`。**

---

## 测试

项目包含 93 个单元测试，覆盖核心模块：

```bash
# 运行全部测试
python -m pytest tests/ -v

# 运行特定模块测试
python -m pytest tests/test_utils.py -v            # utils 纯函数
python -m pytest tests/test_decompressor.py -v      # 解压安全
python -m pytest tests/test_parser_plugin.py -v     # 解析编排
python -m pytest tests/test_cycle_detector.py -v    # 周期检测
python -m pytest tests/test_role_identifier.py -v   # 角色判定
python -m pytest tests/test_output_writer.py -v     # 落盘输出
python -m pytest tests/test_scanner_plugin.py -v    # 目录发现
python -m pytest tests/test_plugin_loader.py -v     # 插件加载
python -m pytest tests/test_timestamp_extractor.py -v  # 时间戳提取
```

---

## 常见问题

### Q: Windows 下运行报 GBK 编码错误？

CLI 入口已自动将 stdout/stderr 切换为 UTF-8。如果仍有问题：

```bash
set PYTHONIOENCODING=utf-8
python cli.py parse <package>
```

### Q: 解压后文件丢失？

确保 `pipeline.recursive_extraction` 设置正确。`recursive=False`（默认）仅解压外层，内部压缩包保留原样供 ScannerPlugin 处理。

### Q: 机制模块没有输出？

排查步骤：

1. 运行 `python cli.py check-config` 确认配置正确
2. 运行 `python cli.py test-pattern -m module1 -t diag "实际日志行"` 确认正则能匹配
3. 使用 `--verbose` 查看条数统计，确认 Stage1 预过滤（模块名大小写敏感）没有过滤掉所有行

### Q: 如何添加新的日志匹配规则？

在 `config.yaml` 的 `mechanism_modules` 下添加新模块配置，确保 `diag_pattern` 包含必需的命名组（Slot、CPU_Id、ProcessName、Context）。

### Q: 主控判定不准确？

1. 优先设置 `active_master_keyword` 正则（匹配 Context 中的主控标识）
2. 如无明确关键字，系统会通过 ActivePeriod（时间戳连续性）兜底判定
3. 使用 `--verbose` 查看各 slot 的角色判定结果

### Q: 重启周期没有正确切分？

1. 确认 `board_restart_indicator` 配置了 indicator 进程名
2. 检查日志中该进程是否有 PID 变化（PID 变化是切分触发条件）
3. 序号回绕阈值 `SEQ_ROLLBACK_THRESHOLD=3`，可按需调整
