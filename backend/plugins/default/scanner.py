"""默认目录发现插件：标准 diag/ + varlog/ 结构。"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from backend.models import JournalLogFile, LogEntry, PrivateSlotInfo, SlotInfo
from backend.plugins.base import DirectoryDiscoveryPlugin
from backend.utils import (
    extract_dump_time,
    extract_journal_sequence,
    extract_private_slot_info,
    extract_slot_id,
    glob_to_regex,
    is_compressed,
)

logger = logging.getLogger(__name__)


class ScannerPlugin(DirectoryDiscoveryPlugin):
    """标准诊断日志目录结构发现。

    结构: extracted_root/
            ├── diag/              # 诊断日志目录
            │   ├── slot_1/
            │   └── slot_2/
            └── varlog/            # 私有日志目录
                ├── slot_1/
                └── slot_1_cpu_0/
    """

    def __init__(self, config: dict[str, Any], decompressor: Any = None):
        super().__init__(config, decompressor)
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        self._slot_pattern = glob_to_regex(
            self.config.get("slot_dir_pattern", "slot_*")
        )
        self._diag_file_patterns = [
            glob_to_regex(p)
            for p in self.config.get("diag_file_patterns", ["diag.zip", "diaglog_*.log.zip"])
        ]
        self._filename_ts_regex = re.compile(
            self.config.get("filename_timestamp_regex", r".*_(\d{14})\..*")
        )
        self._private_dir_patterns = [
            glob_to_regex(p)
            for p in self.config.get("private_dir_patterns", ["slot_*", "slot_*_cpu_*"])
        ]
        self._journal_file_patterns = [
            glob_to_regex(p)
            for p in self.config.get("journal_file_patterns", ["journal.log", "journal.log.*.gz"])
        ]
        self._journal_seq_regex = re.compile(
            self.config.get("journal_sequence_regex", r"journal\.log(?:\.(\d+))?(?:\.gz)?"),
            re.IGNORECASE,
        )
        self._compressed_exts = self.config.get(
            "compressed_extensions", [".gz", ".zip", ".tar.gz", ".tgz", ".tar"]
        )

    def discover(
        self, extracted_root: Path,
    ) -> tuple[list[SlotInfo], list[PrivateSlotInfo]]:
        return (
            self._scan_diag(extracted_root),
            self._scan_private(extracted_root),
        )

    # ── diag ──────────────────────────────────────────────

    def _scan_diag(self, extracted_root: Path) -> list[SlotInfo]:
        slots: list[SlotInfo] = []
        if not extracted_root.exists():
            return slots

        diag_dir = self._find_dir(extracted_root, self.config.get("diagnostic_dir", "diag"))
        if diag_dir is None:
            return slots

        for entry in sorted(diag_dir.iterdir()):
            if not entry.is_dir():
                continue
            if not self._slot_pattern.match(entry.name):
                continue

            slot = SlotInfo(
                slot_id=extract_slot_id(entry.name),
                name=entry.name,
                path=str(entry),
            )
            self._scan_slot_diag_files(entry, slot)
            slots.append(slot)

        return slots

    def _scan_slot_diag_files(self, slot_dir: Path, slot: SlotInfo) -> None:
        for f in sorted(slot_dir.iterdir()):
            if not f.is_file():
                continue
            if not self._match_any(f.name, self._diag_file_patterns):
                continue

            dump_time = extract_dump_time(f.name, self._filename_ts_regex)
            compressed = self._is_compressed(f.name)
            entry = LogEntry(
                path=str(f),
                name=f.name,
                size_bytes=f.stat().st_size,
                compressed=compressed,
                original_format=f.suffix if compressed else "",
                dump_time=dump_time,
            )
            slot.add_diagnostic_log(entry)

    # ── private / varlog ──────────────────────────────────

    def _scan_private(self, extracted_root: Path) -> list[PrivateSlotInfo]:
        private_slots: list[PrivateSlotInfo] = []
        if not extracted_root.exists():
            return private_slots

        varlog_dir = self._find_dir(extracted_root, self.config.get("private_dir", "varlog"))
        if varlog_dir is None:
            return private_slots

        archive_name = self.config.get("archive_name", "varlog.zip")

        for entry in sorted(varlog_dir.iterdir()):
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

            # 优先检测是否已被外层递归解压
            pre_extracted = (entry / "varlog").is_dir()
            if not pre_extracted:
                archive_path = entry / archive_name
                if archive_path.exists():
                    try:
                        extract_dir = entry / f"{archive_name}_extracted"
                        if not extract_dir.exists():
                            extract_dir.mkdir(parents=True, exist_ok=True)
                            if self.decompressor:
                                self.decompressor.extract_all(
                                    archive_path, extract_dir, recursive=False,
                                )
                            else:
                                import zipfile
                                with zipfile.ZipFile(archive_path, "r") as zf:
                                    for member in zf.infolist():
                                        if not self._is_safe_member(member.filename):
                                            logger.warning("跳过不安全路径: %s 中的 %s",
                                                           archive_path, member.filename)
                                            continue
                                        zf.extract(member, extract_dir)
                    except Exception:
                        logger.warning("解压 varlog.zip 失败: %s", archive_path)

            # 扫描解压目录中的 journal 文件
            for subdir in entry.iterdir():
                if subdir.is_dir() and subdir.name != archive_name:
                    self._scan_journal_in_dir(subdir, private_slot)

            private_slots.append(private_slot)

        return private_slots

    def _scan_journal_in_dir(
        self, dir_path: Path, private_slot: PrivateSlotInfo,
    ) -> None:
        inner = dir_path / "varlog"
        scan_root = inner if inner.is_dir() else dir_path
        for f in sorted(scan_root.rglob("*")):
            if not f.is_file():
                continue
            if not self._match_any(f.name, self._journal_file_patterns):
                continue
            seq = extract_journal_sequence(f.name, self._journal_seq_regex)
            if any(j.name == f.name for j in private_slot.journal_logs):
                continue
            private_slot.journal_logs.append(JournalLogFile(
                path=str(f),
                name=f.name,
                size_bytes=f.stat().st_size,
                compressed=self._is_compressed(f.name),
                sequence=seq,
            ))

    # ── helpers ───────────────────────────────────────────

    @staticmethod
    def _match_any(name: str, patterns: list[re.Pattern]) -> bool:
        return any(p.match(name) for p in patterns)

    def _is_compressed(self, name: str) -> bool:
        return is_compressed(name, self._compressed_exts)

    @staticmethod
    def _is_safe_member(name: str) -> bool:
        """检查压缩包内文件路径是否安全（无路径穿越、无绝对路径）。"""
        if os.path.isabs(name):
            return False
        # 防御 Unix 风格绝对路径 (/etc/passwd) 在 Windows 上不被 os.path.isabs 识别
        normed = name.replace("\\", "/")
        if normed.startswith("/"):
            return False
        parts = normed.split("/")
        return ".." not in parts

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
