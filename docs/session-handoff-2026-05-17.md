---
name: Session Handoff 2026-05-17
description: Complete state of logparse project after May 17 session — changes made, architecture designed, work remaining for next session
type: project
---

# 会话交接：2026-05-17

## 项目概述

日志解析维护工具（logparse），预处理产品设备日志压缩包。是日志定位流程中的一环，产出 .log 文件（给人）和结构化数据（给 AI agent）。

### 关键约束

1. **内网运行**：无法访问外部大模型 API，工具必须完全自包含
2. **日志无法外传**：必须有 mock 数据生成能力（当前不存在）
3. **本地低性能开源模型**：AI agent 输出应针对小模型优化
4. **多产品支持**：不同产品的日志包内部结构完全不同（不只是参数差异）

## 当前代码状态

### 已完成的变更

1. **安全修复**（CRITICAL 5 + IMPORTANT 8 + MINOR 7）
   - decompressor.py: _extract_zip 安全检查死代码修复（逐文件解压）、_extract_tar 大小检查合并、gzip 截断残留删除
   - scanner.py: 目录发现优先走 Decompressor
   - 静默 except:pass 全部替换为 logging.warning
   - models.py: datetime.now → datetime.now(timezone.utc)
   - config.py: get_config() 加 assert 防 None
   - cli.py: 移除硬编码 debug_filter = "dhcp"

2. **命名清理**
   - AAA → Mech/MechModule: 文件 `aaa_parser.py`→`mech_parser.py`，6 个类重命名，CLI 命令 `aaa-slots`→`mech-slots`
   - docue/DOCUE → EXAMPLE（config.yaml 默认值）
   - cpdt_journal.log → journal.log
   - _glob_to_regex → glob_to_regex（去下划线，被 cli.py 导入）
   - _find_rollback_boundary → _find_seq_wrap_boundary
   - _step → _safe_step
   - 缩写展开: pname→proc_name, jnl→journal_count, kw→keyword, cfg_key→module_key
   - JournalLogEntry → JournalLogFile
   - SlotInfo.type → SlotInfo.board_type（避免遮蔽 Python 内置 type）
   - 删除 SlotInfo.private_logs 死字段
   - CLAUDE.md 同步更新

3. **移除功能**
   - 倒换检测（SwitchoverEvent）已从 identifier.py、models.py、metadata.py、cli.py、frontend 全部删除
   - 前端目录（frontend/index.html, app.js, style.css）已删除
   - Web API（backend/main.py）已删除
   - requirements.txt 从 7 个依赖减到 3 个：pyyaml, pydantic, click

4. **插件框架 Phase 1 完成**（新建文件）
   - `backend/utils.py` — current pure helpers: glob matching, slot parsing, dump-time parsing, journal sequence parsing, compression checks, and safe log path naming
   - `backend/plugins/__init__.py` — 空
   - `backend/plugins/base.py` — DirectoryDiscoveryPlugin (ABC): discover(root)→(slots, private_slots)；LogParserPlugin (ABC): parse(result)→result + write_output(mech_result, dir)→path
   - `backend/plugins/loader.py` — instantiate_plugin(class_path, base, config, **extra)
   - `backend/plugins/default/__init__.py` — 空
   - `backend/pipeline.py` — Pipeline 类：6 步通用管道（解压→发现→内层解压→解析→落盘→元数据），按 product 名加载插件对
   - `backend/decompressor.py` — __init__ 改为接受 config_loader 或 compressed_extensions 列表

### 当前文件清单

```
logparse/
├── cli.py
├── config.yaml
├── requirements.txt          # pyyaml, pydantic, click
├── CLAUDE.md                 # 已更新
├── .gitignore                # 含 .superpowers/
├── backend/
│   ├── __init__.py
│   ├── models.py             # 数据模型（插件间契约，不动）
│   ├── config.py             # ConfigLoader（旧管道依赖）
│   ├── decompressor.py       # 已更新：可配置初始化
│   ├── scanner.py            # 旧管道（保留）
│   ├── mech_parser.py        # 旧管道（保留）
│   ├── log_parser.py         # 旧管道（保留）
│   ├── identifier.py         # 旧管道（保留）
│   ├── metadata.py           # MetadataGenerator
│   ├── utils.py              # 纯函数工具
│   ├── pipeline.py           # Pipeline 编排器
│   └── plugins/
│       ├── __init__.py
│       ├── base.py           # ABC 接口
│       ├── loader.py         # 动态加载
│       └── default/
│           └── __init__.py   # 待放 ScannerPlugin + ParserPlugin
└── .superpowers/             # brainstorming 视觉辅助缓存
```

