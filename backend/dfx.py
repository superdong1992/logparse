"""Deterministic DFX report generation for parsed logparse output."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.query import ResultQueryService
from backend.utils import safe_log_filename, safe_path_segment

SUMMARY_MAX_CHARS = 120
DEEP_MAX_WINDOWS = 5
DEEP_WINDOW_LINES = 48
DEEP_TOTAL_BYTES = 80 * 1024


@dataclass(frozen=True)
class DfxIssue:
    code: str
    message: str
    severity: str = "error"
    detail: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


def build_dfx_output(
    output_task_dir: Path,
    *,
    targets_json: str | None = None,
    problem_time: str | None = None,
    deep: bool = False,
    summary_path: Path | None = None,
) -> dict[str, Any]:
    """Build deterministic DFX files under one parsed output task directory."""
    task_dir = Path(output_task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)
    context_dir = task_dir / "dfx_context"
    if context_dir.exists():
        shutil.rmtree(context_dir)
    context_dir.mkdir(parents=True, exist_ok=True)

    targets, target_problem_time = _parse_targets(targets_json)
    target_problem_time = problem_time or target_problem_time

    report = {
        "schema_version": 1,
        "task_id": task_dir.name,
        "output_task_dir": str(task_dir.resolve()),
        "mode": "deep" if deep else "structure",
        "summary_max_chars": SUMMARY_MAX_CHARS,
        "inputs": {
            "problem_time": target_problem_time,
            "target_count": len(targets),
        },
        "files": _file_status(task_dir),
        "issues": [],
        "metadata": {},
        "result": {},
        "targets": [],
        "deep_context": {
            "enabled": deep,
            "files": [],
            "max_windows": DEEP_MAX_WINDOWS,
            "window_lines": DEEP_WINDOW_LINES,
            "total_bytes_budget": DEEP_TOTAL_BYTES,
        },
    }

    issues: list[DfxIssue] = []
    metadata = _read_json(task_dir / "metadata.json")
    result = _read_json(task_dir / "result.json")

    if metadata is None:
        issues.append(DfxIssue("LP_METADATA_MISSING", "metadata.json 不存在，无法确认扫描覆盖范围。"))
    else:
        errors = metadata.get("errors") or []
        report["metadata"] = {
            "diagnostic_slot_count": len(metadata.get("diagnostic_slots") or []),
            "private_slot_count": len(metadata.get("private_slots") or []),
            "error_count": len(errors),
            "errors": _safe_json_preview(errors, limit=5),
        }

    if result is None:
        issues.append(DfxIssue("LP_RESULT_MISSING", "result.json 不存在，解析输出不可用于定位。"))
    else:
        mech_results = result.get("mech_results") or []
        report["result"] = {
            "mech_result_count": len(mech_results),
            "modules": [_module_summary(item) for item in mech_results],
            "v3_ready": _v3_ready(mech_results),
            "missing_log_examples": _missing_log_examples(task_dir, mech_results),
        }
        if not mech_results:
            issues.append(DfxIssue("LP_RESULT_MISSING", "result.json 中没有机制模块输出。"))
        if not report["result"]["v3_ready"]["ok"]:
            issues.append(DfxIssue("LP_V3_MISSING", "未发现可用的 lifecycle_split V3 输出。"))
        if report["result"]["missing_log_examples"]:
            issues.append(
                DfxIssue(
                    "LP_TARGET_LOG_MISSING",
                    "result.json 中存在进程摘要，但对应 mech_modules 日志文件缺失。",
                    detail={"examples": report["result"]["missing_log_examples"][:3]},
                )
            )

    if targets and target_problem_time:
        target_items, target_issues = _resolve_targets(
            task_dir,
            targets,
            target_problem_time,
            deep=deep,
            context_dir=context_dir,
            report=report,
        )
        report["targets"] = target_items
        issues.extend(target_issues)
    elif targets and not target_problem_time:
        issues.append(DfxIssue("LP_TARGET_TIME_MISSING", "target 级 DFX 缺少 problem_time，只能做结构检查。"))
        report["targets"] = [{"anchor": _target_anchor(target), "status": "skipped"} for target in targets]

    if deep and targets and not report["deep_context"]["files"]:
        issues.append(DfxIssue("LP_DEEP_WINDOW_EMPTY", "deep DFX 未生成任何目标日志窗口。"))

    if not issues:
        issues.append(DfxIssue("LP_DFX_OK", "output 结构检查通过，未发现确定性工具错误。", severity="info"))

    report["issues"] = [issue.to_dict() for issue in issues]
    summary = _summary_for_issue(_primary_issue(issues))
    report["summary"] = summary

    (task_dir / "dfx_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (task_dir / "dfx_summary.txt").write_text(summary + "\n", encoding="utf-8")
    if summary_path is not None:
        Path(summary_path).parent.mkdir(parents=True, exist_ok=True)
        Path(summary_path).write_text(summary + "\n", encoding="utf-8")
    return report


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _file_status(task_dir: Path) -> dict[str, Any]:
    mech_dir = task_dir / "mech_modules"
    return {
        "result_json": (task_dir / "result.json").exists(),
        "metadata_json": (task_dir / "metadata.json").exists(),
        "performance_json": (task_dir / "performance.json").exists(),
        "mech_modules_dir": mech_dir.exists(),
        "mech_modules_file_count": (
            sum(1 for item in mech_dir.rglob("*") if item.is_file()) if mech_dir.exists() else 0
        ),
    }


def _safe_json_preview(value: Any, *, limit: int) -> Any:
    if isinstance(value, list):
        return value[:limit]
    return value


def _module_summary(module: dict[str, Any]) -> dict[str, Any]:
    slots = module.get("slots") or []
    process_count = 0
    for slot in slots:
        for board in slot.get("board_cycles") or []:
            process_count += len(board.get("processes") or [])
            for cpu in board.get("cpu_cycles") or []:
                process_count += len(cpu.get("processes") or [])
    return {
        "module_key": module.get("module_key") or "",
        "module_name": module.get("module_name") or "",
        "slot_count": len(slots),
        "process_count": process_count,
        "diag_entry_count": module.get("diag_entry_count"),
        "journal_entry_count": module.get("journal_entry_count"),
    }


def _v3_ready(mech_results: list[dict[str, Any]]) -> dict[str, Any]:
    slots_checked = 0
    ready_slots = []
    missing_slots = []
    for module in mech_results:
        for slot in module.get("slots") or []:
            slots_checked += 1
            split = slot.get("lifecycle_split_result")
            item = {
                "module_key": module.get("module_key") or "",
                "module_name": module.get("module_name") or "",
                "slot": slot.get("slot_id") or "",
            }
            if isinstance(split, dict) and split.get("algorithm") == "interval_v3":
                ready_slots.append(item)
            else:
                missing_slots.append(item)
    return {
        "ok": bool(ready_slots) or slots_checked == 0,
        "slots_checked": slots_checked,
        "ready_slots": ready_slots[:10],
        "missing_slots": missing_slots[:10],
    }


def _missing_log_examples(task_dir: Path, mech_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for module in mech_results:
        module_name = module.get("module_name") or ""
        for slot in module.get("slots") or []:
            slot_id = str(slot.get("slot_id") or "")
            for board in slot.get("board_cycles") or []:
                board_name = str(board.get("dir_name") or "")
                for process in board.get("processes") or []:
                    path = _expected_log_path(task_dir, module_name, slot_id, board_name, process)
                    if not path.exists() and not _direct_cpu_log_exists(path.parent, process):
                        examples.append(_missing_log_example(module, slot_id, board_name, process, path))
                        if len(examples) >= 10:
                            return examples
                for cpu in board.get("cpu_cycles") or []:
                    for process in cpu.get("processes") or []:
                        path = _expected_log_path(
                            task_dir,
                            module_name,
                            slot_id,
                            board_name,
                            process,
                            cpu_id=str(cpu.get("cpu_id") or ""),
                            cpu_cycle=str(cpu.get("dir_name") or ""),
                        )
                        if not path.exists():
                            examples.append(_missing_log_example(module, slot_id, board_name, process, path))
                            if len(examples) >= 10:
                                return examples
    return examples


def _expected_log_path(
    task_dir: Path,
    module_name: str,
    slot_id: str,
    board_cycle: str,
    process: dict[str, Any],
    *,
    cpu_id: str | None = None,
    cpu_cycle: str | None = None,
) -> Path:
    path = (
        task_dir
        / "mech_modules"
        / safe_path_segment(module_name)
        / f"slot_{safe_path_segment(slot_id)}"
        / safe_path_segment(board_cycle)
    )
    if cpu_id:
        path = path / f"cpu_{safe_path_segment(cpu_id)}"
    if cpu_id and cpu_cycle:
        path = path / safe_path_segment(cpu_cycle)
    return path / safe_log_filename(process.get("process_name") or "", process.get("pid") or "")


def _direct_cpu_log_exists(base: Path, process: dict[str, Any]) -> bool:
    proc_file = safe_log_filename(process.get("process_name") or "", process.get("pid") or "")
    return any(
        item.is_file() and item.name.lower() == proc_file.lower()
        for item in base.glob("cpu_*/*")
    )


def _missing_log_example(
    module: dict[str, Any],
    slot_id: str,
    board_cycle: str,
    process: dict[str, Any],
    path: Path,
) -> dict[str, Any]:
    return {
        "module_key": module.get("module_key") or "",
        "module_name": module.get("module_name") or "",
        "slot": slot_id,
        "board_cycle": board_cycle,
        "process_name": process.get("process_name") or "",
        "pid": process.get("pid") or "",
        "expected_path": str(path),
    }


def _parse_targets(raw: str | None) -> tuple[list[dict[str, Any]], str | None]:
    if not raw:
        return [], None
    payload = json.loads(raw)
    if isinstance(payload, dict):
        targets = payload.get("targets") or []
        problem_time = payload.get("problem_time")
        return [dict(item) for item in targets], None if problem_time is None else str(problem_time)
    if isinstance(payload, list):
        return [dict(item) for item in payload], None
    raise ValueError("targets-json must be a list or an object with targets")


def _resolve_targets(
    task_dir: Path,
    targets: list[dict[str, Any]],
    problem_time: str,
    *,
    deep: bool,
    context_dir: Path,
    report: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[DfxIssue]]:
    svc = ResultQueryService(task_dir.parent)
    issues: list[DfxIssue] = []
    target_items: list[dict[str, Any]] = []
    windows_written = 0
    bytes_written = 0
    for index, target in enumerate(targets, start=1):
        anchor = _target_anchor(target)
        payload = svc.resolve_target_logs(
            task_dir.name,
            problem_time=problem_time,
            module=anchor["module"],
            slot=anchor["slot"],
            process_name=anchor["process_name"],
            pid=anchor.get("pid"),
            label=anchor.get("label"),
            explain=True,
        )
        target_log = payload["target_logs"][0]
        code = target_log.get("error_code")
        item = {
            "anchor": anchor,
            "target_log": target_log,
            "selection_diagnostics": payload.get("selection_diagnostics", {}),
        }
        if code and code != "LP_TARGET_OK":
            issues.append(DfxIssue(str(code), _target_issue_message(code, anchor)))
        if deep and target_log.get("log_path") and windows_written < DEEP_MAX_WINDOWS:
            path = Path(str(target_log["log_path"]))
            written = _write_deep_window(context_dir, index, path, bytes_budget=DEEP_TOTAL_BYTES - bytes_written)
            if written:
                windows_written += 1
                bytes_written += written["size_bytes"]
                report["deep_context"]["files"].append(written)
                item["deep_window"] = written
        target_items.append(item)
    return target_items, issues


def _target_anchor(target: dict[str, Any]) -> dict[str, Any]:
    anchor = {
        "label": str(target.get("label") or target.get("process_name") or "target"),
        "module": str(target.get("module") or target.get("module_name") or ""),
        "slot": str(target.get("slot") or ""),
        "process_name": str(target.get("process_name") or ""),
    }
    if target.get("pid") not in {None, ""}:
        anchor["pid"] = str(target.get("pid"))
    return anchor


def _target_issue_message(code: str, anchor: dict[str, Any]) -> str:
    subject = (
        f"module={anchor.get('module')} slot={anchor.get('slot')} "
        f"process={anchor.get('process_name')}"
    )
    if anchor.get("pid"):
        subject += f" pid={anchor.get('pid')}"
    messages = {
        "LP_MODULE_MISSING": f"{subject} 所属模块未出现在 result.json。",
        "LP_SLOT_MISSING": f"{subject} 所属 slot 未出现在模块输出。",
        "LP_TARGET_MISSING": f"{subject} 未命中目标进程，优先核对 PID、问题时间或解析规则。",
        "LP_TARGET_AMBIGUOUS": f"{subject} 命中多个候选，需补充 PID 或更准确的问题时间。",
        "LP_TARGET_LOG_MISSING": f"{subject} 已命中但日志文件不存在，优先检查 mech_modules 写出路径。",
    }
    return messages.get(code, f"{subject} target resolution 返回 {code}。")


def _write_deep_window(
    context_dir: Path,
    index: int,
    log_path: Path,
    *,
    bytes_budget: int,
) -> dict[str, Any] | None:
    if bytes_budget <= 0 or not log_path.exists():
        return None
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return None
    half = DEEP_WINDOW_LINES // 2
    if len(lines) <= DEEP_WINDOW_LINES:
        window = lines
        truncated = False
    else:
        window = [*lines[:half], "...<truncated>...", *lines[-half:]]
        truncated = True
    content = "\n".join(window) + "\n"
    data = content.encode("utf-8")[:bytes_budget]
    if not data:
        return None
    target = context_dir / f"target_{index:03d}_window.txt"
    target.write_bytes(data)
    return {
        "path": str(target),
        "source_path": str(log_path),
        "line_count": len(window),
        "size_bytes": len(data),
        "truncated": truncated or len(data) < len(content.encode("utf-8")),
    }


def _summary_for_issue(issue: DfxIssue) -> str:
    text = f"{issue.code}: {_single_line(issue.message)}"
    if len(text) <= SUMMARY_MAX_CHARS:
        return text
    return text[: SUMMARY_MAX_CHARS - 1].rstrip() + "…"


def _primary_issue(issues: list[DfxIssue]) -> DfxIssue:
    priority = {
        "LP_RESULT_MISSING": 10,
        "LP_MODULE_MISSING": 20,
        "LP_SLOT_MISSING": 30,
        "LP_TARGET_LOG_MISSING": 40,
        "LP_TARGET_AMBIGUOUS": 50,
        "LP_TARGET_MISSING": 60,
        "LP_V3_MISSING": 70,
        "LP_METADATA_MISSING": 80,
        "LP_DEEP_WINDOW_EMPTY": 90,
        "LP_TARGET_TIME_MISSING": 100,
        "LP_DFX_OK": 1000,
    }
    return sorted(issues, key=lambda issue: priority.get(issue.code, 500))[0]


def _single_line(text: str) -> str:
    return " ".join(str(text).split())
