"""Current-product deterministic DFX and artifact compatibility checks."""

from __future__ import annotations

import json
import hashlib
import re
import shutil
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.contracts.diagnostics import DiagnosticRecord
from backend.extensions.products.current.evidence_layout import (
    CurrentProductEvidenceLayout,
)
from backend.extensions.products.current.query import ResultQueryService
from backend.infrastructure.artifact_layout import ArtifactLayout
from backend.infrastructure.artifact_repository import ArtifactRepository
from backend.performance import summarize_performance_data

SUMMARY_MAX_CHARS = 120
DEEP_MAX_WINDOWS = 5
DEEP_WINDOW_LINES = 48
DEEP_TOTAL_BYTES = 80 * 1024
_LOG_TIMESTAMP_RE = re.compile(
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)

# Backwards-compatible public name. The DTO itself is shared with parse
# manifests and contains no product topology concepts.
DfxIssue = DiagnosticRecord


def check_task_artifacts(output_task_dir: Path) -> dict[str, Any]:
    """Inspect one task without writing or reading raw log bodies.

    This is the backend for ``artifact-check --json``.  Generic manifest and
    JSON checks use ``ArtifactRepository``; the final index projection retains
    the current product's compatibility layout until the LAN adapter owns it.
    """
    layout = ArtifactLayout.from_task_dir(Path(output_task_dir))
    repository = ArtifactRepository(layout)
    issues: list[DfxIssue] = []
    checks: dict[str, Any] = {}

    manifest_check, manifest = _inspect_json_artifact(
        layout.parse_manifest,
        expected_schema=1,
    )
    checks["manifest"] = manifest_check
    if not manifest_check["exists"]:
        issues.append(DfxIssue("LP_MANIFEST_MISSING", "parse_manifest.json 不存在。"))
    elif not manifest_check["valid"]:
        issues.append(
            DfxIssue(
                "LP_MANIFEST_INVALID", "parse_manifest.json 无效或 schema 不受支持。"
            )
        )
    elif manifest is not None:
        integrity = repository.verify_manifest(manifest)
        checks["manifest"]["integrity"] = integrity
        if not integrity["ok"]:
            issues.append(
                DfxIssue(
                    "LP_ARTIFACT_INTEGRITY_FAILED",
                    "parse_manifest.json 与当前产物不一致。",
                    detail={"issues": integrity["issues"][:5]},
                )
            )

    metadata_check, _metadata = _inspect_json_artifact(
        layout.metadata,
        expected_schema=2,
    )
    checks["metadata"] = metadata_check
    _append_required_json_issue(
        issues,
        metadata_check,
        missing_code="LP_METADATA_MISSING",
        invalid_code="LP_METADATA_INVALID",
        name="metadata.json",
    )

    result_check, result = _inspect_json_artifact(
        layout.result,
        expected_schema=2,
    )
    checks["result"] = result_check
    _append_required_json_issue(
        issues,
        result_check,
        missing_code="LP_RESULT_MISSING",
        invalid_code="LP_RESULT_INVALID",
        name="result.json",
    )

    mech_exists = layout.mech_modules.is_dir()
    checks["mech_modules"] = {
        "exists": mech_exists,
        "file_count": (
            sum(1 for path in layout.mech_modules.rglob("*.log") if path.is_file())
            if mech_exists
            else 0
        ),
    }
    if not mech_exists:
        issues.append(DfxIssue("LP_MECH_MODULES_MISSING", "mech_modules/ 目录不存在。"))

    if result is not None and result_check["valid"] and mech_exists:
        index_check = _index_artifact_consistency(
            layout.task_dir,
            result.get("mech_results") or [],
        )
        checks["index_vs_files"] = index_check
        if not index_check["ok"]:
            issues.append(
                DfxIssue(
                    "LP_ARTIFACT_INDEX_MISMATCH",
                    "result.json 索引与 mech_modules 日志文件不一致。",
                    detail={
                        "missing": index_check["missing"][:5],
                        "orphan_files": index_check["orphan_files"][:5],
                    },
                )
            )
    else:
        checks["index_vs_files"] = {
            "ok": False,
            "status": "skipped",
            "reason": "result or mech_modules unavailable",
        }

    if layout.performance.exists():
        performance_check, performance = _inspect_json_artifact(
            layout.performance,
            expected_schema=1,
        )
        if performance is not None and performance_check["valid"]:
            try:
                performance_check["summary"] = summarize_performance_data(performance)
            except ValueError as exc:
                performance_check["valid"] = False
                performance_check["reason"] = str(exc)
        checks["performance"] = performance_check
        if not performance_check["valid"]:
            issues.append(DfxIssue("LP_PERFORMANCE_INVALID", "performance.json 无效。"))
    else:
        checks["performance"] = {"exists": False, "optional": True}

    issue_payloads = [issue.to_dict() for issue in issues]
    return {
        "schema_version": 1,
        "task_id": layout.task_id,
        "ok": not issues,
        "status": "ok" if not issues else "failed",
        "files": _file_status(layout),
        "checks": checks,
        "issues": issue_payloads,
    }


