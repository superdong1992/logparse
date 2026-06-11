from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import zipfile


NAME_PATTERN = re.compile(r"^diagnose-[a-z0-9-]{1,54}$")


class ValidationResult:
    def __init__(self, errors: list[str] | None = None):
        self.errors = errors or []

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, errors: list[str]) -> None:
        self.errors.extend(errors)


def _read_skill(skill_dir: Path) -> tuple[dict[str, str], str, list[str]]:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return {}, "", [f"missing SKILL.md: {skill_md}"]

    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, text, ["SKILL.md must start with YAML frontmatter"]

    lines = text.splitlines()
    try:
        end = lines[1:].index("---") + 1
    except ValueError:
        return {}, text, ["SKILL.md frontmatter must end with ---"]

    frontmatter: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip("'\"")
    body = "\n".join(lines[end + 1 :])
    return frontmatter, body, []


def _contains_all(text: str, phrases: list[str]) -> list[str]:
    return [phrase for phrase in phrases if phrase not in text]


def validate_skill_dir(skill_dir: str | Path) -> ValidationResult:
    skill_dir = Path(skill_dir)
    frontmatter, body, errors = _read_skill(skill_dir)

    name = frontmatter.get("name", "")
    description = frontmatter.get("description", "")
    effort = frontmatter.get("effort", "")
    module_name = frontmatter.get("module_name", "")

    if not NAME_PATTERN.fullmatch(name):
        errors.append("frontmatter name must match diagnose-<english-topic-slug>")
    if name and skill_dir.name != name:
        errors.append("skill directory name must match frontmatter name")
    if effort != "medium":
        errors.append("frontmatter must contain effort: medium")
    if not module_name:
        errors.append("frontmatter must contain module_name")
    if "module" in frontmatter:
        errors.append("frontmatter must not contain legacy module; use module_name")

    for phrase in ["logparse-diagnose", "target_logs", "result.zip"]:
        if phrase not in description:
            errors.append(f"frontmatter description must mention {phrase}")

    if (skill_dir / "agents" / "openai.yaml").exists():
        errors.append("generated Claude skills must not contain agents/openai.yaml")

    required_sections = [
        "## 运行时输入",
        "## 先调用 logparse-diagnose skill",
        "## 证据收敛约束",
        "## Wiki 定位步骤",
        "## Result.zip 交付物",
    ]
    missing_sections = _contains_all(body, required_sections)
    errors.extend(f"missing required section: {section}" for section in missing_sections)

    required_phrases = [
        "input_path",
        "config_path",
        "output_dir",
        "problem_time",
        "原始日志输入",
        "单个非压缩诊断日志",
        "原始日志目录",
        "固定 module_name",
        "frontmatter",
        "module_name + slot + process_name",
        "运行时不再向用户询问模块",
        "组装 targets[]",
        "targets[].module",
        "logparse-diagnose",
        "Claude skill",
        "-c <config_path>",
        "具体 YAML 文件名",
        "不要只传配置目录",
        "不要省略配置文件路径",
        "cli.py mech-target-logs",
        "target_logs[*].log_path",
        "不要遍历",
        "output/",
        "不要重新选择 lifecycle/cycle",
        "不要重新拼接日志路径",
        "不要用相关日志替代缺失的目标日志",
        "当前证据不足以确认根因",
        "result.zip",
        "result.txt",
        "安全扁平文件名",
        "Windows 非法字符",
        "cpu_<cpu_id>",
        "pack_result_zip.py",
    ]
    missing_phrases = _contains_all(body, required_phrases)
    errors.extend(f"missing required contract phrase: {phrase}" for phrase in missing_phrases)

    return ValidationResult(errors)


def validate_result_zip(zip_path: str | Path) -> ValidationResult:
    zip_path = Path(zip_path)
    errors: list[str] = []
    if not zip_path.exists():
        return ValidationResult([f"missing result.zip: {zip_path}"])

    with zipfile.ZipFile(zip_path) as zf:
        names = [info.filename for info in zf.infolist()]

    if "result.txt" not in names:
        errors.append("result.zip must contain result.txt at the root")
    for name in names:
        normalized = name.replace("\\", "/")
        if normalized.endswith("/"):
            errors.append(f"result.zip must be flat; directory entry found: {name}")
        elif "/" in normalized:
            errors.append(f"result.zip must be flat; nested file found: {name}")
        if Path(normalized).name == "manifest.txt":
            errors.append("result.zip must not contain manifest.txt")
    return ValidationResult(errors)


def _print_result(label: str, result: ValidationResult) -> None:
    if result.ok:
        print(f"{label}: OK")
        return
    print(f"{label}: FAILED")
    for error in result.errors:
        print(f"- {error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate generated logparse diagnosis skills.")
    parser.add_argument("skill_dir", type=Path, help="Generated .claude/skills/diagnose-* directory")
    parser.add_argument("--result-zip", type=Path, help="Optional result.zip artifact to validate")
    args = parser.parse_args(argv)

    skill_result = validate_skill_dir(args.skill_dir)
    _print_result("skill", skill_result)

    zip_result = ValidationResult()
    if args.result_zip:
        zip_result = validate_result_zip(args.result_zip)
        _print_result("result.zip", zip_result)

    return 0 if skill_result.ok and zip_result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
