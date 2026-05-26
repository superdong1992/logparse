# logparse Usage Guide

`logparse` 是一个用于解析诊断日志包、提取机制模块日志、分析板卡生命周期并输出结构化结果的日志解析框架。

当前版本特性：

- 插件化 discovery / parser 架构
- 多产品支持
- 机制模块日志拆分
- 生命周期分析
- 多机制模块查询
- 配置预飞检查
- 流式读取普通 `.gz` 日志
- 安全压缩包解压
- Windows UTF-8 终端支持

---

# 安装与环境

推荐 Python 版本：

```text
Python 3.11+
```

创建虚拟环境：

```bash
python -m venv .venv
```

Linux / macOS:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

安装依赖：

```bash
pip install -r requirements.txt
```

---

# Windows 终端编码

本项目源码、配置文件和文档均使用 UTF-8 编码。

Windows PowerShell / CMD 在非 UTF-8 代码页下可能出现中文乱码。推荐在运行 CLI 前执行：

PowerShell:

```powershell
chcp 65001
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
```

CMD:

```cmd
chcp 65001
```

查看文件时建议显式指定 UTF-8：

```powershell
Get-Content .\README.md -Encoding UTF8
Get-Content .\config.yaml -Encoding UTF8
Get-Content .\usage.md -Encoding UTF8
```

推荐：

- 使用 PowerShell 7
- 使用 Windows Terminal
- 确认编辑器保存为 UTF-8

---

# 配置预飞检查

正式解析前，建议先执行配置检查：

```bash
python cli.py check-config --config config.yaml
```

当前会检查：

- 配置文件结构
- `products` 配置
- `discovery` / `log_parser` 插件结构
- 插件模块是否可导入
- 插件类是否存在
- 插件类是否继承预期基类
- 插件关键方法是否存在
- mechanism module 正则是否合法
- journal pattern 捕获组数量
- diag pattern 命名组完整性

成功示例：

```text
✓ 配置加载成功
✓ 配置检查通过
```

失败时会输出具体配置路径和错误原因，例如：

```text
配置检查失败:
  - products.default.discovery.plugin 必须是非空字符串
  - mechanism_modules.example.diag_pattern 缺少命名组 CPU_Id
```

---

# 基础解析

解析默认产品：

```bash
python cli.py parse diagnostic_information_20260103.zip \
  --output output \
  --product default
```

解析 compact 产品：

```bash
python cli.py parse compact_package_20260103.zip \
  --output output \
  --product compact
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--output` | 输出目录 |
| `--product` | 产品类型 |
| `--config` | 配置文件路径 |
| `--debug-expand-gz` | 调试用：额外展开普通 `.gz` 日志 |

---

# 普通 `.gz` 日志处理

默认情况下，普通 `.gz` 文件不会被预展开。解析器会在需要时直接流式读取 `.gz` 内容。

如果启用调试展开：

```yaml
pipeline:
  debug_expand_gz: true
```

或命令行：

```bash
python cli.py parse xxx.zip --debug-expand-gz
```

工具会额外在同目录生成去掉 `.gz` 后缀的文件。例如：

```text
journal.log.1.gz
journal.log.1
```

原始 `.gz` 文件会保留。如果目标文件已存在，工具会跳过展开并记录 warning。

该功能仅建议用于调试、人工排查、日志内容比对，不建议在正式批量解析中长期打开。

---

# 查询机制模块结果

## 查看全部机制模块的 slot

```bash
python cli.py mech-slots <task_id> \
  --output output
```

示例：

```bash
python cli.py mech-slots diagnostic_information_20260103 \
  --output output
```

输出示例：

```text
[EXAMPLE] slot_1: 2 周期, 3 进程, 120 条日志
[EXAMPLE] slot_2: 1 周期, 2 进程, 80 条日志
```

## 只查看指定机制模块

```bash
python cli.py mech-slots diagnostic_information_20260103 \
  --output output \
  --module EXAMPLE
```

## 查看 slot 生命周期

```bash
python cli.py mech-lifecycles diagnostic_information_20260103 \
  --output output \
  -s 1
```

输出示例：

```text
[EXAMPLE] slot_1
20260103T000100-20260103T000200
  SERVICE-12345: 80 条
  TASK-888: 40 条
```

## 只查看指定机制模块的生命周期

```bash
python cli.py mech-lifecycles diagnostic_information_20260103 \
  --output output \
  -s 1 \
  --module EXAMPLE
```

## 查看机制模块日志

```bash
python cli.py mech-logs diagnostic_information_20260103 \
  --output output \
  -s 1 \
  -c 20260103T000100-20260103T000200 \
  -p SERVICE-12345
```

指定机制模块：