def build_dfx_output(
    output_task_dir: Path,
    *,
    targets_json: str | None = None,
    problem_time: str | None = None,
    deep: bool = False,
    summary_path: Path | None = None,
) -> dict[str, Any]:
    """Build deterministic DFX files under one parsed output task directory."""
    layout = ArtifactLayout.from_task_dir(Path(output_task_dir))
    repository = ArtifactRepository(layout)
    repository.ensure_task()
    task_dir = layout.task_dir
    context_dir = layout.dfx_context
    if context_dir.exists():
        shutil.rmtree(context_dir)

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
        "files": _file_status(layout),
        "issues": [],
        "manifest": {},
        "metadata": {},
        "result": {},
        "performance": {"available": False},
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
    manifest = _read_json(layout.parse_manifest)
    metadata = _read_json(layout.metadata)
    result = _read_json(layout.result)

    if manifest is None:
        # Older task outputs remain diagnosable during the handoff period.
        report["manifest"] = {
            "available": False,
            "compatibility": "legacy_output_without_manifest",
        }
    elif manifest.get("schema_version") != 1:
        report["manifest"] = {
            "available": True,
            "valid": False,
            "schema_version": manifest.get("schema_version"),
        }
        issues.append(
            DfxIssue(
                "LP_MANIFEST_INVALID",
                "parse_manifest.json schema 不受支持，产物完整性无法确认。",
                severity="warning",
            )
        )
    else:
        report["manifest"] = _manifest_summary(manifest)
        integrity = repository.verify_manifest(manifest)
        report["manifest"]["integrity"] = integrity
        if not integrity["ok"]:
            issues.append(
                DfxIssue(
                    "LP_ARTIFACT_INTEGRITY_FAILED",
                    "parse_manifest.json 与当前产物不一致。",
                    detail={"issues": integrity["issues"][:5]},
                )
            )

    if metadata is None:
        issues.append(
            DfxIssue(
                "LP_METADATA_MISSING", "metadata.json 不存在，无法确认扫描覆盖范围。"
            )
        )
    else:
        errors = metadata.get("errors") or []
        report["metadata"] = {
            "diagnostic_slot_count": len(metadata.get("diagnostic_slots") or []),
            "private_slot_count": len(metadata.get("private_slots") or []),
            "error_count": len(errors),
            "errors": _safe_json_preview(errors, limit=5),
        }

    if result is None:
        issues.append(
            DfxIssue("LP_RESULT_MISSING", "result.json 不存在，解析输出不可用于定位。")
        )
    else:
        mech_results = result.get("mech_results") or []
        report["result"] = {
            "mech_result_count": len(mech_results),
            "modules": [_module_summary(item) for item in mech_results],
            "v3_ready": _v3_ready(mech_results),
            "missing_log_examples": _missing_log_examples(task_dir, mech_results),
        }
        if not mech_results:
            issues.append(
                DfxIssue("LP_RESULT_MISSING", "result.json 中没有机制模块输出。")
            )
        if not report["result"]["v3_ready"]["ok"]:
            issues.append(
                DfxIssue("LP_V3_MISSING", "未发现可用的 lifecycle_split V3 输出。")
            )
        if report["result"]["missing_log_examples"]:
            issues.append(
                DfxIssue(
                    "LP_TARGET_LOG_MISSING",
                    "result.json 中存在进程摘要，但对应 mech_modules 日志文件缺失。",
                    detail={"examples": report["result"]["missing_log_examples"][:3]},
                )
            )

    if layout.performance.exists():
        performance = _read_json(layout.performance)
        if performance is None:
            issues.append(
                DfxIssue(
                    "LP_PERFORMANCE_INVALID",
                    "performance.json 无法读取或不是合法 JSON。",
                    severity="warning",
                )
            )
            report["performance"] = {"available": True, "valid": False}
        else:
            try:
                report["performance"] = summarize_performance_data(performance)
            except ValueError as exc:
                issues.append(
                    DfxIssue(
                        "LP_PERFORMANCE_INVALID",
                        "performance.json schema 无效。",
                        severity="warning",
                        detail={"reason": str(exc)},
                    )
                )
                report["performance"] = {"available": True, "valid": False}
            else:
                anomalies = report["performance"].get("anomalies") or []
                if anomalies:
                    issues.append(
                        DfxIssue(
                            "LP_PERFORMANCE_STAGE_ERROR",
                            "performance.json 显示解析阶段存在异常计数。",
                            severity="warning",
                            detail={"anomalies": anomalies[:5]},
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
        issues.append(
            DfxIssue(
                "LP_TARGET_TIME_MISSING",
                "target 级 DFX 缺少 problem_time，只能做结构检查。",
            )
        )
        report["targets"] = [
            {"anchor": _target_anchor(target), "status": "skipped"}
            for target in targets
        ]

    if deep and targets and not report["deep_context"]["files"]:
        issues.append(
            DfxIssue("LP_DEEP_WINDOW_EMPTY", "deep DFX 未生成任何目标日志窗口。")
        )

    if report["deep_context"]["files"]:
        context_manifest = {
            "schema_version": 1,
            "problem_time": target_problem_time,
            "max_windows": DEEP_MAX_WINDOWS,
            "window_lines": DEEP_WINDOW_LINES,
            "total_bytes_budget": DEEP_TOTAL_BYTES,
            "total_bytes": sum(
                int(item.get("size_bytes") or 0)
                for item in report["deep_context"]["files"]
            ),
            "windows": report["deep_context"]["files"],
        }
        context_manifest_path = repository.write_json(
            context_dir / "manifest.json",
            context_manifest,
        )
        report["deep_context"]["manifest"] = {
            "relative_path": layout.relative_path(context_manifest_path),
            "sha256": _sha256_file(context_manifest_path),
        }

    if not issues:
        issues.append(
            DfxIssue(
                "LP_DFX_OK",
                "output 结构检查通过，未发现确定性工具错误。",
                severity="info",
            )
        )

    report["issues"] = [issue.to_dict() for issue in issues]
    summary = _summary_for_issue(_primary_issue(issues))
    report["summary"] = summary

    repository.write_json(layout.dfx_report, report)
    repository.write_text(layout.dfx_summary, summary + "\n")
    if summary_path is not None:
        Path(summary_path).parent.mkdir(parents=True, exist_ok=True)
        Path(summary_path).write_text(summary + "\n", encoding="utf-8")
    return report


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _inspect_json_artifact(
    path: Path,
    *,
    expected_schema: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not path.exists():
        return {"exists": False, "valid": False}, None
    payload = _read_json(path)
    if payload is None:
        return {"exists": True, "valid": False, "reason": "invalid_json"}, None
    schema_version = payload.get("schema_version")
    valid = schema_version == expected_schema
    check: dict[str, Any] = {
        "exists": True,
        "valid": valid,
        "schema_version": schema_version,
        "expected_schema_version": expected_schema,
    }
    if not valid:
        check["reason"] = "unsupported_schema"
    return check, payload


def _append_required_json_issue(
    issues: list[DfxIssue],
    check: dict[str, Any],
    *,
    missing_code: str,
    invalid_code: str,
    name: str,
) -> None:
    if not check["exists"]:
        issues.append(DfxIssue(missing_code, f"{name} 不存在。"))
    elif not check["valid"]:
        issues.append(DfxIssue(invalid_code, f"{name} 无效或 schema 不受支持。"))


def _file_status(layout: ArtifactLayout) -> dict[str, Any]:
    mech_dir = layout.mech_modules
    return {
        "parse_manifest_json": layout.parse_manifest.exists(),
        "result_json": layout.result.exists(),
        "metadata_json": layout.metadata.exists(),
        "performance_json": layout.performance.exists(),
        "mech_modules_dir": mech_dir.exists(),
        "mech_modules_file_count": (
            sum(1 for item in mech_dir.rglob("*") if item.is_file())
            if mech_dir.exists()
            else 0
        ),
    }


def _manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    artifacts = manifest.get("artifacts")
    diagnostics = manifest.get("diagnostics")
    stages = manifest.get("stages")
    return {
        "available": True,
        "valid": True,
        "schema_version": manifest.get("schema_version"),
        "artifact_contract_version": manifest.get("artifact_contract_version"),
        "status": manifest.get("status"),
        "product": manifest.get("product"),
        "artifact_count": len(artifacts) if isinstance(artifacts, dict) else 0,
        "stage_count": len(stages) if isinstance(stages, list) else 0,
        "diagnostic_count": len(diagnostics) if isinstance(diagnostics, list) else 0,
        "counters": manifest.get("counters")
        if isinstance(manifest.get("counters"), dict)
        else {},
        "workspace": manifest.get("workspace")
        if isinstance(manifest.get("workspace"), dict)
        else {},
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


def _missing_log_examples(
    task_dir: Path, mech_results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for module in mech_results:
        module_name = module.get("module_name") or ""
        for slot in module.get("slots") or []:
            slot_id = str(slot.get("slot_id") or "")
            for board in slot.get("board_cycles") or []:
                board_name = str(board.get("dir_name") or "")
                for process in board.get("processes") or []:
                    path = _expected_log_path(
                        task_dir, module_name, slot_id, board_name, process
                    )
                    if not path.exists() and not _direct_cpu_log_exists(
                        path.parent, process
                    ):
                        examples.append(
                            _missing_log_example(
                                module, slot_id, board_name, process, path
                            )
                        )
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
                            examples.append(
                                _missing_log_example(
                                    module, slot_id, board_name, process, path
                                )
                            )
                            if len(examples) >= 10:
                                return examples
    return examples


def _index_artifact_consistency(
    task_dir: Path,
    mech_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare the current product result projection with emitted log files."""
    claimed: set[Path] = set()
    missing: list[dict[str, Any]] = []
    expected_count = 0
    for module in mech_results:
        module_name = module.get("module_name") or ""
        for slot in module.get("slots") or []:
            slot_id = str(slot.get("slot_id") or "")
            for board in slot.get("board_cycles") or []:
                board_name = str(board.get("dir_name") or "")
                for process in board.get("processes") or []:
                    expected_count += 1
                    path = _expected_log_path(
                        task_dir,
                        module_name,
                        slot_id,
                        board_name,
                        process,
                    )
                    if path.is_file():
                        claimed.add(path.resolve())
                        continue
                    direct_matches = _direct_cpu_log_matches(path.parent, process)
                    if direct_matches:
                        claimed.update(item.resolve() for item in direct_matches)
                        continue
                    missing.append(
                        _missing_log_example(
                            module,
                            slot_id,
                            board_name,
                            process,
                            path,
                        )
                    )
                for cpu in board.get("cpu_cycles") or []:
                    for process in cpu.get("processes") or []:
                        expected_count += 1
                        path = _expected_log_path(
                            task_dir,
                            module_name,
                            slot_id,
                            board_name,
                            process,
                            cpu_id=str(cpu.get("cpu_id") or ""),
                            cpu_cycle=str(cpu.get("dir_name") or ""),
                        )
                        if path.is_file():
                            claimed.add(path.resolve())
                        else:
                            missing.append(
                                _missing_log_example(
                                    module,
                                    slot_id,
                                    board_name,
                                    process,
                                    path,
                                )
                            )

    mech_dir = task_dir / "mech_modules"
    actual = {path.resolve() for path in mech_dir.rglob("*.log") if path.is_file()}
    orphan_files = [
        path.relative_to(task_dir.resolve()).as_posix()
        for path in sorted(actual - claimed, key=lambda item: item.as_posix())
    ]
    return {
        "ok": not missing and not orphan_files,
        "status": "checked",
        "expected_process_log_count": expected_count,
        "claimed_file_count": len(claimed),
        "actual_file_count": len(actual),
        "missing": missing[:20],
        "orphan_files": orphan_files[:20],
    }


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
    return CurrentProductEvidenceLayout.from_task_dir(task_dir).process_path(
        module_name=module_name,
        slot_id=slot_id,
        board_cycle=board_cycle,
        process_name=process.get("process_name") or "",
        pid=process.get("pid") or "",
        cpu_id=cpu_id,
        cpu_cycle=cpu_cycle,
    )


def _direct_cpu_log_exists(base: Path, process: dict[str, Any]) -> bool:
    return bool(_direct_cpu_log_matches(base, process))


def _direct_cpu_log_matches(
    base: Path,
    process: dict[str, Any],
) -> list[Path]:
    proc_file = CurrentProductEvidenceLayout.process_filename(
        process.get("process_name") or "",
        process.get("pid") or "",
    )
    return [
        item
        for item in sorted(base.glob("cpu_*/*"))
        if item.is_file() and item.name.lower() == proc_file.lower()
    ]


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
        return [dict(item) for item in targets], None if problem_time is None else str(
            problem_time
        )
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
            written = _write_deep_window(
                context_dir,
                index,
                path,
                problem_time=problem_time,
                task_dir=task_dir,
                bytes_budget=DEEP_TOTAL_BYTES - bytes_written,
            )
            if written:
                written["anchor"] = anchor
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
    problem_time: str,
    task_dir: Path,
    bytes_budget: int,
) -> dict[str, Any] | None:
    if bytes_budget <= 0 or not log_path.exists():
        return None
    selection = _select_log_window(log_path, problem_time)
    selected_lines = selection["lines"]
    if not selected_lines:
        return None

    data, emitted_line_numbers, byte_truncated = _encode_bounded_lines(
        selected_lines,
        bytes_budget,
    )
    if not data:
        return None
    context_dir.mkdir(parents=True, exist_ok=True)
    target = context_dir / f"target_{index:03d}_window.txt"
    ArtifactRepository.for_task_dir(task_dir).write_bytes(target, data)
    try:
        source_relative_path = (
            log_path.resolve().relative_to(task_dir.resolve()).as_posix()
        )
    except ValueError:
        source_relative_path = log_path.name
    return {
        "relative_path": target.relative_to(task_dir).as_posix(),
        "source_relative_path": source_relative_path,
        "problem_time": problem_time,
        "selection_reason": selection["selection_reason"],
        "nearest_timestamp": selection.get("nearest_timestamp"),
        "distance_seconds": selection.get("distance_seconds"),
        "line_ranges": _line_ranges(emitted_line_numbers),
        "line_count": len(emitted_line_numbers),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "truncated": bool(selection["truncated"] or byte_truncated),
        "caveats": list(selection.get("caveats") or []),
    }


def _encode_bounded_lines(
    lines: list[tuple[int, str]],
    bytes_budget: int,
) -> tuple[bytes, list[int], bool]:
    output = bytearray()
    emitted: list[int] = []
    truncated = False
    for line_number, line in lines:
        encoded_line = (line + "\n").encode("utf-8")
        remaining = bytes_budget - len(output)
        if remaining <= 0:
            truncated = True
            break
        if len(encoded_line) <= remaining:
            output.extend(encoded_line)
            emitted.append(line_number)
            continue
        partial = (
            encoded_line[:remaining]
            .decode(
                "utf-8",
                errors="ignore",
            )
            .encode("utf-8")
        )
        if partial:
            output.extend(partial)
            emitted.append(line_number)
        truncated = True
        break
    return bytes(output), emitted, truncated


def _select_log_window(log_path: Path, problem_time: str) -> dict[str, Any]:
    """Stream a file and retain only a bounded problem-time-centered window."""
    parsed_problem_time = _parse_iso_datetime(problem_time)
    before: deque[tuple[int, str]] = deque(maxlen=DEEP_WINDOW_LINES // 2)
    head: list[tuple[int, str]] = []
    tail: deque[tuple[int, str]] = deque(maxlen=DEEP_WINDOW_LINES // 2)
    best: dict[str, Any] | None = None
    total_lines = 0

    with log_path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            total_lines = line_number
            line = raw_line.rstrip("\r\n")
            if len(head) < DEEP_WINDOW_LINES // 2:
                head.append((line_number, line))
            tail.append((line_number, line))

            if best is not None and len(best["after"]) < (
                DEEP_WINDOW_LINES - len(best["before"]) - 1
            ):
                best["after"].append((line_number, line))

            if parsed_problem_time is not None:
                parsed_line_time = _closest_timestamp_in_line(
                    line,
                    parsed_problem_time,
                )
                if parsed_line_time is not None:
                    distance = _datetime_distance_seconds(
                        parsed_line_time,
                        parsed_problem_time,
                    )
                    if best is None or distance < best["distance_seconds"]:
                        best = {
                            "before": list(before),
                            "target": (line_number, line),
                            "after": [],
                            "timestamp": parsed_line_time,
                            "distance_seconds": distance,
                        }
            before.append((line_number, line))

    if best is not None:
        lines = [*best["before"], best["target"], *best["after"]]
        return {
            "lines": lines[:DEEP_WINDOW_LINES],
            "selection_reason": "nearest_problem_time",
            "nearest_timestamp": best["timestamp"].isoformat(),
            "distance_seconds": round(float(best["distance_seconds"]), 6),
            "truncated": total_lines > len(lines),
            "caveats": [],
        }

    fallback_by_line = {line_number: line for line_number, line in [*head, *tail]}
    fallback = sorted(fallback_by_line.items())[:DEEP_WINDOW_LINES]
    caveat = (
        "problem_time 无法解析，已确定性回退到文件首尾窗口。"
        if parsed_problem_time is None
        else "目标日志没有可解析时间戳，已确定性回退到文件首尾窗口。"
    )
    return {
        "lines": fallback,
        "selection_reason": "head_tail_fallback",
        "nearest_timestamp": None,
        "distance_seconds": None,
        "truncated": total_lines > len(fallback),
        "caveats": [caveat],
    }


def _closest_timestamp_in_line(
    line: str,
    problem_time: datetime,
) -> datetime | None:
    candidates: list[datetime] = []
    for match in _LOG_TIMESTAMP_RE.finditer(line):
        parsed = _parse_iso_datetime(match.group("timestamp"))
        if parsed is not None:
            candidates.append(parsed)
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda value: _datetime_distance_seconds(value, problem_time),
    )


def _parse_iso_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _datetime_distance_seconds(left: datetime, right: datetime) -> float:
    if left.tzinfo is None and right.tzinfo is not None:
        left = left.replace(tzinfo=right.tzinfo)
    elif right.tzinfo is None and left.tzinfo is not None:
        right = right.replace(tzinfo=left.tzinfo)
    return abs((left - right).total_seconds())


def _line_ranges(line_numbers: list[int]) -> list[dict[str, int]]:
    if not line_numbers:
        return []
    ranges: list[dict[str, int]] = []
    start = previous = line_numbers[0]
    for line_number in line_numbers[1:]:
        if line_number == previous + 1:
            previous = line_number
            continue
        ranges.append({"start": start, "end": previous})
        start = previous = line_number
    ranges.append({"start": start, "end": previous})
    return ranges


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _summary_for_issue(issue: DfxIssue) -> str:
    text = f"{issue.code}: {_single_line(issue.message)}"
    if len(text) <= SUMMARY_MAX_CHARS:
        return text
    return text[: SUMMARY_MAX_CHARS - 1].rstrip() + "…"


def _primary_issue(issues: list[DfxIssue]) -> DfxIssue:
    priority = {
        "LP_RESULT_MISSING": 10,
        "LP_ARTIFACT_INTEGRITY_FAILED": 15,
        "LP_MODULE_MISSING": 20,
        "LP_SLOT_MISSING": 30,
        "LP_TARGET_LOG_MISSING": 40,
        "LP_TARGET_AMBIGUOUS": 50,
        "LP_TARGET_MISSING": 60,
        "LP_V3_MISSING": 70,
        "LP_METADATA_MISSING": 80,
        "LP_DEEP_WINDOW_EMPTY": 90,
        "LP_TARGET_TIME_MISSING": 100,
        "LP_MANIFEST_INVALID": 200,
        "LP_PERFORMANCE_INVALID": 210,
        "LP_PERFORMANCE_STAGE_ERROR": 220,
        "LP_DFX_OK": 1000,
    }
    return sorted(issues, key=lambda issue: priority.get(issue.code, 500))[0]


def _single_line(text: str) -> str:
    return " ".join(str(text).split())