## 架构设计（已讨论确定）

### 产品差异 5 层

| 层级 | 差异点 | 处理方式 |
|------|--------|---------|
| 1. 输入转换 | 二进制→文本 | 插件可选（后续） |
| 2. 目录结构 | 内部布局完全不同 | DirectoryDiscoveryPlugin |
| 3. 日志格式 | 字段、分隔符、编码 | LogParserPlugin |
| 4. 机制模块识别 | 同一模块不同产品关键字不同 | LogParserPlugin 的 mechanism_modules 配置 |
| 5. 运行流程解析 | 主备倒换等 | 移到产品 skill（不在本项目） |

### 插件架构

- YAML + Python 插件模型（B 方案）
- DirectoryDiscoveryPlugin 和 LogParserPlugin 分离，可自由组合
- 插件通过 YAML 中 `plugin: "backend.plugins.X.Y.ClassName"` 动态加载
- 中间数据模型（SlotInfo, PrivateSlotInfo, ParseResult, MechResult）是插件间契约，不动
- Inner Extraction 作为通用管道步骤，从 Scanner 剥离
- 输出放在 LogParserPlugin（输出格式跟解析紧密耦合）

### 配置结构（待实施）

```yaml
pipeline:
  recursive_extraction: true
  inner_extraction: true
  generate_metadata: true
  output_base_dir: "./output"

products:
  default:
    discovery:
      plugin: "backend.plugins.default.scanner.ScannerPlugin"
      config: { diagnostic_dir: "diag", ... }
    log_parser:
      plugin: "backend.plugins.default.parser.ParserPlugin"
      config: { timestamp_regex: "..", mechanism_modules: {...} }
```

旧平铺格式保留向后兼容。

## 待做事项

### Phase 2: 创建默认插件（最优先）

- [ ] `backend/plugins/default/scanner.py` — ScannerPlugin(DirectoryDiscoveryPlugin)
  - 迁移当前 Scanner.scan_diag() + scan_private() 逻辑
  - 用 config: dict 替代 ConfigLoader
  - 内部编译 regex 模式（用 utils.py 函数）
- [ ] `backend/plugins/default/parser.py` — ParserPlugin(LogParserPlugin)
  - 合并当前 MechParser.parse_all() + LogParser.build_all_periods() + Identifier.analyze()
  - parse() 原地修改 ParseResult
  - write_output() 复用 MechParser.write_output() 逻辑

### Phase 3: 集成 CLI

- [ ] config.yaml 新增 `products:` 段 + 旧格式保留
- [ ] cli.py parse 加 `--product` flag
  - 有 --product → 走 Pipeline.run()
  - 无 --product → 走旧流程（向后兼容）
- [ ] 测试：默认插件输出与旧管道一致

### Phase 4: 验证 + 清理

- [ ] 创建 mock 数据生成脚本（tests/generate_mock_data.py）
- [ ] 旧模块加 deprecation warning
- [ ] 第二个产品 mock 插件示例

### 后续（未排期）

- AI agent 消费层设计（针对低性能模型优化）
- 产品 skill 开发（主备倒换检测等）
- 新前端实现
- 新 Web API 实现

## 关键设计决策记录

- **前端和 Web API 一起移除**：用户明确要求"Web 相关的功能都先移除，后面统一实现"
- **倒换检测移出本项目**：角色判定（ACTIVE/STANDBY/UNKNOWN）保留在核心工具，倒换事件推理移到产品 skill
- **目录发现和日志解析分离**：用户选 B，可自由组合
- **不搞纯 YAML 配置**：用户说"A 不行，产品有自己的复杂逻辑"，所以用 YAML + Python 插件
