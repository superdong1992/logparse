from __future__ import annotations

import importlib.util
from pathlib import Path
import textwrap
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = (
    ROOT
    / ".claude"
    / "skills"
    / "wiki-to-diagnosis-skill"
    / "scripts"
    / "validate_generated_skill.py"
)
PACKER_PATH = (
    ROOT
    / ".claude"
    / "skills"
    / "wiki-to-diagnosis-skill"
    / "scripts"
    / "pack_result_zip.py"
)


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_generated_skill", VALIDATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_packer():
    spec = importlib.util.spec_from_file_location("pack_result_zip", PACKER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_skill(tmp_path: Path, body: str) -> Path:
    skill_dir = tmp_path / "diagnose-link-timeout"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")
    return skill_dir


VALID_SKILL = """\
---
name: diagnose-link-timeout
description: 用于链路超时问题定位；必须先调用 logparse-diagnose skill 获取 target_logs，再只基于 target_logs[*].log_path 指定日志按 wiki 规则分析并生成扁平 result.zip。
effort: medium
---

# 链路超时问题定位

中文显示名：链路超时问题定位

## 运行时输入

先收集全局输入 `input_path` 和 `problem_time`。

再按目标进程记录收集：

| 标签 | module | slot | process_name | pid |
| --- | --- | --- | --- | --- |
| client | 用户提供 | 用户提供 | 用户提供 | 可选 |
| server | 用户提供 | 用户提供 | 用户提供 | 可选 |

每组目标进程必须保持为同一条 `module + slot + process_name + 可选 pid` 记录。

## 先调用 logparse-diagnose skill

`logparse-diagnose` 也是本项目里的一个 Claude skill，路径是 `.claude/skills/logparse-diagnose/SKILL.md`。
不要把 `logparse-diagnose` 当成 shell 命令、Python 模块或普通说明文字。必须先调用/加载这个 skill。
对每个 anchor 调用 `cli.py mech-target-logs`，返回结构化 `target_logs` 清单。
当前定位 skill 只分析 `target_logs[*].log_path` 指定的模块日志。

## 证据收敛约束

禁止发散分析。只允许基于 `logparse-diagnose` 返回的 `target_logs[*].log_path` 目标模块日志和本 wiki 的定位规则给结论。
不要遍历 `output/`，不要重新选择 lifecycle/cycle，不要重新拼接日志路径，不要用相关日志替代缺失的目标日志。
没有日志证据时，定位结论必须写“当前证据不足以确认根因”。

## Wiki 定位步骤

1. 在 client 日志中查找问题时间附近的请求发送记录。
2. 在 server 日志中查找相同 request_id 的接收记录。

## 判断规则

- 只有两端日志都存在同一个 request_id，才能建立跨进程关联。

## Result.zip 交付物

生成扁平 `result.zip`，根目录只包含 `result.txt` 和本次实际使用的目标进程日志。
不要创建 `logs/`，不要创建 `manifest.txt`，也不要创建任何子目录。
每份日志必须来自 `target_logs[*].log_path`。
打包时使用 `python3.12 scripts/pack_result_zip.py <work_dir> <result_zip>`。
"""


def test_generated_skill_contract_accepts_compliant_skill(tmp_path):
    validator = _load_validator()
    skill_dir = _write_skill(tmp_path, VALID_SKILL)

    result = validator.validate_skill_dir(skill_dir)

    assert result.ok, result.errors


def test_generated_skill_contract_rejects_missing_logparse_section(tmp_path):
    validator = _load_validator()
    skill_dir = _write_skill(
        tmp_path,
        VALID_SKILL.replace("## 先调用 logparse-diagnose skill", "## 日志获取阶段"),
    )

    result = validator.validate_skill_dir(skill_dir)

    assert not result.ok
    assert any("先调用 logparse-diagnose skill" in error for error in result.errors)


def test_generated_skill_contract_rejects_non_flat_result_zip(tmp_path):
    validator = _load_validator()
    zip_path = tmp_path / "result.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("result.txt", "定位结论\n当前证据不足以确认根因。\n")
        zf.writestr("logs/client.log", "client log")

    result = validator.validate_result_zip(zip_path)

    assert not result.ok
    assert any("flat" in error.lower() or "扁平" in error for error in result.errors)


def test_pack_result_zip_creates_flat_zip(tmp_path):
    packer = _load_packer()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "result.txt").write_text("定位结论\n当前证据不足以确认根因。\n", encoding="utf-8")
    (work_dir / "client__module1__slot_1__proc-123.log").write_text("client log", encoding="utf-8")
    zip_path = tmp_path / "result.zip"

    packer.pack_result_zip(work_dir, zip_path)

    with zipfile.ZipFile(zip_path) as zf:
        assert zf.namelist() == ["client__module1__slot_1__proc-123.log", "result.txt"]


def test_pack_result_zip_rejects_subdirectories(tmp_path):
    packer = _load_packer()
    work_dir = tmp_path / "work"
    (work_dir / "logs").mkdir(parents=True)
    (work_dir / "result.txt").write_text("定位结论\n当前证据不足以确认根因。\n", encoding="utf-8")

    try:
        packer.pack_result_zip(work_dir, tmp_path / "result.zip")
    except ValueError as exc:
        assert "flat" in str(exc)
    else:
        raise AssertionError("expected subdirectory to be rejected")
