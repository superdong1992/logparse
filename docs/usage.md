# logparse Usage Guide

`logparse` 是一个用于解析诊断日志包、提取机制模块日志、分析板卡生命周期并输出结构化结果的日志解析框架。

当前版本特性：

- 插件化 discovery / parser 架构
- 多产品支持
- 机制模块日志拆分
- 生命周期分析
- 板卡周期内嵌套 CPU 周期输出
- 多机制模块查询
- 配置预飞检查
- 流式读取普通 `.gz` 日志
- 安全压缩包解压
- Windows UTF-8 终端支持
- 轻量 `result.json` 和解析后清理配置

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

默认产品启用 lifecycle_split v2：

```bash
python cli.py parse diagnostic_information_20260103.zip \
  --config config.lifecycle-v2.yaml \
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

默认情况下，即未开启 `--debug-expand-gz` 且配置项 `debug_expand_gz: false` 时：

- 普通 `.gz` 日志不会被额外展开成 `.gz_extracted/` 或同名无 `.gz` 文件
- `extracted/` 目录会保留原始普通 `.gz` 文件
- parser 会在需要时直接流式读取普通 `.gz` 内容
- `.tar.gz` / `.tgz` 属于归档压缩包，不属于这里说的“普通 `.gz` 日志”，仍可能按递归解压规则处理

这里的“普通 `.gz`”指类似：

```text
journal.log.1.gz
xxx.log.gz
```

不包括：

```text
archive.tar.gz
archive.tgz
```

如果需要人工查看普通 `.gz` 内容，可以显式开启：

```bash
python cli.py parse xxx.zip \
  --output output \
  --product default \
  --debug-expand-gz
```

开启后，可能会额外生成：

```text
journal.log.1.gz_extracted/
```

或其他用于人工查看的展开文件。

该功能仅建议用于：

- 调试
- 人工排查
- 日志内容比对

不建议在正式批量解析中长期打开。

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
    cpu_1/20260103T000130-20260103T000180
      TASK-888: 40 条
```

## 只查看指定机制模块的生命周期

```bash
python cli.py mech-lifecycles diagnostic_information_20260103 \
  --output output \
  -s 1 \
  --module EXAMPLE
```

## lifecycle_split v2 / v3

`module1` 的 `lifecycle_split` 默认关闭。只有在机制模块配置里显式写
`enabled: true` 时才启用；未配置 `lifecycle_split` 或写 `enabled: false`
都会继续使用旧 `CycleDetector`。

缺省 `algorithm` 保持当前 v2 行为，也就是 `interval_v2`。配置
`algorithm: interval_v3` 时启用 V3：先用 `>=30 秒` 静默间隔生成候选生命周期，
再从左到右反复尝试聚合相邻候选段。合并后每个白名单进程不同 PID 数 `<=1` 时允许聚合；
若白名单 PID 冲突或 reliable journal 回绕跨候选段对齐，则保留切分。

仓库提供两份配置文件用于通过 `-c/--config` 切换默认产品的实现：

| 配置文件 | 默认产品 `module1` 行为 | 用法 |
|---|---|---|
| `config.yaml` | `lifecycle_split.enabled: false`，继续使用旧 `CycleDetector` | `python cli.py parse <package> -c config.yaml --product default` |
| `config.lifecycle-v2.yaml` | `lifecycle_split.enabled: true` 且 `algorithm: interval_v2`，默认产品走 v2 | `python cli.py parse <package> -c config.lifecycle-v2.yaml --product default` |

`config.lifecycle-v2.yaml` 的默认产品 `module1` 不保留旧 `board_restart_indicator`、
`board_restart_whitelist`、旧 `process_name_mapping`。v2 不兼容旧 indicator/whitelist
语义，生命周期相关进程只通过 `lifecycle_split.process_name_mapping`、
`lifecycle_split.reliable_processes` 和 `lifecycle_split.multi_instance_processes` 配置。

注意：配置文件里的 `products.compact` 是 `--product compact` 的产品示例；它和
`pipeline.result_json_mode: "compact"` 的轻量 `result.json` 输出模式不是一回事。
默认产品解析只读取 `products.default` 下的机制模块配置。

启用示例：

```yaml
lifecycle_split:
  enabled: true
  algorithm: interval_v3   # 缺省 interval_v2；未知 algorithm 会报配置错误
  process_name_mapping:
    canonical_proc:
      - alias_in_diag
      - alias_in_journal
  reliable_processes:
    - canonical_lifecycle_proc
  multi_instance_processes: []
```

