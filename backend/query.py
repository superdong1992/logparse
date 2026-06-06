"""查询服务：封装 result.json 和 metadata.json 的读取与过滤逻辑。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from backend.utils import safe_log_filename, safe_path_segment


class ResultQueryService:
    """从 output 目录中读取解析结果并提供查询方法。"""

    def __init__(self, output_dir: Path):
        self._output_dir = output_dir

    def _read_json(self, path: Path) -> dict | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def read_metadata(self, task_id: str) -> dict | None:
        return self._read_json(self._output_dir / task_id / "metadata.json")

    def read_result(self, task_id: str) -> dict | None:
        return self._read_json(self._output_dir / task_id / "result.json")

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
                        "boundary_issues": slot.get("boundary_issues", []),
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
        base = self._output_dir / task_id / "mech_modules"
        if module_name:
            base = base / safe_path_segment(module_name)
        target = base / f"slot_{safe_path_segment(slot_id)}" / safe_path_segment(cycle)
        if cpu_id and cpu_cycle:
            target = (
                target
                / f"cpu_{safe_path_segment(cpu_id)}"
                / safe_path_segment(cpu_cycle)
            )
        elif cpu_id:
            target = target / f"cpu_{safe_path_segment(cpu_id)}"
        return _proc_argument_path(target, proc, pid)

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

        parsed_problem_time = _parse_time(problem_time)
        if parsed_problem_time is None:
            return {"target_logs": [_missing_target(anchor, "invalid problem_time")]}

        modules = self.mech_modules(task_id)
        if not modules:
            return {"target_logs": [_missing_target(anchor, "result.json not found or contains no mechanism modules")]}

        module_matches = [
            item for item in modules
            if _module_matches(item, module)
        ]
        if not module_matches:
            return {"target_logs": [_missing_target(anchor, f"module not found: {module}")]}
        if len(module_matches) > 1:
            return {"target_logs": [_ambiguous_target(anchor, f"module is ambiguous: {module}")]}

        module_item = module_matches[0]
        module_key = module_item.get("module_key") or ""
        module_name = module_item.get("module_name") or module
        target_slot = _normalize_slot(slot)

        slot_matches = [
            item for item in module_item.get("slots", [])
            if _normalize_slot(item.get("slot_id", "")) == target_slot
        ]
        if not slot_matches:
            enriched = dict(anchor, module_key=module_key, module_name=module_name)
            return {"target_logs": [_missing_target(enriched, f"slot not found: {slot}")]}

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
            return {"target_logs": [_missing_target(base_target, "process not found for anchor")]}

        selected = _select_candidate(candidates, parsed_problem_time)
        if selected["status"] == "ambiguous":
            target = _ambiguous_target(base_target, selected["reason"])
            target["caveats"].extend(selected.get("caveats", []))
            return {"target_logs": [target]}

        candidate = selected["candidate"]
        target = candidate.to_target(label=label or process_name, match_status=selected["status"])
        target["caveats"].extend(selected.get("caveats", []))

        path_result = candidate.resolve_log_path()
        if path_result["status"] == "ambiguous":
            ambiguous = _ambiguous_target(target, path_result["reason"])
            ambiguous["caveats"].extend(target.get("caveats", []))
            return {"target_logs": [ambiguous]}
        if path_result["status"] == "missing":
            missing = _missing_target(target, path_result["reason"])
            missing["caveats"].extend(target.get("caveats", []))
            return {"target_logs": [missing]}

        target["log_path"] = str(path_result["path"].resolve())
        if path_result.get("cpu_id") and not target.get("cpu_id"):
            target["cpu_id"] = path_result["cpu_id"]
        return {"target_logs": [target]}


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
        proc_file = safe_log_filename(self.process_name, self.pid)
        base = (
            self.output_dir / self.task_id / "mech_modules" / safe_path_segment(self.module_name)
            / f"slot_{safe_path_segment(self.slot)}" / safe_path_segment(self.board_cycle_name)
        )
        if self.cpu_cycle:
            path = (
                base
                / f"cpu_{safe_path_segment(self.cpu_id)}"
                / safe_path_segment(self.cpu_cycle_name)
                / proc_file
            )
            if path.exists():
                return {"status": "found", "path": path}
            return {"status": "missing", "reason": f"log file missing: {path}"}

        path = base / proc_file
        if path.exists():
            return {"status": "found", "path": path}

        direct_cpu_matches = sorted(base.glob(f"cpu_*/*"))
        direct_cpu_matches = [
            item for item in direct_cpu_matches
            if item.is_file() and item.name.lower() == proc_file.lower()
        ]
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


def _proc_argument_path(base: Path, proc: str, pid: str | None) -> Path:
    if pid is not None:
        return base / safe_log_filename(proc, pid)
    return base / safe_log_filename(proc)


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


def _select_candidate(candidates: list[_TargetCandidate], problem_time: datetime) -> dict:
    timed: list[tuple[_TargetCandidate, datetime | None, datetime | None]] = []
    unknown: list[_TargetCandidate] = []
    for candidate in candidates:
        start, end = candidate.interval()
        if start is None and end is None:
            unknown.append(candidate)
            continue
        timed.append((candidate, start, end))

    exact = [
        candidate for candidate, start, end in timed
        if _contains_time(start, end, problem_time)
    ]
    if len(exact) == 1:
        return {"status": "exact", "candidate": exact[0], "caveats": []}
    if len(exact) > 1:
        return {
            "status": "ambiguous",
            "reason": "multiple exact cycles match target process and problem_time",
            "caveats": [],
        }

    if timed:
        by_distance = [
            (candidate, _distance_to_interval(start, end, problem_time))
            for candidate, start, end in timed
        ]
        best_distance = min(distance for _candidate, distance in by_distance)
        nearest = [
            candidate for candidate, distance in by_distance
            if distance == best_distance
        ]
        if len(nearest) == 1:
            return {
                "status": "nearest",
                "candidate": nearest[0],
                "caveats": ["nearest-cycle fallback"],
            }
        return {
            "status": "ambiguous",
            "reason": "nearest tie for target process and problem_time",
            "caveats": ["nearest-cycle fallback tied"],
        }

    if len(unknown) == 1:
        return {
            "status": "unknown",
            "candidate": unknown[0],
            "caveats": ["no timed cycle available; using unknown cycle"],
        }
    return {
        "status": "ambiguous",
        "reason": "multiple unknown cycles match target process",
        "caveats": ["no timed cycle available"],
    }


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


def _align_for_compare(
    problem_time: datetime,
    start: datetime | None,
    end: datetime | None,
) -> tuple[datetime, datetime | None, datetime | None]:
    reference_tz = problem_time.tzinfo
    if reference_tz is None:
        reference_tz = (start.tzinfo if start and start.tzinfo else None) or (
            end.tzinfo if end and end.tzinfo else None
        )
    if reference_tz is None:
        return problem_time.replace(tzinfo=None), _strip_tz(start), _strip_tz(end)
    aligned_problem = problem_time
    if aligned_problem.tzinfo is None:
        aligned_problem = aligned_problem.replace(tzinfo=reference_tz)
    return aligned_problem, _with_tz(start, reference_tz), _with_tz(end, reference_tz)


def _strip_tz(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None)


def _with_tz(value: datetime | None, tzinfo) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=tzinfo)
    return value


def _contains_time(start: datetime | None, end: datetime | None, problem_time: datetime) -> bool:
    problem, aligned_start, aligned_end = _align_for_compare(problem_time, start, end)
    if aligned_start and problem < aligned_start:
        return False
    if aligned_end and problem > aligned_end:
        return False
    return aligned_start is not None or aligned_end is not None


def _distance_to_interval(start: datetime | None, end: datetime | None, problem_time: datetime) -> float:
    problem, aligned_start, aligned_end = _align_for_compare(problem_time, start, end)
    if _contains_time(aligned_start, aligned_end, problem):
        return 0.0
    distances = []
    if aligned_start is not None:
        distances.append(abs((problem - aligned_start).total_seconds()))
    if aligned_end is not None:
        distances.append(abs((problem - aligned_end).total_seconds()))
    return min(distances) if distances else float("inf")


def _missing_target(base: dict, reason: str) -> dict:
    target = dict(base)
    target["match_status"] = "missing"
    target.setdefault("caveats", []).append(reason)
    target.pop("log_path", None)
    return target


def _ambiguous_target(base: dict, reason: str) -> dict:
    target = dict(base)
    target["match_status"] = "ambiguous"
    target.setdefault("caveats", []).append(reason)
    target.pop("log_path", None)
    return target
