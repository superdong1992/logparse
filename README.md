# logparse

日志解析维护工具，用于预处理产品设备的日志压缩包。支持多层解压，发现诊断日志和私有日志（varlog），通过可配置的机制模块优先判定主控，兜底通过目录+时间戳推断。输出结构化元数据供 AI agent 消费。

## 快速开始

```bash
pip install -r requirements.txt

# 解析日志包
python cli.py parse diagnostic_information_20260103.zip

# 检查配置
python cli.py check-config
```

## 错误处理

- `parse` 成功返回退出码 0
- 致命错误（解压/扫描/解析失败）返回非 0 退出码
- 错误同时写入终端和 `result.json`

## 命令参考

```bash
# 解析
python cli.py parse <package_path> [-c config.yaml] [-o ./output] [--verbose] [--product default|compact]
python cli.py parse <package_path> --debug-expand-gz   # 调试用：同目录生成去掉 .gz 后缀的文件

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

## 测试

```bash
python -m pytest tests/ -v
```

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
