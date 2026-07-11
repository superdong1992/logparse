"""Canonical paths for one logparse task's official artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArtifactLayout:
    output_root: Path
    task_id: str

    def __post_init__(self) -> None:
        task_id = str(self.task_id)
        if not task_id or task_id in {".", ".."}:
            raise ValueError("task_id must be a non-empty path segment")
        if Path(task_id).name != task_id or "/" in task_id or "\\" in task_id:
            raise ValueError("task_id must be a single path segment")
        object.__setattr__(self, "output_root", Path(self.output_root))
        object.__setattr__(self, "task_id", task_id)

    @classmethod
    def from_task_dir(cls, task_dir: Path) -> "ArtifactLayout":
        path = Path(task_dir)
        return cls(output_root=path.parent, task_id=path.name)

    @property
    def task_dir(self) -> Path:
        return self.output_root / self.task_id

    @property
    def parse_manifest(self) -> Path:
        return self.task_dir / "parse_manifest.json"

    @property
    def metadata(self) -> Path:
        return self.task_dir / "metadata.json"

    @property
    def result(self) -> Path:
        return self.task_dir / "result.json"

    @property
    def mech_modules(self) -> Path:
        return self.task_dir / "mech_modules"

    @property
    def performance(self) -> Path:
        return self.task_dir / "performance.json"

    @property
    def dfx_report(self) -> Path:
        return self.task_dir / "dfx_report.json"

    @property
    def dfx_summary(self) -> Path:
        return self.task_dir / "dfx_summary.txt"

    @property
    def dfx_context(self) -> Path:
        return self.task_dir / "dfx_context"

    def relative_path(self, path: Path) -> str:
        candidate = Path(path)
        try:
            return candidate.relative_to(self.task_dir).as_posix()
        except ValueError as exc:
            raise ValueError(
                f"artifact path is outside task directory: {path}"
            ) from exc
