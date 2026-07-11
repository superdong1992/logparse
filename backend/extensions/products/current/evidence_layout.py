"""Current-product projection from generic artifact roots to evidence paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.infrastructure.artifact_layout import ArtifactLayout
from backend.utils import safe_log_filename, safe_path_segment


@dataclass(frozen=True, slots=True)
class CurrentProductEvidenceLayout:
    """Own slot/CPU hierarchy without teaching ``ArtifactLayout`` the product."""

    artifact_layout: ArtifactLayout

    @classmethod
    def from_task_dir(cls, task_dir: Path) -> "CurrentProductEvidenceLayout":
        return cls(ArtifactLayout.from_task_dir(task_dir))

    @classmethod
    def from_output_root(
        cls,
        output_root: Path,
        task_id: str,
    ) -> "CurrentProductEvidenceLayout":
        return cls(ArtifactLayout(Path(output_root), task_id))

    @property
    def root(self) -> Path:
        return self.artifact_layout.mech_modules

    def module_dir(self, module_name: str | None) -> Path:
        if not module_name:
            return self.root
        return self.root / safe_path_segment(module_name)

    def board_cycle_dir(
        self,
        module_name: str | None,
        slot_id: str,
        board_cycle: str,
    ) -> Path:
        return (
            self.module_dir(module_name)
            / f"slot_{safe_path_segment(slot_id)}"
            / safe_path_segment(board_cycle)
        )

    def process_path(
        self,
        *,
        module_name: str | None,
        slot_id: str,
        board_cycle: str,
        process_name: str,
        pid: str | None,
        cpu_id: str | None = None,
        cpu_cycle: str | None = None,
    ) -> Path:
        directory = self.board_cycle_dir(module_name, slot_id, board_cycle)
        if cpu_id:
            directory = self.cpu_dir(module_name, slot_id, board_cycle, cpu_id)
        if cpu_id and cpu_cycle:
            directory = self.cpu_cycle_dir(
                module_name,
                slot_id,
                board_cycle,
                cpu_id,
                cpu_cycle,
            )
        return directory / self.process_filename(process_name, pid)

    @staticmethod
    def process_filename(process_name: str, pid: str | None) -> str:
        return safe_log_filename(process_name, pid or "")

    def cpu_dir(
        self,
        module_name: str | None,
        slot_id: str,
        board_cycle: str,
        cpu_id: str,
    ) -> Path:
        return self.board_cycle_dir(
            module_name,
            slot_id,
            board_cycle,
        ) / f"cpu_{safe_path_segment(cpu_id)}"

    def cpu_cycle_dir(
        self,
        module_name: str | None,
        slot_id: str,
        board_cycle: str,
        cpu_id: str,
        cpu_cycle: str,
    ) -> Path:
        return self.cpu_dir(
            module_name,
            slot_id,
            board_cycle,
            cpu_id,
        ) / safe_path_segment(cpu_cycle)

    def direct_cpu_process_matches(
        self,
        *,
        module_name: str | None,
        slot_id: str,
        board_cycle: str,
        process_name: str,
        pid: str | None,
    ) -> list[Path]:
        expected_name = self.process_filename(process_name, pid)
        base = self.board_cycle_dir(module_name, slot_id, board_cycle)
        return [
            item
            for item in sorted(base.glob("cpu_*/*"))
            if item.is_file() and item.name.lower() == expected_name.lower()
        ]
