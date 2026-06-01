# logparse

日志解析维护工具，用于预处理产品设备的日志压缩包。支持统一多层归档解压，发现诊断日志和私有日志（varlog），通过可配置的机制模块优先判定主控，兜底通过目录+时间戳推断。输出结构化元数据供 AI agent 消费。

## 快速开始

```bash
pip install -r requirements.txt

# 解析日志包
python cli.py parse diagnostic_information_20260103.zip

# 检查配置
python cli.py check-config
```

## 命令参考

```bash
# 解析
python cli.py parse <package_path> [-c config.yaml] [-o ./output] [--verbose] [--product default|compact]
python cli.py parse <package_path> -c config.lifecycle-v2.yaml --product default  # 默认产品启用 lifecycle_split v2
python cli.py parse <package_path> --debug-expand-gz   # 调试用：就地展开普通 .gz 日志

# 查询
python cli.py info <task_id>
python cli.py list-slots <task_id>
python cli.py query-diag <task_id> -s <slot_id>
python cli.py mech-slots <task_id> [-m MODULE]
python cli.py mech-lifecycles <task_id> -s <slot_id> [-m MODULE]
python cli.py mech-logs <task_id> -s <slot_id> -c <board_cycle_dir> -p <proc_name>-<pid> [-m MODULE] [--cpu <cpu_id> --cpu-cycle <cpu_cycle_dir>]

# 调试
python cli.py check-config [-c config.yaml]
python cli.py test-pattern -m module1 -t diag "日志行"
python cli.py test-pattern -m module1 -t journal "日志行"
```

## 工作流程

```text
外层压缩包
  → Decompressor 统一解压归档包（外层 + 内层 zip/tar/tgz/tar.gz；普通 .gz 默认保留）
  → DirectoryDiscoveryPlugin 扫描已解压工作区
  → LogParserPlugin 提取基础时间戳和 ActivePeriod，编排机制模块插件
  → MechanismModulePlugin 解析特殊机制模块日志
  → MechOutputWriter 写出板卡周期 + 嵌套 CPU 周期日志
  → MetadataGenerator 生成 metadata.json，CLI 写出轻量 result.json
```

机制模块通过 `MechanismModulePlugin` 扩展。`module1` 是机制模块插件，拥有自己的日志扫描、周期切分和主控角色信号；其他模块如果没有周期切分或主控判定需求，可以只实现自己的解析逻辑。`module1` 支持日志行缺少 `No[n]` 的旧版本格式：同一个 slot family（slot 本体及其 CPU 子卡）的同一周期内，若所有 module1 日志都没有 `No[n]`，则按时间排序输出；若都有 `No[n]`，则保留按序号排序、缺号检测和 journal 序号回绕辅助切分。

当前输出模型以板卡周期为顶层生命周期，CPU 周期嵌套在对应板卡周期下。板卡日志写到 `slot_<id>/<board_cycle>/<proc>-<pid>.log`；CPU 日志写到 `slot_<id>/<board_cycle>/cpu_<id>/<cpu_cycle>/<proc>-<pid>.log`。`mech-logs` 查询 CPU 日志时需要同时传 `--cpu` 和 `--cpu-cycle`。

生命周期切分报错和 DFX 字段解读见 `docs/lifecycle-dfx-guide.md`。定位边界问题时可使用 `mech-lifecycles --show-boundaries` 查看结构化证据和建议查询命令。

`module1` 的新一代生命周期切分 `lifecycle_split` v2 默认关闭，只有显式配置 `enabled: true` 时才启用；未配置或 `enabled: false` 时继续使用旧 `CycleDetector`。仓库提供两份配置文件用于显式切换：`config.yaml` 保持默认产品 v2 关闭，`config.lifecycle-v2.yaml` 在默认产品 `module1` 中开启 v2，并且默认产品示例不再保留旧 `board_restart_indicator`、`board_restart_whitelist`、旧 `process_name_mapping`，避免把旧 CycleDetector 语义误认为 v2 配置。运行时通过 `-c/--config` 指定即可：

```bash
python cli.py parse diagnostic_information_20260103.zip -c config.yaml --product default
python cli.py parse diagnostic_information_20260103.zip -c config.lifecycle-v2.yaml --product default
```

启用后，compact `result.json` 会在对应 slot 下写入 `lifecycle_split_result`，其中包含 v2 boundaries、evidence 和 issues。这里的 compact 指轻量 `result.json` 输出模式，不是 `--product compact` 产品分支。查看方式：

```bash
python cli.py mech-lifecycles <task_id> -s <slot_id> -m <module_name> --show-boundaries --boundary-detail full
```

