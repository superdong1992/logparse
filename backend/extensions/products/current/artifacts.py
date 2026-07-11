"""当前产品的 slot/CPU 机制证据路径 projection。"""

from __future__ import annotations

from pathlib import Path

from backend.extensions.products.current.evidence_layout import (
    CurrentProductEvidenceLayout,
)
from backend.infrastructure.artifact_repository import ArtifactRepository
from backend.models import MechResult


class MechOutputWriter:
    def write(self, mech_result: MechResult, output_dir: Path) -> Path:
        layout = CurrentProductEvidenceLayout.from_task_dir(output_dir)
        repository = ArtifactRepository(layout.artifact_layout)
        mech_dir = layout.module_dir(mech_result.module_name)
        mech_dir.mkdir(parents=True, exist_ok=True)

        for slot in mech_result.slots:
            for cycle in slot.board_cycles:
                cycle_dir = layout.board_cycle_dir(
                    mech_result.module_name,
                    slot.slot_id,
                    cycle.dir_name,
                )
                for proc in cycle.processes:
                    cpu_id = proc.logs[0].cpu_id if proc.logs else None
                    out_dir = cycle_dir
                    if cpu_id:
                        out_dir = layout.cpu_dir(
                            mech_result.module_name,
                            slot.slot_id,
                            cycle.dir_name,
                            cpu_id,
                        )
                    self._write_process(repository, out_dir, proc)

                for cpu_cycle in cycle.cpu_cycles:
                    cpu_dir = layout.cpu_cycle_dir(
                        mech_result.module_name,
                        slot.slot_id,
                        cycle.dir_name,
                        cpu_cycle.cpu_id,
                        cpu_cycle.dir_name,
                    )
                    for proc in cpu_cycle.processes:
                        self._write_process(repository, cpu_dir, proc)

        return mech_dir

    @staticmethod
    def _write_process(
        repository: ArtifactRepository,
        out_dir: Path,
        proc,
    ) -> None:
        fname = CurrentProductEvidenceLayout.process_filename(
            proc.process_name,
            proc.pid,
        )
        out_path = out_dir / fname
        lines = []
        for log in proc.logs:
            seq = f"[{log.sequence:04d}]" if log.sequence else "[....]"
            lines.append(f"{seq} [{log.source}|{log.source_file}] {log.raw}\n")
        repository.write_text(out_path, "".join(lines))
