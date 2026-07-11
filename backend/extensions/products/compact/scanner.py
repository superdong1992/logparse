"""Compact 产品目录与日志格式发现扩展。"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from backend.models import JournalLogFile, LogEntry, PrivateSlotInfo, SlotInfo
from backend.extensions.products.config_validation import validate_discovery_config
from backend.plugins.base import DirectoryDiscoveryPlugin
from backend.utils import (
    extract_dump_time,
    extract_private_slot_info,
    extract_slot_id,
    glob_to_regex,
    is_compressed,
)

logger = logging.getLogger(__name__)


class CompactScannerPlugin(DirectoryDiscoveryPlugin):
    """Compact 产品目录结构。

    结构:
      ├── boards/              # 诊断日志目录
      │   ├── slot_1/
      │   │   └── debug_*.log   # 未压缩日志
      │   └── slot_2/
      └── logs/                 # 私有日志目录
          ├── slot_1/
          │   └── syslog.log
          └── slot_2/
    """

    @classmethod
    def validate_config(cls, product_name: str, config: dict[str, Any]) -> list[str]:
        return validate_discovery_config(product_name, config)

    def __init__(self, config: dict[str, Any], decompressor: Any = None):
        super().__init__(config, decompressor)
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        self._slot_pattern = glob_to_regex(
            self.config.get("slot_dir_pattern", "slot_*")
        )
        self._diag_file_patterns = [
            glob_to_regex(p)
            for p in self.config.get("diag_file_patterns", ["debug_*.log"])
        ]
        self._filename_ts_regex = re.compile(
            self.config.get("filename_timestamp_regex", r".*_(\d{8})\..*")
        )
        self._private_dir_patterns = [
            glob_to_regex(p)
            for p in self.config.get("private_dir_patterns", ["slot_*"])
        ]
        self._syslog_patterns = [
            glob_to_regex(p)
            for p in self.config.get("syslog_file_patterns", ["syslog.log"])
        ]
        self._compressed_exts = self.config.get(
            "compressed_extensions", [".gz", ".zip", ".tar.gz", ".tgz", ".tar"]
        )

    def discover(
        self, extracted_root: Path,
    ) -> tuple[list[SlotInfo], list[PrivateSlotInfo]]:
        return (
            self._scan_boards(extracted_root),
            self._scan_logs(extracted_root),
        )

    # ── diagnostic (boards/) ────────────────────────────

    def _scan_boards(self, extracted_root: Path) -> list[SlotInfo]:
        slots: list[SlotInfo] = []
        if not extracted_root.exists():
            return slots

        boards_dir = self._find_dir(extracted_root, self.config.get("diagnostic_dir", "boards"))
        if boards_dir is None:
            return slots

        for entry in sorted(boards_dir.iterdir()):
            if not entry.is_dir():
                continue
            if not self._slot_pattern.match(entry.name):
                continue

            slot = SlotInfo(
                slot_id=extract_slot_id(entry.name),
                name=entry.name,
                path=str(entry),
            )
            self._scan_board_files(entry, slot)
            slots.append(slot)

        return slots

    def _scan_board_files(self, slot_dir: Path, slot: SlotInfo) -> None:
        for f in sorted(slot_dir.iterdir()):
            if not f.is_file():
                continue
            if not self._match_any(f.name, self._diag_file_patterns):
                continue

            dump_time = extract_dump_time(f.name, self._filename_ts_regex)
            compressed = self._is_compressed(f.name)
            slot.add_diagnostic_log(LogEntry(
                path=str(f),
                name=f.name,
                size_bytes=f.stat().st_size,
                compressed=compressed,
                original_format=f.suffix if compressed else "",
                dump_time=dump_time,
            ))

    # ── private (logs/) ─────────────────────────────────

    def _scan_logs(self, extracted_root: Path) -> list[PrivateSlotInfo]:
        private_slots: list[PrivateSlotInfo] = []
        if not extracted_root.exists():
            return private_slots

        logs_dir = self._find_dir(extracted_root, self.config.get("private_dir", "logs"))
        if logs_dir is None:
            return private_slots

        for entry in sorted(logs_dir.iterdir()):
            if not entry.is_dir():
                continue
            if not self._match_any(entry.name, self._private_dir_patterns):
                continue

            slot_id, cpu_id = extract_private_slot_info(entry.name)
            private_slot = PrivateSlotInfo(
                dir_name=entry.name,
                slot_id=slot_id,
                cpu_id=cpu_id,
                path=str(entry),
            )

            # Compact 产品：syslog 文件直接存在，无内层压缩
            for f in sorted(entry.rglob("*")):
                if not f.is_file():
                    continue
                if not self._match_any(f.name, self._syslog_patterns):
                    continue
                if any(j.name == f.name for j in private_slot.journal_logs):
                    continue
                private_slot.journal_logs.append(JournalLogFile(
                    path=str(f),
                    name=f.name,
                    size_bytes=f.stat().st_size,
                    compressed=self._is_compressed(f.name),
                    sequence=0,
                ))

            private_slots.append(private_slot)

        return private_slots

    # ── helpers ───────────────────────────────────────────

    @staticmethod
    def _match_any(name: str, patterns: list[re.Pattern]) -> bool:
        return any(p.match(name) for p in patterns)

    def _is_compressed(self, name: str) -> bool:
        return is_compressed(name, self._compressed_exts)

    @staticmethod
    def _find_dir(root: Path, target: str) -> Path | None:
        direct = root / target
        if direct.is_dir():
            return direct
        for entry in root.iterdir():
            if entry.is_dir():
                candidate = entry / target
                if candidate.is_dir():
                    return candidate
        return None
