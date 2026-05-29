# logparse 架构图

下面是当前统一解压、机制模块插件化、以及板卡周期内嵌套 CPU 周期输出后的架构。

```mermaid
flowchart TD
    A["CLI<br/>cli.py"] --> B["Pipeline<br/>backend/pipeline.py"]

    B --> C["Decompressor<br/>统一归档解压"]
    C --> C1["外层 archive"]
    C --> C2["内层 zip/tar/tgz/tar.gz"]
    C --> C3["普通 .gz 日志默认保留<br/>parser 流式读取<br/>debug 时可展开"]
    C1 --> D["Expanded Workspace<br/>output/{task_id}/extracted"]
    C2 --> D
    C3 --> D

    D --> E["DirectoryDiscoveryPlugin<br/>只扫描，不解压"]
    E --> E1["default.ScannerPlugin<br/>diag/ + varlog/"]
    E --> E2["compact.ScannerPlugin<br/>boards/ + logs/"]
    E1 --> F["ParseResult skeleton<br/>diagnostic_slots + private_slots"]
    E2 --> F

    F --> G["LogParserPlugin<br/>default.ParserPlugin"]
    G --> G1["TimestampExtractor"]
    G --> G2["ActivePeriodBuilder"]
    G --> H["MechanismModulePlugin Loader"]

    H --> I["Module1Plugin<br/>backend/plugins/mechanisms/module1.py"]
    I --> I1["MechDiagScanner"]
    I --> I2["MechJournalScanner"]
    I --> I3["CycleDetector<br/>板卡周期 + 嵌套 CPU 周期"]
    I --> I4["RoleIdentifier<br/>module1 主控信号"]
    I1 --> J["MechResult"]
    I2 --> J
    I3 --> J
    I4 --> F

    H --> K["Module2Plugin<br/>backend/plugins/mechanisms/module2.py"]
    J --> K
    K --> K1["依赖 module1 周期<br/>按 slot + cpu_id + timestamp 归档"]
    K1 --> J2["MechResult<br/>module2 / other"]

    H --> O["Other Mechanism Plugins<br/>按需扩展"]
    O --> O1["自定义解析逻辑"]
    O1 --> J2

    J --> L["MechOutputWriter<br/>slot/{board_cycle}/[cpu_N/{cpu_cycle}/]"]
    J2 --> L
    F --> M["MetadataGenerator<br/>metadata.json"]
    F --> N["CLI result writer<br/>result.json"]
```

## 职责边界

- `Decompressor`：负责外层和内层归档包的统一解压；普通 `.gz` 日志默认不展开。
- `DirectoryDiscoveryPlugin`：只扫描统一解压后的工作区，发现 slot、诊断日志和 private/journal 日志。
- `LogParserPlugin`：负责产品级解析编排，提取基础时间戳、构建 `ActivePeriod`，并加载机制模块插件。
- `MechanismModulePlugin`：机制模块自己的扩展点。`module1` 自己拥有特殊日志扫描、周期切分和主控角色信号。
- `module2`：诊断日志-only 机制模块，依赖 `module1` 的生命周期结果；CPU 日志优先落到同 slot、同板卡周期下的嵌套 CPU 周期，无法匹配 CPU 周期时进入 `cpu_<id>/unknown/`。
- `MechOutputWriter` / `MetadataGenerator`：负责结构化输出落盘；日志路径为 `slot_<id>/<board_cycle>/<proc>.log` 或 `slot_<id>/<board_cycle>/cpu_<id>/<cpu_cycle>/<proc>.log`。