```bash
python cli.py mech-logs diagnostic_information_20260103 \
  --output output \
  -s 1 \
  -c 20260103T000100-20260103T000200 \
  -p SERVICE-12345 \
  --module EXAMPLE
```

查询参数说明：

| 参数 | 说明 |
|---|---|
| `-s / --slot` | slot 编号 |
| `-c / --cycle` | 周期目录名 |
| `-p / --proc` | 进程名 |
| `--module` | 机制模块名；不传时默认查询全部模块或使用默认模块 |

---

# 输出目录结构

解析结果示例：

```text
output/
└── diagnostic_information_20260103/
    ├── extracted/         ← 外层诊断包解压结果
    ├── contents/          ← 内层日志包解压结果
    ├── result.json        ← 完整解析结果
    ├── metadata.json      ← 轻量索引
    └── mech_modules/      ← 机制模块输出
        └── EXAMPLE/
            └── slot_1/
                └── 20260103T000100-20260103T000200/
                    └── SERVICE-12345.log
```

目录说明：

| 路径 | 说明 |
|---|---|
| `extracted/` | 外层诊断包解压目录 |
| `contents/` | 内层日志包解压目录 |
| `result.json` | 完整结构化解析结果 |
| `metadata.json` | 轻量索引文件 |
| `mech_modules/` | 机制模块日志拆分结果 |

---

# 错误处理与退出码

`parse` 命令会尽量保留部分可用结果。

- 非致命错误（如某个内层日志包解压失败）写入 `result.errors`，流程继续
- 致命错误（如外层包无法解压、目录扫描失败、核心解析失败）写入 `result.errors`，CLI 返回非 0 退出码
- 所有错误都会打印到终端，并写入 `result.json`

## `metadata.json` 与 `result.json`

`metadata.json` 是轻量索引文件，适合快速查看解析到的 slot、时间范围、关键输出路径。

`result.json` 是完整结果文件，包含更详细的解析结构、错误信息和机制模块输出信息。AI agent 或自动化系统建议优先读取 `result.json`。

---

# 配置结构说明

配置文件示例：

```yaml
pipeline:
  recursive_extraction: true
  debug_expand_gz: false

products:
  default:
    discovery:
      plugin: default
      config:
        diag_dir_names:
          - diagnostic_log
        varlog_dir_names:
          - varlog

    log_parser:
      plugin: default
      config:
        timestamp_patterns:
          - "\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}"

        mechanism_modules:
          example:
            module_name: EXAMPLE
            diag_pattern: "..."
```

主要字段：

| 字段 | 说明 |
|---|---|
| `pipeline.recursive_extraction` | 是否递归解压归档压缩包 |
| `pipeline.debug_expand_gz` | 是否调试展开普通 `.gz` 日志 |
| `products` | 产品配置 |
| `discovery` | 日志发现插件 |
| `log_parser` | 日志解析插件 |
| `mechanism_modules` | 机制模块配置 |
| `timestamp_patterns` | 时间戳正则 |

---

# 推荐使用流程

## 1. 执行配置预飞检查

```bash
python cli.py check-config --config config.yaml
```

## 2. 执行日志解析

```bash
python cli.py parse diagnostic_information_20260103.zip \
  --output output \
  --product default
```

## 3. 查看机制模块结果

```bash
python cli.py mech-slots diagnostic_information_20260103 \
  --output output
```

## 4. 查看生命周期

```bash
python cli.py mech-lifecycles diagnostic_information_20260103 \
  --output output \
  -s 1
```

## 5. 查看具体日志

```bash
python cli.py mech-logs diagnostic_information_20260103 \
  --output output \
  -s 1 \
  -c 20260103T000100-20260103T000200 \
  -p SERVICE-12345
```

---

# 开发与测试

运行全部测试：

```bash
pytest -q
```

运行指定测试：

```bash
pytest tests/test_query.py -q
pytest tests/test_pipeline.py -q
pytest tests/test_config_validation.py -q
```

推荐开发验证流程：

```text
修改代码
→ check-config
→ pytest
→ parse mock package
→ 查询结果验证
```

---

# 编码约定

- 所有源码、配置、测试数据和文档默认使用 UTF-8
- 新增中文文档时请确认保存为 UTF-8
- Windows PowerShell 查看文件时建议显式指定 `-Encoding UTF8`
- 推荐使用 PowerShell 7 或 Windows Terminal

---

# 当前版本

当前推荐版本：

```text
v0.3.1
```

当前已具备：

- 插件化 parser / discovery
- 安全压缩包解压
- 多机制模块支持
- 生命周期分析
- 机制模块查询
- 配置预飞检查
- 普通 `.gz` 流式读取
- Windows UTF-8 支持
- 完整测试覆盖
