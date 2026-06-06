# 大包性能与隔离环境 DFX

## 背景约束

本项目常运行在隔离环境中，真实日志压缩包通常不能提供给 AI agent。性能优化和问题定位必须假设 agent 看不到真实 2GB 级日志包，只能依赖人类在隔离环境运行命令后转述结构化证据。

典型大包特征：

- 外层压缩包约 2GB。
- slot 约 10 个，CPU 接近 100 个。
- 双主控，但不能按主控 slot 裁剪，因为 module1 日志可能分布在其他 slot/cpu 下。
- 双主控诊断日志文件很多，约 200-300 个，每个几万行。

## Profile 命令

```bash
python cli.py parse diagnostic_information.zip \
  --output output_optimized \
  --product default \
  --profile
```

输出：

```text
output_optimized/{task_id}/performance.json
```

`performance.json` 是性能验收主入口，stdout 只作为一屏摘要使用。

## DFX 安全策略

- 性能 DFX 只记录耗时、文件数、行数、命中数、worker 配置和阶段树。
- 禁止记录原始日志行、raw 片段、敏感 context。
- module2 unknown 的 raw 级定位信息不进入性能 DFX。
- 现有代码中的旧 `LOGPARSE_PERF` 日志暂不作为验收入口；新验收以 `performance.json` 为准。
- `parser.timestamps`、`module1.diag_scan`、`module2.diag_scan` 的重复扫描视角合并到 `diagnostic_scan.shared`。

## 等价性对比

优化前后分别运行：

```bash
python cli.py parse diagnostic_information.zip --profile --output output_baseline
python cli.py parse diagnostic_information.zip --profile --output output_optimized
python scripts/compare_parse_outputs.py output_baseline output_optimized
```

对比脚本会忽略 `performance.json` 与 `extracted/`，比较业务输出文件集合与内容 hash。任何 `result.json`、`metadata.json` 或 `mech_modules/` 差异都应视为回归。

## 性能验收

目标：约 2GB 大包端到端处理在 240 秒内完成。

未达标时，先查看：

- `performance.json.total_seconds`
- 慢阶段 top N
- `diagnostic_scan.shared` 的 files、lines、timestamps、module1/module2 entries
- `pipeline.extract` 的 files 与 worker 配置

不要通过裁剪 slot/cpu 或跳过非主控 slot 来换取性能。
