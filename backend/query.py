"""查询服务：封装 result.json 和 metadata.json 的读取与过滤逻辑。"""

from __future__ import annotations

import json
from pathlib import Path


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
    ) -> Path:
        """获取指定进程日志的文件路径。"""
        if module_name is None:
            module_name = self.first_module_name(task_id)
        base = self._output_dir / task_id / "mech_modules"
        if module_name:
            base = base / module_name
        target = base / f"slot_{slot_id}" / cycle
        if cpu_id:
            target = target / f"cpu_{cpu_id}" / (cpu_cycle or "unknown")
        return target / f"{proc}.log"