`module2` 是诊断日志-only 的机制模块示例。它依赖 `module1` 的生命周期切分结果，不自行切周期；解析到的 module2 日志会按 `slot + cpu_id + timestamp` 归入 module1 对应周期。CPU 日志优先匹配嵌套 CPU 周期，找不到 CPU 周期但能匹配板卡周期时写入 `cpu_<id>/unknown/`；无法匹配板卡周期的日志写入 `unknown/`。配置时需要把 `module2` 声明在它依赖的 `module1` 之后。

解压职责集中在 `Decompressor`。Scanner 插件只扫描统一解压后的工作区，不再自行解压 `varlog.zip` 或诊断日志内层包。内层归档会保留原文件，并在同目录生成 `*_extracted/` 目录供 scanner/parser 使用；如需降低磁盘占用，可配置 `pipeline.cleanup_inner_archives: true` 在解析后删除已展开的内层归档副本，或配置 `pipeline.cleanup_extracted: true` 删除整个 `extracted/` 工作区。

普通 `.gz` 日志（如 `journal.log.1.gz`）默认不会展开成独立文件，parser 会直接流式读取，避免批量解析时产生大量重复文件。只有传入 `--debug-expand-gz` 或配置 `pipeline.debug_expand_gz: true` 时，才会额外展开普通 `.gz`，方便人工排查。

`result.json` 默认使用 `pipeline.result_json_mode: "compact"`，只保留查询所需摘要，不再重复写入每条机制日志 raw 内容；需要历史完整对象时可改为 `"full"`。

## 测试

```bash
python -m pytest tests/ -v
```

## 变更记录

- 2026-05-29：新增大包资源占用优化配置。`result.json` 默认改为 compact 摘要；`pipeline.cleanup_extracted` 可在解析完成后删除 `extracted/`；`pipeline.cleanup_inner_archives` 可删除已展开的内层归档副本。
- 2026-05-29：生命周期输出改为板卡周期内嵌套 CPU 周期，`MechCpuCycle`、`lifecycle_reliable` 和 `boundary_issues` 已进入模型/元数据；`module2` 按 `slot + cpu_id + timestamp` 优先匹配嵌套 CPU 周期；`mech-logs` 支持 `--cpu` / `--cpu-cycle` 查询嵌套 CPU 日志。
- 2026-05-26：新增 `module2` 机制模块。module2 只扫描诊断日志，复用 module1 生命周期切分结果落盘；未匹配周期的日志写入 `unknown/`。module2 日志中 Slot 字段支持 `框号/slot` 格式（如 `1/2`），当前临时提取 `/` 后的 slot 号匹配 module1 周期，正式方案将保留完整框号语义。
- 2026-05-26：支持 `module1` 无 `No[n]` 日志格式。诊断日志和 journal 日志不再强制要求序号；journal 会从现有 4 组 `No[n]` pattern 自动派生无序号 fallback，一般无需手动修改 `config.yaml`；按 slot family 的周期判断排序模式，有序号周期继续使用 `No[n]` 排序和缺号检测，无序号周期按时间排序，并对混合状态记录 warning。
- 2026-05-26：`module1` 机制模块插件化。`ParserPlugin` 只负责编排机制模块插件，module1 自己拥有特殊日志解析、周期切分和主控判定逻辑。
- 2026-05-26：统一解压职责。`Decompressor` 负责外层和内层归档解压；Scanner 插件只扫描已解压工作区，不再自行解压 `varlog.zip` 或诊断日志内层包；普通 `.gz` 日志默认保留并由 parser 流式读取，调试时可通过 `--debug-expand-gz` 展开。

## Windows 终端编码

本项目源码、配置文件和文档均使用 UTF-8 编码。Windows PowerShell / CMD 在非 UTF-8 代码页下可能出现中文乱码。

推荐在运行 CLI 前执行：

PowerShell:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001
```

CMD:

```cmd
chcp 65001
```

如果只是查看文件内容，Windows PowerShell 建议显式指定编码：

```powershell
Get-Content .\README.md -Encoding UTF8
Get-Content .\config.yaml -Encoding UTF8
```

如果使用 VS Code 终端，建议确认：
- 文件编码为 UTF-8
- 终端使用 UTF-8
- 优先使用 PowerShell 7，而不是 Windows PowerShell 5.1

CLI 入口已自动将 stdout/stderr 切换为 UTF-8，避免 GBK 编码下 Unicode 符号报错。

## 编码约定

- 所有源码、配置、测试数据和文档默认使用 UTF-8
- 新增中文文档或测试 fixture 时，请确认保存为 UTF-8
- Windows 下查看文件时建议显式指定 `-Encoding UTF8`
