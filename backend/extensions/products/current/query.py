"""当前产品查询投影：封装 result/metadata 与 slot/CPU 兼容查询。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from backend.domain.correlation.target_selection import select_interval_candidate
from backend.extensions.products.current.evidence_layout import (
    CurrentProductEvidenceLayout,
)


TARGET_LOGS_SCHEMA_VERSION = 1
TARGET_LOGS_API_VERSION = 1
RESULT_SCHEMA_VERSION = 2
METADATA_SCHEMA_VERSION = 2


class QueryArtifactSchemaError(ValueError):
    def __init__(self, artifact: str, actual: object, expected: int) -> None:
        self.artifact = artifact
        self.actual = actual
        self.expected = expected
        super().__init__(
            f"unsupported {artifact} schema_version: {actual}; expected {expected}"
        )


class ResultQueryService:
    """从 output 目录中读取解析结果并提供查询方法。"""

    def __init__(self, output_dir: Path):
        self._output_dir = output_dir

    def _read_json(self, path: Path, *, expected_schema: int) -> dict | None:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        actual_schema = payload.get("schema_version")
        # Schema-less files are accepted only as a legacy compatibility input;
        # every newly written formal artifact carries an explicit version.
        if actual_schema is not None and actual_schema != expected_schema:
            raise QueryArtifactSchemaError(path.name, actual_schema, expected_schema)
        return payload

    def read_metadata(self, task_id: str) -> dict | None:
        return self._read_json(
            self._output_dir / task_id / "metadata.json",
            expected_schema=METADATA_SCHEMA_VERSION,
        )

    def read_result(self, task_id: str) -> dict | None:
        return self._read_json(
            self._output_dir / task_id / "result.json",
            expected_schema=RESULT_SCHEMA_VERSION,
        )

    def list_slots(self, task_id: str) -> list[dict]:
        """列出所有诊断日志槽位。"""
        data = self.read_metadata(task_id)
        if not data:
            return []
        return data.get("diagnostic_slots", [])

    def query_diag(self, task_id: str, slot_id: str) -> dict | None:
        """查询特定槽位的诊断日志详情。"""
        data = self.read_metadata(task_id)
        if not data:
            return None
        for s in data.get("diagnostic_slots", []):
            if s["slot_id"] == slot_id:
                return s
        return None

    def mech_modules(self, task_id: str, module_name: str | None = None) -> list[dict]:
        """列出机制模块解析结果，可按模块名过滤。"""
        data = self.read_result(task_id)
        if not data:
            return []
        modules = data.get("mech_results") or []
        if module_name:
            modules = [m for m in modules if m.get("module_name") == module_name]
        return modules

    def mech_slots(self, task_id: str, module_name: str | None = None) -> list[dict]:
        """列出机制模块各 slot 概况，默认返回全部模块的 slots。"""
        modules = self.mech_modules(task_id, module_name)
        results: list[dict] = []
        for module in modules:
            name = module.get("module_name", "")
            for slot in module.get("slots", []):
                item = dict(slot)
                item["_module_name"] = name
                results.append(item)
        return results

    def mech_lifecycles(
        self,
        task_id: str,
        slot_id: str,
        module_name: str | None = None,
    ) -> list[dict]:
        """列出某 slot 的周期和进程，默认返回全部模块中的该 slot。"""
        modules = self.mech_modules(task_id, module_name)
        results: list[dict] = []
        for module in modules:
            name = module.get("module_name", "")
            for slot in module.get("slots", []):
                if slot.get("slot_id") == slot_id:
                    results.append({
                        "module_name": name,
                        "slot_id": slot_id,
                        "lifecycle_reliable": slot.get("lifecycle_reliable", True),
                        "lifecycle_split_result": slot.get("lifecycle_split_result"),
                        "board_cycles": slot.get("board_cycles", []),
                    })
        return results

    def first_module_name(self, task_id: str) -> str | None:
        """从 result.json 中获取第一个机制模块名。"""
        data = self.read_result(task_id)
        if not data:
            return None
        mech_results = data.get("mech_results") or []
        if not mech_results:
            return None
        return mech_results[0].get("module_name")

    def mech_log_path(
        self,
        task_id: str,
        slot_id: str,
        cycle: str,
        proc: str,
        module_name: str | None = None,
        cpu_id: str | None = None,
        cpu_cycle: str | None = None,
        pid: str | None = None,
    ) -> Path:
        """获取指定进程日志的文件路径。"""
        if module_name is None:
            module_name = self.first_module_name(task_id)
        layout = CurrentProductEvidenceLayout.from_output_root(
            self._output_dir,
            task_id,
        )
        return layout.process_path(
            module_name=module_name,
            slot_id=slot_id,
            board_cycle=cycle,
            process_name=proc,
            pid=pid,
            cpu_id=cpu_id,
            cpu_cycle=cpu_cycle,
        )

    def resolve_target_logs(
        self,
        task_id: str,
        *,
        problem_time: str | datetime,
        module: str,
        slot: str,
        process_name: str,
        pid: str | None = None,
        label: str | None = None,
        explain: bool = False,
    ) -> dict:
        """Resolve one process anchor to the deterministic target_logs contract."""
        anchor = {
            "label": label or process_name,
            "module": module,
            "slot": _normalize_slot(slot),
            "process_name": process_name,
        }
        if pid:
            anchor["pid"] = str(pid)
        diagnostics = _selection_diagnostics(anchor)

        parsed_problem_time = _parse_time(problem_time)
        if parsed_problem_time is None:
            return _target_payload(
                _missing_target(anchor, "invalid problem_time", error_code="LP_TARGET_TIME_INVALID"),
                diagnostics,
                explain,
                error_code="LP_TARGET_TIME_INVALID",
                reason="invalid problem_time",
            )

        try:
            modules = self.mech_modules(task_id)
        except QueryArtifactSchemaError as exc:
            return _target_payload(
                _missing_target(
                    anchor,
                    str(exc),
                    error_code="LP_SCHEMA_UNSUPPORTED",
                ),
                diagnostics,
                explain,
                error_code="LP_SCHEMA_UNSUPPORTED",
                reason=str(exc),
            )
        if not modules:
            return _target_payload(
                _missing_target(
                    anchor,
                    "result.json not found or contains no mechanism modules",
                    error_code="LP_RESULT_MISSING",
                ),
                diagnostics,
                explain,
                error_code="LP_RESULT_MISSING",
                reason="result.json not found or contains no mechanism modules",
            )

        module_matches = [
            item for item in modules
            if _module_matches(item, module)
        ]
        diagnostics["module_match_count"] = len(module_matches)
        diagnostics["available_modules"] = [
            {
                "module_key": item.get("module_key") or "",
                "module_name": item.get("module_name") or "",
            }
            for item in modules
        ]
        if not module_matches:
            return _target_payload(
                _missing_target(anchor, f"module not found: {module}", error_code="LP_MODULE_MISSING"),
                diagnostics,
                explain,
                error_code="LP_MODULE_MISSING",
                reason=f"module not found: {module}",
            )
        if len(module_matches) > 1:
            return _target_payload(
                _ambiguous_target(anchor, f"module is ambiguous: {module}", error_code="LP_TARGET_AMBIGUOUS"),
                diagnostics,
                explain,
                error_code="LP_TARGET_AMBIGUOUS",
                reason=f"module is ambiguous: {module}",
            )

        module_item = module_matches[0]
        module_key = module_item.get("module_key") or ""
        module_name = module_item.get("module_name") or module
        target_slot = _normalize_slot(slot)

        slot_matches = [
            item for item in module_item.get("slots", [])
            if _normalize_slot(item.get("slot_id", "")) == target_slot
        ]
        diagnostics["module_key"] = module_key
        diagnostics["module_name"] = module_name
        diagnostics["slot_match_count"] = len(slot_matches)
        diagnostics["available_slots"] = [
            _normalize_slot(item.get("slot_id", "")) for item in module_item.get("slots", [])
        ]
        if not slot_matches:
            enriched = dict(anchor, module_key=module_key, module_name=module_name)
            return _target_payload(
                _missing_target(enriched, f"slot not found: {slot}", error_code="LP_SLOT_MISSING"),
                diagnostics,
                explain,
                error_code="LP_SLOT_MISSING",
                reason=f"slot not found: {slot}",
            )

        candidates: list[_TargetCandidate] = []
        for slot_item in slot_matches:
            candidates.extend(
                _iter_target_candidates(
                    output_dir=self._output_dir,
                    task_id=task_id,
                    module_key=module_key,
                    module_name=module_name,
                    slot_id=_normalize_slot(slot_item.get("slot_id", "")),
                    board_cycles=slot_item.get("board_cycles", []),
                    process_name=process_name,
                    pid=str(pid) if pid else None,
                )
            )
        diagnostics["candidate_count"] = len(candidates)
        diagnostics["candidate_summaries"] = [_candidate_summary(item) for item in candidates]

        base_target = {
            "label": label or process_name,
            "module_key": module_key,
            "module_name": module_name,
            "slot": target_slot,
            "process_name": process_name,
        }
        if pid:
            base_target["pid"] = str(pid)

        if not candidates:
            return _target_payload(
                _missing_target(base_target, "process not found for anchor", error_code="LP_TARGET_MISSING"),
                diagnostics,
                explain,
                error_code="LP_TARGET_MISSING",
                reason="process not found for anchor",
            )

        selected = select_interval_candidate(candidates, parsed_problem_time)
        diagnostics["selected_status"] = selected.status
        if selected.status == "ambiguous":
            target = _ambiguous_target(
                base_target,
                selected.reason,
                error_code="LP_TARGET_AMBIGUOUS",
            )
            target["caveats"].extend(selected.caveats)
            return _target_payload(
                target,
                diagnostics,
                explain,
                error_code="LP_TARGET_AMBIGUOUS",
                reason=selected.reason,
            )

        candidate = selected.candidate
        if candidate is None:  # Defensive: all non-ambiguous policies select one.
            raise RuntimeError("target selection returned no candidate")
        target = candidate.to_target(
            label=label or process_name,
            match_status=selected.status,
        )
        target["caveats"].extend(selected.caveats)
        diagnostics["selected_candidate"] = _candidate_summary(candidate)

        path_result = candidate.resolve_log_path()
        if path_result["status"] == "ambiguous":
            ambiguous = _ambiguous_target(
                target,
                path_result["reason"],
                error_code="LP_TARGET_AMBIGUOUS",
            )
            ambiguous["caveats"].extend(target.get("caveats", []))
            return _target_payload(
                ambiguous,
                diagnostics,
                explain,
                error_code="LP_TARGET_AMBIGUOUS",
                reason=path_result["reason"],
            )
        if path_result["status"] == "missing":
            missing = _missing_target(
                target,
                path_result["reason"],
                error_code="LP_TARGET_LOG_MISSING",
            )
            missing["caveats"].extend(target.get("caveats", []))
            return _target_payload(
                missing,
                diagnostics,
                explain,
                error_code="LP_TARGET_LOG_MISSING",
                reason=path_result["reason"],
            )

        target["log_path"] = str(path_result["path"].resolve())
        if path_result.get("cpu_id") and not target.get("cpu_id"):
            target["cpu_id"] = path_result["cpu_id"]
        return _target_payload(
            target,
            diagnostics,
            explain,
            error_code="LP_TARGET_OK",
            reason=f"target resolved with status: {selected.status}",
        )


class _TargetCandidate:
    def __init__(
        self,
        *,
        output_dir: Path,
        task_id: str,
        module_key: str,
        module_name: str,
        slot: str,
        process: dict,
        board_cycle: dict,
        cpu_cycle: dict | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.task_id = task_id
        self.module_key = module_key
        self.module_name = module_name
        self.slot = slot
        self.process = process
        self.board_cycle = board_cycle
        self.cpu_cycle = cpu_cycle

    @property
    def process_name(self) -> str:
        return str(self.process.get("process_name") or "")

    @property
    def pid(self) -> str:
        return str(self.process.get("pid") or "")

    @property
    def board_cycle_name(self) -> str:
        return str(self.board_cycle.get("dir_name") or "")

    @property
    def cpu_id(self) -> str:
        if not self.cpu_cycle:
            return ""
        return str(self.cpu_cycle.get("cpu_id") or "")

    @property
    def cpu_cycle_name(self) -> str:
        if not self.cpu_cycle:
            return ""
        return str(self.cpu_cycle.get("dir_name") or "")

    def interval(self) -> tuple[datetime | None, datetime | None]:
        owner = self.cpu_cycle or self.board_cycle
        return _parse_time(owner.get("start_time")), _parse_time(owner.get("end_time"))

    def to_target(self, *, label: str, match_status: str) -> dict:
        target = {
            "label": label,
            "module_key": self.module_key,
            "module_name": self.module_name,
            "slot": self.slot,
            "process_name": self.process_name,
            "match_status": match_status,
            "board_cycle": self.board_cycle_name or None,
            "cpu_cycle": self.cpu_cycle_name or None,
            "caveats": [],
        }
        if self.pid:
            target["pid"] = self.pid
        if self.cpu_id:
            target["cpu_id"] = self.cpu_id
        return target

    def resolve_log_path(self) -> dict:
        layout = CurrentProductEvidenceLayout.from_output_root(
            self.output_dir,
            self.task_id,
        )
        if self.cpu_cycle:
            path = layout.process_path(
                module_name=self.module_name,
                slot_id=self.slot,
                board_cycle=self.board_cycle_name,
                process_name=self.process_name,
                pid=self.pid,
                cpu_id=self.cpu_id,
                cpu_cycle=self.cpu_cycle_name,
            )
            if path.exists():
                return {"status": "found", "path": path}
            return {"status": "missing", "reason": f"log file missing: {path}"}

        path = layout.process_path(
            module_name=self.module_name,
            slot_id=self.slot,
            board_cycle=self.board_cycle_name,
            process_name=self.process_name,
            pid=self.pid,
        )
        if path.exists():
            return {"status": "found", "path": path}

        direct_cpu_matches = layout.direct_cpu_process_matches(
            module_name=self.module_name,
            slot_id=self.slot,
            board_cycle=self.board_cycle_name,
            process_name=self.process_name,
            pid=self.pid,
        )
        if len(direct_cpu_matches) == 1:
            cpu_dir = direct_cpu_matches[0].parent.name
            cpu_id = cpu_dir.removeprefix("cpu_")
            return {"status": "found", "path": direct_cpu_matches[0], "cpu_id": cpu_id}
        if len(direct_cpu_matches) > 1:
            return {
                "status": "ambiguous",
                "reason": "multiple board-level CPU log files match target process",
            }
        return {"status": "missing", "reason": f"log file missing: {path}"}


def _normalize_slot(value) -> str:
    text = str(value or "")
    if text.lower().startswith("slot_"):
        return text[5:]
    return text


def _module_matches(module_item: dict, module: str) -> bool:
    target = str(module or "").lower()
    return target in {
        str(module_item.get("module_key") or "").lower(),
        str(module_item.get("module_name") or "").lower(),
    }


def _process_matches(process: dict, process_name: str, pid: str | None) -> bool:
    if str(process.get("process_name") or "").lower() != str(process_name or "").lower():
        return False
    if pid is not None and str(process.get("pid") or "") != str(pid):
        return False
    return True


def _iter_target_candidates(
    *,
    output_dir: Path,
    task_id: str,
    module_key: str,
    module_name: str,
    slot_id: str,
    board_cycles: list[dict],
    process_name: str,
    pid: str | None,
) -> list[_TargetCandidate]:
    candidates: list[_TargetCandidate] = []
    for board_cycle in board_cycles:
        for process in board_cycle.get("processes", []):
            if _process_matches(process, process_name, pid):
                candidates.append(
                    _TargetCandidate(
                        output_dir=output_dir,
                        task_id=task_id,
                        module_key=module_key,
                        module_name=module_name,
                        slot=slot_id,
                        process=process,
                        board_cycle=board_cycle,
                    )
                )
        for cpu_cycle in board_cycle.get("cpu_cycles", []):
            for process in cpu_cycle.get("processes", []):
                if _process_matches(process, process_name, pid):
                    candidates.append(
                        _TargetCandidate(
                            output_dir=output_dir,
                            task_id=task_id,
                            module_key=module_key,
                            module_name=module_name,
                            slot=slot_id,
                            process=process,
                            board_cycle=board_cycle,
                            cpu_cycle=cpu_cycle,
                        )
                    )
    return candidates


def _parse_time(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _selection_diagnostics(anchor: dict) -> dict:
    return {
        "normalized_anchor": dict(anchor),
        "module_match_count": 0,
        "slot_match_count": 0,
        "candidate_count": 0,
        "candidate_summaries": [],
    }


def _candidate_summary(candidate: _TargetCandidate) -> dict:
    start, end = candidate.interval()
    item = {
        "module_key": candidate.module_key,
        "module_name": candidate.module_name,
        "slot": candidate.slot,
        "process_name": candidate.process_name,
        "pid": candidate.pid,
        "board_cycle": candidate.board_cycle_name or None,
        "cpu_id": candidate.cpu_id or None,
        "cpu_cycle": candidate.cpu_cycle_name or None,
        "start_time": start.isoformat() if start else None,
        "end_time": end.isoformat() if end else None,
    }
    return item


def _target_payload(
    target: dict,
    diagnostics: dict,
    explain: bool,
    *,
    error_code: str,
    reason: str,
) -> dict:
    if explain:
        target = dict(target)
        target["error_code"] = error_code
        diagnostics = dict(diagnostics)
        diagnostics["error_code"] = error_code
        diagnostics["reason"] = reason
        return {
            "schema_version": TARGET_LOGS_SCHEMA_VERSION,
            "api_version": TARGET_LOGS_API_VERSION,
            "target_logs": [target],
            "selection_diagnostics": diagnostics,
        }
    target = dict(target)
    target.pop("error_code", None)
    return {
        "schema_version": TARGET_LOGS_SCHEMA_VERSION,
        "api_version": TARGET_LOGS_API_VERSION,
        "target_logs": [target],
    }


def _missing_target(base: dict, reason: str, *, error_code: str | None = None) -> dict:
    target = dict(base)
    target["match_status"] = "missing"
    target.setdefault("caveats", []).append(reason)
    if error_code:
        target["error_code"] = error_code
    target.pop("log_path", None)
    return target


def _ambiguous_target(base: dict, reason: str, *, error_code: str | None = None) -> dict:
    target = dict(base)
    target["match_status"] = "ambiguous"
    target.setdefault("caveats", []).append(reason)
    if error_code:
        target["error_code"] = error_code
    target.pop("log_path", None)
    return target
