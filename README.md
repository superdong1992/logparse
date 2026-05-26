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
python cli.py parse <package_path> --debug-expand-gz   # 调试用：就地展开普通 .gz 日志

# 查询
python cli.py info <task_id>
python cli.py list-slots <task_id>
python cli.py query-diag <task_id> -s <slot_id>
python cli.py mech-slots <task_id> [-m MODULE]
python cli.py mech-lifecycles <task_id> -s <slot_id> [-m MODULE]
python cli.py mech-logs <task_id> -s <slot_id> -c <cycle_dir> -p <proc_name>-<pid> [-m MODULE]

# 调试
python cli.py check-config [-c config.yaml]
python cli.py test-pattern -m module1 -t diag "日志行"
python cli.py test-pattern -m module1 -t journal "日志行"
```

## 工作流程

```text
外层压缩包
  → Decompressor 统一解压归档包（外层 + 内层 zip/tar/tgz/tar.gz）
  → DirectoryDiscoveryPlugin 扫描已解压工作区
  → LogParserPlugin 提取基础时间戳和 ActivePeriod，编排机制模块插件
  → MechanismModulePlugin 解析特殊机制模块日志
  → MechOutputWriter 写出机制模块日志
  → MetadataGenerator 生成 metadata.json，CLI 写出 result.json
```

机制模块通过 `MechanismModulePlugin` 扩展。`module1` 是机制模块插件，拥有自己的日志扫描、周期切分和主控角色信号；其他模块如果没有周期切分或主控判定需求，可以只实现自己的解析逻辑。`module1` 支持日志行缺少 `No[n]` 的旧版本格式：同一个 slot family（slot 本体及其 CPU 子卡）的同一周期内，若所有 module1 日志都没有 `No[n]`，则按时间排序输出；若都有 `No[n]`，则保留按序号排序、缺号检测和 journal 序号回绕辅助切分。

`module2` 是诊断日志-only 的机制模块示例。它依赖 `module1` 的生命周期切分结果，不自行切周期；解析到的 module2 日志会按 slot 和时间归入 module1 对应周期，无法匹配周期的日志写入 `unknown/`。配置时需要把 `module2` 声明在它依赖的 `module1` 之后。

解压职责集中在 `Decompressor`。Scanner 插件只扫描统一解压后的工作区，不再自行解压 `varlog.zip` 或诊断日志内层包。内层归档会保留原文件，并在同目录生成 `*_extracted/` 目录供 scanner/parser 使用。

普通 `.gz` 日志（如 `journal.log.1.gz`）默认不会展开成独立文件，parser 会直接流式读取，避免批量解析时产生大量重复文件。只有传入 `--debug-expand-gz` 或配置 `pipeline.debug_expand_gz: true` 时，才会额外展开普通 `.gz`，方便人工排查。

## 测试

```bash
python -m pytest tests/ -v
```

## 变更记录

- 2026-05-26：新增 `module2` 机制模块。module2 只扫描诊断日志，复用 module1 生命周期切分结果落盘；未匹配周期的日志写入 `unknown/`。
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
