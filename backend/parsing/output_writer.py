"""机制模块日志三层落盘。"""
from __future__ import annotations

from pathlib import Path

from backend.models import MechResult


class MechOutputWriter:
    def write(self, mech_result: MechResult, output_dir: Path) -> Path:
        mech_dir = output_dir / "mech_modules" / mech_result.module_name
        mech_dir.mkdir(parents=True, exist_ok=True)

        for slot in mech_result.slots:
            for cycle in slot.board_cycles:
                cycle_dir = mech_dir / f"slot_{slot.slot_id}" / cycle.dir_name
                for proc in cycle.processes:
                    cpu_id = proc.logs[0].cpu_id if proc.logs else None
                    out_dir = cycle_dir
                    if cpu_id:
                        out_dir = cycle_dir / f"cpu_{cpu_id}"
                    self._write_process(out_dir, proc)

                for cpu_cycle in cycle.cpu_cycles:
                    cpu_dir = cycle_dir / f"cpu_{cpu_cycle.cpu_id}" / cpu_cycle.dir_name
                    for proc in cpu_cycle.processes:
                        self._write_process(cpu_dir, proc)

        return mech_dir

    @staticmethod
    def _write_process(out_dir: Path, proc) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{proc.process_name}-{proc.pid}.log" if proc.pid else f"{proc.process_name}.log"
        out_path = out_dir / fname
        with open(out_path, "w", encoding="utf-8") as fh:
            for log in proc.logs:
                seq = f"[{log.sequence:04d}]" if log.sequence else "[....]"
                fh.write(
                    f"{seq} [{log.source}|{log.source_file}] {log.raw}\n"
                )