`reliable_processes` 是统一的 canonical 进程列表，不再拆分 board/cpu。
日志实际无 `cpu_id` 或 `cpu_id=0` 时，PID changed 生成 board scope 边界证据；
日志实际带 CPU 编号时，PID changed 生成对应 CPU scope 边界证据。
journal 回绕在 V3 中只作为候选边界证据：如果回绕前日志在前候选段、回绕后日志在后候选段，
则保留这条候选切分。
journal 缺 PID 或缺 `No[]` 序号是正常输入：这些日志会保留到归档中，但不会被当成
`invalid_lifecycle_evidence`。只有被用于正向边界求解的证据缺必要字段时才会记录
`invalid_lifecycle_evidence`，并在 DFX 中带中文原因、来源文件和原始日志片段。

启用后，compact `result.json` 会在 slot 下包含 `lifecycle_split_result`，
V2 记录 boundaries、evidence 和 issues；V3 记录 `algorithm: interval_v3`、
candidate_segments、merge_decisions、lifecycles、journal_evidence、issues 和
lifecycle_reliable。普通 `parse` 默认只显示 error 摘要；`--verbose` 不再展开生命周期
DFX。查看中文聚合/切分说明：

```bash
python cli.py parse <package> -c config.yaml --lifecycle-dfx decisions
```

查看落盘结果里的完整中文 DFX：

```bash
python cli.py mech-lifecycles diagnostic_information_20260103 \
  --output output \
  -s 1 \
  --module EXAMPLE \
  --show-boundaries \
  --lifecycle-dfx full
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

查询嵌套 CPU 周期日志：

```bash
python cli.py mech-logs diagnostic_information_20260103 \
  --output output \
  -s 1 \
  -c 20260103T000100-20260103T000200 \
  -p SERVICE-12345 \
  --module EXAMPLE \
  --cpu 1 \
  --cpu-cycle 20260103T000130-20260103T000180
```

查询参数说明：

| 参数 | 说明 |
|---|---|
| `-s / --slot` | slot 编号 |
| `-c / --cycle` | 板卡周期目录名 |
| `-p / --proc` | 进程名-PID 文件名前缀 |
| `--module` | 机制模块名；`mech-logs` 不传时默认取第一个机制模块 |
| `--cpu` | CPU ID；查询 CPU 日志时传入 |
| `--cpu-cycle` | CPU 周期目录名；查询 CPU 日志时传入，不传则落到 `unknown` |

---

# 输出目录结构

解析结果示例：

```text
output/
└── diagnostic_information_20260103/
    ├── extracted/                 # cleanup_extracted=true 时不保留
    ├── result.json
    └── mech_modules/
        └── EXAMPLE/
            └── slot_1/
                └── 20260103T000100-20260103T000200/
                    ├── SERVICE-12345.log
                    └── cpu_1/
                        └── 20260103T000130-20260103T000180/
                            └── TASK-888.log
```

目录说明：

| 路径 | 说明 |
|---|---|
| `extracted/` | 原始解压目录；`pipeline.cleanup_extracted: true` 时解析完成后删除 |
| `result.json` | 结构化解析结果；默认 compact 摘要，不重复保存每条 raw 日志 |
| `mech_modules/` | 机制模块日志拆分结果 |

机制模块日志使用板卡周期作为顶层目录。板卡日志直接写到板卡周期下；CPU 日志写到 `cpu_<id>/<cpu_cycle>/`，如果只匹配到板卡周期但没有匹配到 CPU 周期，则进入 `cpu_<id>/unknown/`。

---

# 配置结构说明

配置文件示例：

```yaml
pipeline:
  recursive_extraction: true
  debug_expand_gz: false
  result_json_mode: "compact"
  cleanup_extracted: false
  cleanup_inner_archives: false

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
| `pipeline.result_json_mode` | `compact` 写轻量结果摘要；`full` 写完整 ParseResult |
| `pipeline.cleanup_extracted` | 解析完成后是否删除整个 `extracted/` 工作区 |
| `pipeline.cleanup_inner_archives` | 保留 `extracted/` 时，是否删除已有 `_extracted/` 目录的内层归档副本 |
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

当前工作区版本：

```text
2026-05-29
```

当前已具备：

- 插件化 parser / discovery
- 安全压缩包解压
- 多机制模块支持
- 生命周期分析
- 板卡周期内嵌套 CPU 周期输出
- 机制模块查询
- 配置预飞检查
- 普通 `.gz` 流式读取
- Windows UTF-8 支持
- 完整测试覆盖
