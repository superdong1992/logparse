"""Structured performance DFX helpers.

The recorder intentionally stores counters and timings only. It must never
persist raw log lines, contexts, or other source payload snippets.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from backend.infrastructure.artifact_repository import ArtifactRepository


SENSITIVE_METRIC_KEYS = {"raw", "context", "line", "payload", "log"}
ALLOWED_STAGE_NAMES = {
    "cli.result_json",
    "diagnostic_scan.shared",
    "pipeline.cleanup",
    "pipeline.discovery",
    "pipeline.extract",
    "pipeline.metadata",
    "pipeline.parse",
    "pipeline.write_output",
}
MAX_WORKER_COUNT = 32
SAFE_METRIC_KEYS = {
    "debug_expand_gz",
    "diagnostic_files",
    "diagnostic_scan_workers",
    "diagnostic_scan_workers_resolved",
    "error",
    "errors",
    "extraction_workers",
    "extraction_workers_resolved",
    "files",
    "lines",
    "mech_results",
    "timestamps",
}
SAFE_METRIC_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ENTRY_METRIC_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*_entries$")
SAFE_TEXT_METRIC_KEYS = {
    "diagnostic_scan_workers",
    "extraction_workers",
}
SAFE_TEXT_METRIC_VALUES = {"auto", "summary"}


def resolve_worker_count(
    value: Any,
    *,
    default_cap: int,
    cpu_count: int | None = None,
    max_workers: int = MAX_WORKER_COUNT,
) -> int:
    """Resolve an auto/int worker setting to a bounded positive integer."""
    available = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    available = max(1, int(available))
    max_workers = max(1, int(max_workers))
    if isinstance(value, bool):
        raise ValueError("worker count must be 'auto' or a positive integer")
    if value in (None, "auto"):
        return max(1, min(default_cap, available, max_workers))
    if isinstance(value, str):
        value = value.strip()
        if value == "auto":
            return max(1, min(default_cap, available, max_workers))
        if not value.isdigit():
            raise ValueError("worker count must be 'auto' or a positive integer")
        parsed = int(value)
    elif isinstance(value, int):
        parsed = value
    else:
        raise ValueError("worker count must be 'auto' or a positive integer")
    if parsed < 1:
        raise ValueError("worker count must be 'auto' or a positive integer")
    if parsed > max_workers:
        raise ValueError(
            f"worker count must be 'auto' or a positive integer <= {max_workers}"
        )
    return parsed


class PerformanceRecorder:
    """Collect stage timings and write a human-shareable performance report."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.enabled = enabled
        self.config = dict(config or {})
        self._started = time.perf_counter()
        self._stages: list[dict[str, Any]] = []

    def record_stage(
        self,
        name: str,
        *,
        elapsed_seconds: float,
        **metrics: Any,
    ) -> None:
        if not self.enabled:
            return
        stage_name = self._sanitize_stage_name(name)
        self._stages.append(
            {
                "name": stage_name,
                "elapsed_seconds": round(float(elapsed_seconds), 6),
                "metrics": self._sanitize_metrics(metrics),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        total = time.perf_counter() - self._started
        return {
            "schema_version": 1,
            "total_seconds": round(total, 6),
            "config": self._sanitize_metrics(self.config),
            "stages": list(self._stages),
            "stage_tree": self._build_stage_tree(),
        }

    def write(self, output_dir: Path) -> Path:
        return ArtifactRepository.for_task_dir(output_dir).write_performance(
            self.to_dict()
        )

    def summary_lines(self, *, top_n: int = 5) -> list[str]:
        data = self.to_dict()
        lines = [f"性能DFX: total={data['total_seconds']:.1f}s"]
        config_bits = [
            f"{key}={data['config'][key]}"
            for key in (
                "debug_expand_gz",
                "extraction_workers",
                "diagnostic_scan_workers",
            )
            if key in data["config"]
        ]
        if config_bits:
            lines.append("配置: " + " ".join(config_bits))
        slow = sorted(
            data["stages"],
            key=lambda item: item["elapsed_seconds"],
            reverse=True,
        )[:top_n]
        for stage in slow:
            lines.append(f"慢阶段: {stage['name']} {stage['elapsed_seconds']:.1f}s")
        if slow:
            lines.append(
                f"建议: 优先查看 performance.json 中 {slow[0]['name']} 的子阶段和计数"
            )
        else:
            lines.append("建议: performance.json 已生成，可用于隔离环境转述")
        return lines

    def _build_stage_tree(self) -> dict[str, Any]:
        root: dict[str, Any] = {}
        for stage in self._stages:
            parts = [part for part in stage["name"].split(".") if part]
            if not parts:
                continue
            children = root
            path_nodes: list[dict[str, Any]] = []
            for part in parts:
                node = children.setdefault(
                    part,
                    {"elapsed_seconds": 0.0, "metrics": {}, "children": {}},
                )
                path_nodes.append(node)
                children = node["children"]
            for node in path_nodes:
                node["elapsed_seconds"] = round(
                    node["elapsed_seconds"] + stage["elapsed_seconds"],
                    6,
                )
            path_nodes[-1]["metrics"] = dict(stage.get("metrics", {}))
        return root

    @staticmethod
    def _sanitize_stage_name(name: Any) -> str:
        text = str(name or "").strip()
        if text in ALLOWED_STAGE_NAMES:
            return text
        return "custom"

    @staticmethod
    def _sanitize_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, value in metrics.items():
            safe_key = PerformanceRecorder._sanitize_metric_key(key)
            if safe_key is None:
                continue
            if isinstance(value, (int, float, bool)) or value is None:
                clean[safe_key] = value
            elif isinstance(value, str):
                safe = PerformanceRecorder._sanitize_text_metric(safe_key, value)
                if safe is not None:
                    clean[safe_key] = safe
            elif isinstance(value, Path):
                continue
            elif isinstance(value, (list, tuple)):
                items = []
                for item in value:
                    if isinstance(item, (int, float, bool)) or item is None:
                        items.append(item)
                    elif isinstance(item, str):
                        safe = PerformanceRecorder._sanitize_text_metric(safe_key, item)
                        if safe is not None:
                            items.append(safe)
                if items:
                    clean[safe_key] = items
            elif isinstance(value, dict):
                nested = PerformanceRecorder._sanitize_metrics(value)
                if nested:
                    clean[safe_key] = nested
            else:
                if safe_key in SAFE_METRIC_KEYS:
                    clean[safe_key] = "<object>"
        return clean

    @staticmethod
    def _sanitize_metric_key(key: Any) -> str | None:
        text = str(key or "").strip()
        if PerformanceRecorder._is_sensitive_key(text):
            return None
        normalized = text.lower().replace("-", "_")
        if normalized in SAFE_METRIC_KEYS:
            return normalized
        if SAFE_METRIC_KEY_PATTERN.fullmatch(normalized):
            return normalized
        return None

    @staticmethod
    def _sanitize_text_metric(key: Any, value: str) -> str | None:
        text = value.strip()
        if not text:
            return ""
        normalized_key = str(key).lower().replace("-", "_")
        normalized_value = text.lower()
        if normalized_key in SAFE_TEXT_METRIC_KEYS:
            if normalized_value in SAFE_TEXT_METRIC_VALUES or text.isdigit():
                return text
        return None

    @staticmethod
    def _is_sensitive_key(key: Any) -> bool:
        normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).lower().replace("-", "_")
        tokens = set(normalized.split("_"))
        if tokens & {"raw", "context", "payload", "log", "line", "secret"}:
            return True
        if normalized in SENSITIVE_METRIC_KEYS:
            return True
        return False


def summarize_performance_data(data: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic DFX facts from a performance artifact.

    Missing performance data is handled by the caller because profiling is
    optional. Invalid data raises ValueError so DFX can report it explicitly.
    """
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("unsupported performance schema")
    stages = data.get("stages")
    if not isinstance(stages, list):
        raise ValueError("performance stages must be a list")

    normalized_stages: list[dict[str, Any]] = []
    counters = {"files": 0, "lines": 0, "entries": 0, "errors": 0}
    anomalies: list[dict[str, Any]] = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        name = str(stage.get("name") or "custom")
        try:
            elapsed = max(0.0, float(stage.get("elapsed_seconds") or 0.0))
        except (TypeError, ValueError):
            elapsed = 0.0
            anomalies.append({"stage": name, "reason": "invalid elapsed_seconds"})
        metrics = stage.get("metrics") if isinstance(stage.get("metrics"), dict) else {}
        normalized_stages.append({"name": name, "elapsed_seconds": elapsed})
        for key, value in metrics.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            normalized_key = str(key).lower()
            if normalized_key in {"files", "diagnostic_files"}:
                counters["files"] += int(value)
            elif normalized_key == "lines":
                counters["lines"] += int(value)
            elif normalized_key == "errors":
                counters["errors"] += int(value)
            elif ENTRY_METRIC_KEY_PATTERN.fullmatch(normalized_key):
                counters["entries"] += int(value)
        if metrics.get("error") is True or (
            isinstance(metrics.get("errors"), (int, float))
            and metrics.get("errors", 0) > 0
        ):
            anomalies.append({"stage": name, "reason": "stage reported errors"})

    slowest = sorted(
        normalized_stages,
        key=lambda item: (-item["elapsed_seconds"], item["name"]),
    )[:5]
    try:
        total_seconds = max(0.0, float(data.get("total_seconds") or 0.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid performance total_seconds") from exc
    return {
        "available": True,
        "schema_version": 1,
        "total_seconds": total_seconds,
        "stage_count": len(normalized_stages),
        "slowest_stages": slowest,
        "observed_counters": counters,
        "anomalies": anomalies[:10],
    }
