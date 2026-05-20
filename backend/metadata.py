from __future__ import annotations

import json
from pathlib import Path

from backend.models import MechResult, ParseResult, PrivateSlotInfo, SlotInfo


class MetadataGenerator:
    """生成结构化元数据 JSON 文件。"""

    def generate(self, result: ParseResult, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = output_dir / "metadata.json"

        data = {
            "task_id": result.task_id,
            "package_name": result.package_name,
            "extracted_root": result.extracted_root,
            "created_at": result.created_at.isoformat(),
            "diagnostic_slots": [self._slot_to_dict(s) for s in result.diagnostic_slots],
            "private_slots": [self._private_slot_to_dict(s) for s in result.private_slots],
            "mech_results": [self._mech_to_dict(a) for a in result.mech_results] if result.mech_results else [],
            "errors": result.errors,
        }

        with metadata_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
        return metadata_path

    @staticmethod
    def _slot_to_dict(slot: SlotInfo) -> dict:
        return {
            "slot_id": slot.slot_id,
            "name": slot.name,
            "type": slot.board_type.value,
            "role": slot.role.value,
            "path": slot.path,
            "content_timestamp_count": len(slot.all_content_timestamps),
            "active_periods": [
                {
                    "start": p.start.isoformat(),
                    "end": p.end.isoformat(),
                    "duration_seconds": p.duration.total_seconds(),
                }
                for p in slot.active_periods
            ],
            "diagnostic_logs": [
                {
                    "path": e.path,
                    "name": e.name,
                    "size_bytes": e.size_bytes,
                    "compressed": e.compressed,
                    "original_format": e.original_format,
                    "extracted_path": e.extracted_path,
                    "dump_time": e.dump_time.isoformat() if e.dump_time else None,
                    "content_timestamp_count": len(e.content_timestamps),
                }
                for e in slot.diagnostic_logs
            ],
        }

    @staticmethod
    def _private_slot_to_dict(slot: PrivateSlotInfo) -> dict:
        return {
            "dir_name": slot.dir_name,
            "slot_id": slot.slot_id,
            "cpu_id": slot.cpu_id,
            "path": slot.path,
            "journal_logs": [
                {
                    "path": jl.path,
                    "name": jl.name,
                    "size_bytes": jl.size_bytes,
                    "compressed": jl.compressed,
                    "sequence": jl.sequence,
                }
                for jl in slot.journal_logs
            ],
        }

    @staticmethod
    def _mech_to_dict(mech: MechResult) -> dict:
        return {
            "module_name": mech.module_name,
            "active_master_slots": mech.active_master_slots,
            "diag_entry_count": mech.diag_entry_count,
            "journal_entry_count": mech.journal_entry_count,
            "slots": [
                {
                    "slot_id": s.slot_id,
                    "board_cycles": [
                        {
                            "dir_name": c.dir_name,
                            "start_time": c.start_time.isoformat() if c.start_time else None,
                            "end_time": c.end_time.isoformat() if c.end_time else None,
                            "processes": [
                                {
                                    "process_name": p.process_name,
                                    "pid": p.pid,
                                    "total_count": p.total_count,
                                    "missing_count": len(p.missing_sequences),
                                }
                                for p in c.processes
                            ],
                        }
                        for c in s.board_cycles
                    ],
                }
                for s in mech.slots
            ],
        }
