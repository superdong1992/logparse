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

    def mech_slots(self, task_id: str) -> list[dict]:
        """列出机制模块各 slot 概况。"""
        data = self.read_result(task_id)
        if not data:
            return []
        mech = data.get("mech_results")
        if not mech:
            return []
        return mech[0].get("slots", [])

    def mech_lifecycles(self, task_id: str, slot_id: str) -> list[dict] | None:
        """列出某 slot 的周期和进程。"""
        slots = self.mech_slots(task_id)
        for s in slots:
            if s["slot_id"] == slot_id:
                return s.get("board_cycles", [])
        return None

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
    ) -> Path:
        """获取指定进程日志的文件路径。"""
        if module_name is None:
            module_name = self.first_module_name(task_id)
        base = self._output_dir / task_id / "mech_modules"
        if module_name:
            return base / module_name / f"slot_{slot_id}" / cycle / f"{proc}.log"
        return base / f"slot_{slot_id}" / cycle / f"{proc}.log"
