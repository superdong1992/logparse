# 旧版配置迁移到 schema v2

本文适用于原先使用 `default` 解析 Module1 和 Module2，现在需要升级到当前配置格式，并将设备款型命名为 `galaxy` 的场景。迁移需要调整字段位置、插件路径和产品选择参数，再保留旧配置中实际有效的日志规则。

当前主配置必须显式声明整数 `schema_version: 2`。缺失版本号、`schema_version: 1`、字符串 `"2"` 都不能通过校验。运行时已移除 v1 自动转换和 `migrate-config`，只补一行版本号不能完成迁移。

## 迁移前准备

先在原环境备份旧 `config.yaml` 及其引用的产品配置。新配置先保存为仓库根目录下的 `config-galaxy.yaml`，验证完成后再切换调用方；原有 `config.yaml` 可以继续保留。

本文示例沿用当前 [default 产品模板](../configs/products/default.yaml)。模板里的 `EXAMPLE`、`MODULE2`、`xxx` 和正则只用于说明配置结构，不代表真实 galaxy 日志规格。实际匹配值应从旧配置迁入；真实配置和日志留在 LAN 中处理。

如果旧配置已经是 v2，只需核对插件路径、产品改名和业务参数，不要再次移动已经位于正确位置的字段。更早版本中的生命周期开关、重启规则或自定义插件，需要分别按后文处理。

## 第一步 区分需要改名和需要保留的内容

| 名称或字段 | 迁移为 galaxy 时的处理 |
| --- | --- |
| 产品键 `products.default` | 改为 `products.galaxy`，用于选择整套产品配置 |
| 命令参数 `--product default` | 改为 `--product galaxy`；`-p galaxy` 是同义写法 |
| 产品文件 `configs/products/default.yaml` | 复制为 `configs/products/galaxy.yaml`，同步更新 `$include`；文件名不强制与产品键相同 |
| 机制键 `module1`、`module2` | 保留，供机制依赖和查询使用 |
| Module2 的依赖 | 指向机制键 `module1`，不填写 `galaxy` 或业务模块名 |
| `config.module_name` | 保留旧配置的业务模块名；它还影响证据目录，Module1 也用它过滤诊断行 |
| `identifying_keyword` | 保留日志中实际出现的识别关键字，不替换为设备型号 |
| 插件路径中的 `current`、`module1`、`module2` | 使用当前代码路径，不替换为设备型号 |

`galaxy` 是设备款型的配置标识，Module2 示例中的 `xxx` 是日志过滤关键字，两者没有对应关系。不要全局替换所有 `default`、`EXAMPLE` 或 `xxx`。

## 第二步 调整旧配置结构

下表中的 `products.default` 表示旧产品键，`products.galaxy` 表示迁移后的产品键。使用拆分文件时，`products.galaxy` 下的内容就是 `configs/products/galaxy.yaml` 的文件正文。

| 旧字段 | 当前字段或处理方式 |
| --- | --- |
| 顶层未写版本号，或 `schema_version: 1` | 顶层写 `schema_version: 2`，同时完成以下结构调整 |
| `products.default.log_parser` | 改为 `products.galaxy.parser` |
| `products.default.log_parser.config.active_period_gap_threshold` | 改为 `products.galaxy.parser.config.active_period_gap_seconds`，数值单位仍为秒 |
| `products.default.log_parser.config.mechanism_modules` | 整段移到 `products.galaxy.mechanisms`，与 `parser` 同级 |
| `products.default.discovery.config.compressed_extensions` | 移到 `products.galaxy.archive.compressed_extensions` |
| `pipeline.recursive_extraction` | 移到 `products.galaxy.archive.recursive_extraction`；多个产品分别配置 |
| Module2 的 `config.depends_on_module: module1` | 删除旧字段，在 Module2 条目下新增 `depends_on: [module1]`，与 `plugin`、`enabled`、`config` 同级 |
| `pipeline.cleanup_extracted` | 改为 `pipeline.keep_workspace`，布尔值取反：原 `true` 对应新 `false` |
| `pipeline.debug_expand_gz` | 保留 |
| `pipeline.extraction_workers`、`pipeline.diagnostic_scan_workers` | 保留有效的原值；示例使用 `auto` |
| `pipeline.inner_extraction`、`pipeline.cleanup_inner_archives` | 删除，解压由统一解压器负责 |
| `pipeline.generate_metadata` | 删除，成功解析会生成 metadata |
| `pipeline.output_base_dir` | 删除，在解析命令中使用 `--output` 指定输出根目录 |
| `pipeline.result_json_mode` | 删除，`result.json` 固定为紧凑查询索引 |

