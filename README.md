# logparse

logparse 预处理设备诊断日志：安全解压、按产品发现日志、整理生命周期与进程证据，再生成可查询的结构化索引。独立运行时保持确定性，不调用模型。

当前处于所有者批准的移交前精简阶段，见 [ADR-0006](docs/adr/0006-pre-handoff-simplification.md)。开发前阅读 `CLAUDE.md` 和 [开发指南](docs/lan-development-guide.md)；正式移交后只在 LAN 内维护。

分析代码或交给 GLM5.2 阅读时，从 [AI 阅读入口](docs/ai-reading-guide.md) 开始。实际调用链与职责见 [架构](docs/architecture.md)，default/compact 的支持矩阵、日志字段、合成示例和已知限制见 [产品日志规格](docs/product-log-specification.md)。

## 快速开始

使用 Python 3.12 创建 `.venv` 并安装依赖。以下示例使用 POSIX 路径；Windows 将 `.venv/bin/python` 替换为 `.venv\Scripts\python.exe`。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python cli.py check-config -c config.yaml
.venv/bin/python cli.py parse tests/mock_data/diagnostic_information_20260103.zip -c config.yaml --product default
.venv/bin/python cli.py parse tests/mock_data_compact/compact_package_20260103.zip -c config.yaml --product compact
```

`parse` 接受原始压缩包、单个非压缩诊断日志或原始日志目录。已有 `output/<task_id>` 应交给查询/诊断命令，不再次解析。default 和 compact 是两套持续验收的产品适配器；`tests/mock_data*` 为合成冒烟包，普通单元测试优先使用最小 fixture。

## 配置与接口

唯一配置格式为显式 `schema_version: 2`。根 `config.yaml` 引用 `configs/products/` 中的产品文件；`explain-config` 展示最终配置，`check-config` 校验它。旧 v1 配置、`migrate-config`、旧 Python 导入路径和 pre-v1 插件不再支持。

`ParseService` 是唯一完整解析入口；根 `cli.py` 只启动命令层。机制插件使用 `execute(MechanismContext) -> MechanismOutcome`，依赖关系在扫描前验证。

```bash
.venv/bin/python cli.py parse <input_path> -c config.yaml --product default --profile
.venv/bin/python cli.py info <task_id>
.venv/bin/python cli.py list-slots <task_id>
.venv/bin/python cli.py mech-slots <task_id> -m <module>
.venv/bin/python cli.py mech-lifecycles <task_id> -s <slot> -m <module> --show-boundaries
.venv/bin/python cli.py mech-target-logs <task_id> --problem-time <ISO_TIME> --module <module> --slot <slot> --process-name <name> --pid <pid>
.venv/bin/python cli.py mech-logs <task_id> -s <slot> -c <board_cycle_dir> -p <process> --pid <pid> -m <module>
.venv/bin/python cli.py doctor -c config.yaml --json
.venv/bin/python cli.py explain-config -c config.yaml -p default --json
.venv/bin/python cli.py artifact-check output/<task_id> --json
.venv/bin/python cli.py scaffold-extension --kind product --name <name>
.venv/bin/python cli.py scaffold-extension --kind mechanism --name <name>
```

## 生命周期与扫描

Module1 始终使用 `LifecycleSplitterV3`，`lifecycle_split_result.algorithm` 为 `interval_v3`。`lifecycle_split` 仅接受：

```yaml
lifecycle_split:
  process_name_mapping:
    canonical_proc:
      - alias_in_diag
      - alias_in_journal
  reliable_processes:
    - canonical_lifecycle_proc
  multi_instance_processes: []
```

V3 结果包含 `candidate_segments`、`merge_decisions`、`lifecycles`、`journal_evidence`、`issues`、`lifecycle_reliable`。旧开关和算法字段会被拒绝。

```bash
.venv/bin/python cli.py parse <input_path> -c config.yaml --lifecycle-dfx decisions
.venv/bin/python cli.py mech-lifecycles <task_id> -s <slot> -m <module> --show-boundaries --lifecycle-dfx full
```

板级生命周期为顶层，非零 CPU 生命周期嵌套在对应板级周期内；空 CPU 和 CPU0 属于板级。Module2 复用 Module1 生命周期，不独立切分；如 `1/2` 的 frame/slot 值按最后一个非空段匹配 slot。

解压器负责提取与路径安全，scanner 只检查准备好的工作区。普通 `.gz` 日志默认流式读取，显式 `--debug-expand-gz` 才展开；`--keep-workspace` 可保留临时工作区。

default 产品先扫描 `diag/slot_*`，再按 `products.default.discovery.config.loose_diagnostics.file_patterns` 补充散落日志。默认不匹配宽泛的 `*.log`。机制条目按解析内容去重，源路径不参与重复判定；完整规则见 [使用说明](docs/usage.md)。journal 仍以 `varlog/slot_*` 或 `varlog/slot_*_cpu_*` 为归属锚点。

## 产物与诊断

```text
output/<task_id>/
├── parse_manifest.json
├── metadata.json
├── result.json
├── mech_modules/
├── performance.json       # --profile
├── dfx_report.json        # dfx-output
├── dfx_summary.txt        # dfx-output
└── dfx_context/           # 深度 DFX 确实产生窗口时
```

`metadata.json` 记录扫描覆盖，`result.json` 是紧凑查询索引，不含原始文本或逐行 `logs[]`。机制证据保存在 `mech_modules/<module>/slot_<slot>/<board_cycle>/`；CPU 日志继续嵌套于 `cpu_<id>/<cpu_cycle>/`。普通安全进程名保留 `<proc>-<pid>.log`；路径分隔符、Windows 保留名等不安全名称会编码。

```bash
.venv/bin/python cli.py dfx-output output/<task_id>
.venv/bin/python cli.py dfx-output output/<task_id> --deep
```

默认 DFX 读取结构化产物，并读取证据文件计算完整性哈希；这些正文不会进入诊断报告。深度模式才提取诊断上下文，最多 5 个窗口、每个 48 行、共 80 KiB。`dfx_summary.txt` 只含单行 `ERROR_CODE: 中文结论`。模型只读取指定目标与限量上下文，详见 [DFX 边界](docs/lan-dfx-operating-model.md)。

诊断 skill 的正文保存在 `.agents/skills/`，Claude 从 `.claude/skills/` 的包装入口加载。仓内 wiki 生成器及 `result.zip` 打包工具属于诊断集成能力，独立于预处理运行时。

## 验证

```bash
.venv/bin/python scripts/verify_delivery.py
.venv/bin/python scripts/rule_preflight.py --changed
.venv/bin/python scripts/change_gate.py --changed
.venv/bin/python scripts/change_gate.py --changed --enforce \
  --change-record governance/changes/<change-id>.yaml
```

交付检查包含 Ruff、compileall、配置、完整测试及行/分支/核心覆盖率门槛。性能对比用 `scripts/compare_parse_outputs.py` 核对业务产物和扫描计数，再单独比较 `performance.json`；详见 [性能验收](docs/large-package-performance.md)。

持续维护的文档：[AI 阅读入口](docs/ai-reading-guide.md)、[产品日志规格](docs/product-log-specification.md)、[使用说明](docs/usage.md)、[架构](docs/architecture.md)、[开发指南](docs/lan-development-guide.md)、[DFX 边界](docs/lan-dfx-operating-model.md)、[生命周期 DFX](docs/lifecycle-dfx-guide.md)、[产品接入](docs/product-onboarding-agent-guide.md)、[ADR](docs/adr/)。历史实施方案已从工作树移除，历史依据保留在 Git、ADR 和 `governance/changes/`。
