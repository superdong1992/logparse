---
name: wiki-to-diagnosis-skill
description: 把 Markdown 定位 wiki 生成为仓内 diagnose-* 诊断技能，统一调用 logparse-diagnose 获取目标证据，并生成扁平 result.zip。
---

# 从 wiki 生成诊断 skill

生成前需要 `wiki`（Markdown 路径或内容）和具体业务 `module_name`，例如 `EXAMPLE`、`MODULE2`。缺少这两项、目标角色或诊断步骤时先询问，不把内部插件键 `module1` 猜成业务模块名。

先读 `references/generated-skill-contract.md`，按其中唯一模板生成。wiki 信息不完整时参考 `references/wiki-template.md`，不要自行补造判断规则。

## 提取与生成

从 wiki 提取中文标题、问题范围、目标角色及必需性、诊断步骤、判断规则、结果要求；选择 `diagnose-<english-topic-slug>` 名称，优先沿用合法的 wiki 名称。模块名固定写入 frontmatter；输入路径、配置文件、输出目录、时间和各进程 slot/process/PID 留待运行时收集。

- 正文保存于 `.agents/skills/<skill_name>/SKILL.md`，较长的 wiki 知识放在同目录 `references/`。
- Claude 包装入口保存于 `.claude/skills/<skill_name>/SKILL.md`，只加载规范正文；复制相同 frontmatter，不复制流程。
- 不生成 README、安装文档、changelog 或 `agents/openai.yaml`。
- 保留 `logparse-diagnose` 的结构化 `target_logs` 交接，只读返回的 `log_path`，不自行选周期、拼路径或替代缺失日志。
- `result.zip` 只包含中文 `result.txt` 与实际使用的目标日志；调用仓内 `pack_result_zip.py`，不把打包并入预处理运行时。

## 验证与交付

使用 `.venv` 中的 Python 3.12。原生 Windows 把下面的 `.venv/bin/python` 替换为 `.venv\Scripts\python.exe`。

```bash
.venv/bin/python -X utf8 .claude/skills/wiki-to-diagnosis-skill/scripts/validate_generated_skill.py .agents/skills/<skill_name> --wrapper .claude/skills/<skill_name>
```

若有样例 zip，追加 `--result-zip <result.zip路径>`。修复所有失败，再报告正文和入口路径、模块名、假设及真实验证结果。诊断证据只能留在批准的环境内。