移动后删除旧位置的字段。主配置顶层只保留 `schema_version`、`pipeline`、`products`；不要把旧结构与新结构同时留在文件中。

当前产品配置需要 `archive.recursive_extraction` 和 `archive.compressed_extensions`。如果旧配置没有这些字段，应根据包结构明确设置；下方模板启用递归解压，并列出常用压缩扩展名，不应直接当成旧配置的默认行为。

### 更新四个内置插件路径

下表适用于原来使用仓库自带插件的配置。自定义插件不能只替换类名，需要按当前插件接口检查实现。

| 配置位置 | 旧路径 | 当前路径 |
| --- | --- | --- |
| `discovery.plugin` | `backend.plugins.default.scanner.ScannerPlugin` | `backend.extensions.products.current.scanner.ScannerPlugin` |
| `parser.plugin` | `backend.plugins.default.parser.ParserPlugin` | `backend.extensions.products.current.parser.ParserPlugin` |
| `mechanisms.module1.plugin` | `backend.plugins.mechanisms.module1.Module1Plugin` | `backend.extensions.mechanisms.module1.Module1Plugin` |
| `mechanisms.module2.plugin` | `backend.plugins.mechanisms.module2.Module2Plugin` | `backend.extensions.mechanisms.module2.Module2Plugin` |

### 核对生命周期字段

`mechanisms.module1.config.lifecycle_split` 只支持以下三项：

- `process_name_mapping`：规范进程名到别名的映射。
- `reliable_processes`：可靠进程名列表。
- `multi_instance_processes`：多实例进程名列表。

后两项应为扁平列表；按别名映射和大小写归一化后，同一进程不能同时出现在两项中。沿用已经符合 V3 规则的业务配置，不要为了通过校验把已有内容清空。

如果旧配置在 Module1 的 `config` 下直接放置 `process_name_mapping`，应确认映射方向后移到 `config.lifecycle_split.process_name_mapping`。旧的 `board_restart_indicator`、`board_restart_whitelist`，以及 `lifecycle_split` 内的算法或开关字段已不支持；它们没有通用的一对一改名关系，需要结合实际日志重新确认 V3 的进程配置。当前固定使用 `interval_v3`，配置格式版本 v2 与生命周期算法 V3 是两件事。

## 第三步 保存新的主配置和产品配置

建议采用以下文件布局：

```text
logparse/
  config-galaxy.yaml
  configs/
    products/
      galaxy.yaml
```

### 主配置 config-galaxy.yaml

以下配置只声明 `galaxy`。如果还需要 `compact` 等其他产品，可在 `products` 下保留相应条目；整份配置都需要通过校验。

```yaml
schema_version: 2
pipeline:
  debug_expand_gz: false
  extraction_workers: auto
  diagnostic_scan_workers: auto
  keep_workspace: false
products:
  galaxy:
    $include: configs/products/galaxy.yaml
```

`$include` 相对于主配置所在目录解析，必须使用该目录内的相对路径，不能用绝对路径或 `..` 逃逸。一个产品条目不能同时写 `$include` 和内联配置字段。产品文件本身不写 `schema_version` 或 `products` 包装层。

如果希望只用一个文件，可以删除 `$include`，把下方产品配置整体缩进到 `products.galaxy` 下。

### 产品配置 configs/products/galaxy.yaml

以下是完整的结构模板，取自当前 default 配置。先按下一节的替换表迁入旧业务参数，再用于实际日志；不要直接用模板中的示例值覆盖旧参数。

```yaml
archive:
  recursive_extraction: true
  compressed_extensions:
    - .gz
    - .zip
    - .tar.gz
    - .tgz
    - .tar
discovery:
  plugin: backend.extensions.products.current.scanner.ScannerPlugin
  config:
    diagnostic_dir: diag
    private_dir: varlog
    slot_dir_pattern: slot_*
    diag_file_patterns:
      - diag.zip
      - diaglog_*.log.zip
      - diaglog_*.zip
    loose_diagnostics:
      enabled: true
      file_patterns:
        - diag.zip
        - diaglog_*.log.zip
        - diaglog_*.zip
    filename_timestamp_regex: '.*_(\d{14})\..*'
    private_dir_patterns:
      - slot_*
      - slot_*_cpu_*
    archive_name: varlog.zip
    journal_file_patterns:
      - journal.log
      - journal.log.*.gz
    journal_sequence_regex: 'journal\.log(?:\.(\d+))?(?:\.gz)?'
parser:
  plugin: backend.extensions.products.current.parser.ParserPlugin
  config:
    timestamp_regex: '(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?'
    active_period_gap_seconds: 300
mechanisms:
  module1:
    plugin: backend.extensions.mechanisms.module1.Module1Plugin
    enabled: true
    depends_on: []
    config:
      module_name: EXAMPLE
      diag_pattern: 'Service=(?P<Service>[^;]+).*?Slot=(?P<Slot>[^;,)]+).*?CPU-Id=(?P<CPU_Id>[^;,)]*).*?ProcessName=(?P<ProcessName>[^;,)]+).*?Context=(?P<Context>.+?)\)$'
      active_master_keyword: ''
      lifecycle_split:
        process_name_mapping: {}
        reliable_processes: []
        multi_instance_processes: []
      journal:
        line_pattern: '^\S+\s+\S+\s+\S+?:\s+\[slotId\s*=\s*\d+,\s*cpuId\s*=\s*\d+,\s*processName\s*=\s*(\S+?)-(\d+)\]:\s+No\[(\d+)\](.+)$'
        line_pattern2: '^\S+\s+\S+\s+(\S+?)(?:-(\d+))?:\s+No\[(\d+)\](.+)$'
        line_pattern2_required_substrings: []
        identifying_keyword: EXAMPLE
      sequence_pattern: 'No\[(\d+)\]'
  module2:
    plugin: backend.extensions.mechanisms.module2.Module2Plugin
    enabled: true
    depends_on:
      - module1
    config:
      module_name: MODULE2
      identifying_keyword: xxx
      diag_pattern: 'Slot=(?P<Slot>[\d/]+),CPU-Id=(?P<CPU_Id>[^,]*),ProcessName=(?P<ProcessName>[^,]+),Context="(?P<Context>.*?)"'
```

### 从旧配置迁入哪些实际值

下表路径均相对于产品配置正文。保持 YAML 正则的单引号写法，可以避免反斜杠被双引号字符串解释。

| 字段 | 应使用的实际值 |
| --- | --- |
| `discovery.config` 下的目录、文件模式、文件名时间戳和 journal 序号规则 | 沿用旧配置中与日志包结构匹配的值。散落日志依赖 `loose_diagnostics.file_patterns`，模板不会匹配任意 `.log` |
| `parser.config.timestamp_regex` | 沿用实际日志时间戳正则 |
| `parser.config.active_period_gap_seconds` | 使用旧 `active_period_gap_threshold` 的秒数；它描述日志连续覆盖的间隔，不是进程重启阈值 |
| `mechanisms.module1.config.module_name` | 将 `EXAMPLE` 替换为旧业务模块名；诊断行必须包含该值的大写形式，检查区分大小写 |
| `mechanisms.module1.config.diag_pattern` | 沿用实际 Module1 诊断正则；保留 `Slot`、`CPU_Id`、`ProcessName`、`Context` 命名组 |
| `mechanisms.module1.config.journal` | 迁入实际 `identifying_keyword`、两种行正则和第二种格式的必需子串；不要遗漏旧过滤条件 |
| `mechanisms.module1.config.sequence_pattern` | 沿用实际日志序号正则 |
| `mechanisms.module1.config.active_master_keyword` | 沿用经过确认的主控标识；旧配置没有可靠标识时，不根据设备名编造 |
| `mechanisms.module1.config.lifecycle_split` | 保留实际进程映射、可靠进程和多实例进程知识，按 V3 字段校验 |
| `mechanisms.module2.config.module_name` | 将 `MODULE2` 替换为原业务模块名，影响模块标识和证据目录 |
| `mechanisms.module2.config.identifying_keyword` | 将 `xxx` 替换为实际诊断行关键字；检查区分大小写 |
| `mechanisms.module2.config.diag_pattern` | 沿用实际 Module2 诊断正则；同样保留上述四个命名组 |

Module1 的 journal 行正则需要 3 或 4 个捕获组，含日志序号的格式应使用 4 组，顺序为进程名、PID、序号、正文。其他只用于组织正则的括号应使用非捕获组 `(?:...)`。Module2 只处理诊断日志，复用 Module1 的生命周期，因此两个机制都应启用，且 Module2 的 `depends_on` 只包含 `module1`。

## 第四步 校验并切换调用方

以下 PowerShell 命令从仓库根目录执行，使用 `.venv` 中的 Python 3.12。POSIX 环境将解释器路径改为 `.venv/bin/python`。

先检查结构、环境和机制依赖：

```powershell
.venv\Scripts\python.exe cli.py check-config --config config-galaxy.yaml
.venv\Scripts\python.exe cli.py doctor --config config-galaxy.yaml --product galaxy
.venv\Scripts\python.exe cli.py explain-config --config config-galaxy.yaml --product galaxy --json
```

确认生效产品是 `galaxy`、两个机制均启用、执行顺序为 `module1` 后 `module2`，且 Module2 依赖为 `module1`。配置通过校验，只能说明结构、插件与依赖有效；正则和关键字是否匹配仍需要解析样例。

在 LAN 中选择一份已知同时包含两个模块的代表性日志，替换下面的输入路径和全新输出目录后执行：

```powershell
.venv\Scripts\python.exe cli.py parse "D:\logs\galaxy-sample.zip" --config config-galaxy.yaml --product galaxy --output "D:\logparse-validation\galaxy-v2-first-run"
```

核对扫描文件覆盖、两个机制的条目及错误信息，再抽查已知 slot、进程、PID 和时间窗口的生命周期与目标日志。把新旧结果写到不同目录，避免覆盖旧证据。仓内 default 冒烟包当前不产生 Module2 结果，不能单独证明两个模块都迁移成功。

验证后，将脚本、诊断技能调用方和日常命令中的配置路径与产品参数同步切换为 `--config config-galaxy.yaml --product galaxy`。CLI 不会根据文件名或唯一产品条目自动选中 galaxy：不传 `--product` 时仍选择 `default`。已有解析任务继续按原任务查询，不需要为了产品改名重新解析。

如果最后将新主配置改名为 `config.yaml`，调用方的 `--config` 也要同步修改；保持主配置与引用文件的相对位置。需要回退时，在原版本程序中使用备份的原配置和原输出目录；当前程序不能直接运行旧 v1 配置。

## 常见问题

| 现象 | 优先检查 |
| --- | --- |
| 提示必须显式声明 `schema_version: 2` | 是否读取了正确主配置，版本号是否是整数 2 |
| 提示 unknown fields | 旧字段是否仍留在原位置，是否只改了版本号却没搬迁结构 |
| 插件无法导入 | 是否仍使用 `backend.plugins.default.*` 或 `backend.plugins.mechanisms.*` 旧路径 |
| 产品不存在或仍选择 default | `products.galaxy`、`--config`、`--product galaxy` 是否一致 |
| `$include` 加载失败 | 引用是否相对于主配置目录，文件是否存在，正文是否错误地多套了 `products` 一层 |
| Module1 没有条目 | 文件是否被发现；诊断行是否包含 `module_name.upper()`；正则和 journal 过滤条件是否匹配 |
| Module2 没有条目或被跳过 | 样例是否包含 Module2；`identifying_keyword` 和正则是否匹配；Module1 是否启用、成功且有可用结果；依赖是否为 `[module1]` |
| 改了业务模块名后找不到原证据路径 | 新证据目录取决于 `module_name`，查询优先使用机制键 `module1` 或 `module2` |

## 参考依据

- [配置校验](../backend/config_validation.py)：版本、字段结构和旧字段拒绝规则。
- [配置加载与解释](../backend/application/configuration.py)：产品引用路径和生效配置。
- [当前 default 产品模板](../configs/products/default.yaml)：内置插件及配置示例。
- [机制与生命周期校验](../backend/extensions/mechanisms/validation.py)：正则捕获组和 V3 字段约束。
- [产品日志规格](product-log-specification.md)：Module1、Module2 的提取规则、命名含义及样例限制。
- [使用说明](usage.md)：解析、查询和 DFX 命令。
- [ADR-0006](adr/0006-pre-handoff-simplification.md)：移除旧配置兼容层的决策。
