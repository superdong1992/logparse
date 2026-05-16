from __future__ import annotations

import re
from pathlib import Path

from backend.config import ConfigLoader
from backend.models import JournalLogEntry, LogEntry, PrivateSlotInfo, SlotInfo


class Scanner:
    """
    目录扫描器：按实际日志包结构扫描。

    结构: extracted_root/
            ├── diag/              # 诊断日志目录
            │   ├── slot_1/
            │   │   ├── diag.zip
            │   │   └── diaglog_1_20260103202627.log.zip
            │   └── slot_2/
            └── varlog/            # 私有日志目录
                ├── slot_1/
                │   └── varlog.zip
                ├── slot_1_cpu_0/
                │   └── varlog.zip
                └── slot_2/
                    └── varlog.zip
    """

    def __init__(self, config_loader: ConfigLoader):
        self.config = config_loader

    def scan_diag(self, extracted_root: Path) -> list[SlotInfo]:
        """扫描 diag/ 目录，识别各槽位的诊断日志。"""
        slots: list[SlotInfo] = []

        if not extracted_root.exists():
            return slots

        diag_dir = self._find_diag_dir(extracted_root)
        if diag_dir is None:
            return slots

        for entry in sorted(diag_dir.iterdir()):
            if not entry.is_dir():
                continue
            if not self.config.is_slot_dir(entry.name):
                continue

            slot = SlotInfo(
                slot_id=self.config.extract_slot_id(entry.name),
                name=entry.name,
                path=str(entry),
            )
            self._scan_slot_diag_files(entry, slot)
            slots.append(slot)

        return slots

    def scan_private(self, extracted_root: Path) -> list[PrivateSlotInfo]:
        """扫描 varlog/ 目录，识别各槽位的私有日志。"""
        import zipfile

        private_slots: list[PrivateSlotInfo] = []

        if not extracted_root.exists():
            return private_slots

        varlog_dir = self._find_varlog_dir(extracted_root)
        if varlog_dir is None:
            return private_slots

        archive_name = self.config.get_archive_name()

        for entry in sorted(varlog_dir.iterdir()):
            if not entry.is_dir():
                continue
            if not self.config.is_private_slot_dir(entry.name):
                continue

            slot_id, cpu_id = self.config.extract_private_slot_info(entry.name)

            private_slot = PrivateSlotInfo(
                dir_name=entry.name,
                slot_id=slot_id,
                cpu_id=cpu_id,
                path=str(entry),
            )

            # 优先检测是否已被外层递归解压（varlog/ 子目录已存在）
            pre_extracted = (entry / "varlog").is_dir()

            if not pre_extracted:
                # 解压 varlog.zip 到 _extracted 子目录（兼容非递归场景）
                archive_path = entry / archive_name
                if archive_path.exists():
                    try:
                        extract_dir = entry / f"{archive_name}_extracted"
                        if not extract_dir.exists():
                            extract_dir.mkdir(parents=True, exist_ok=True)
                            with zipfile.ZipFile(archive_path, "r") as zf:
                                zf.extractall(extract_dir)
                    except Exception:
                        import logging
                        logging.getLogger(__name__).warning(
                            "解压 varlog.zip 失败: %s", archive_path
                        )

            # 扫描解压目录中的 journal 文件
            for subdir in entry.iterdir():
                if subdir.is_dir() and subdir.name != archive_name:
                    self._scan_journal_in_dir(subdir, private_slot)

            private_slots.append(private_slot)

        return private_slots

    def _scan_journal_in_dir(self, dir_path: Path, private_slot: PrivateSlotInfo) -> None:
        """在已解压的目录中扫描 journal 日志文件。
        varlog.zip 解压后内部还有一层 varlog/ 目录。"""
        # 先检查是否有一层 inner varlog/
        inner = dir_path / "varlog"
        scan_root = inner if inner.is_dir() else dir_path
        for f in sorted(scan_root.rglob("*")):
            if not f.is_file():
                continue
            if not self.config.match_journal_file(f.name):
                continue
            seq = self.config.extract_journal_sequence(f.name)
            # 避免重复
            if any(j.name == f.name for j in private_slot.journal_logs):
                continue
            private_slot.journal_logs.append(JournalLogEntry(
                path=str(f),
                name=f.name,
                size_bytes=f.stat().st_size,
                compressed=self.config.is_compressed(f.name),
                sequence=seq,
            ))

    def _find_diag_dir(self, root: Path) -> Path | None:
        direct = root / self.config.get_config().package.diagnostic_dir
        if direct.is_dir():
            return direct
        entries = [e for e in root.iterdir() if e.is_dir()]
        for entry in entries:
            candidate = entry / self.config.get_config().package.diagnostic_dir
            if candidate.is_dir():
                return candidate
        return None

    def _find_varlog_dir(self, root: Path) -> Path | None:
        direct = root / self.config.get_config().package.private_dir
        if direct.is_dir():
            return direct
        entries = [e for e in root.iterdir() if e.is_dir()]
        for entry in entries:
            candidate = entry / self.config.get_config().package.private_dir
            if candidate.is_dir():
                return candidate
        return None

    def _scan_slot_diag_files(self, slot_dir: Path, slot: SlotInfo) -> None:
        for f in sorted(slot_dir.iterdir()):
            if not f.is_file():
                continue
            if not self.config.match_diag_file(f.name):
                continue

            dump_time = self.config.extract_dump_time(f.name)
            entry = LogEntry(
                path=str(f),
                name=f.name,
                size_bytes=f.stat().st_size,
                compressed=self.config.is_compressed(f.name),
                original_format=f.suffix if self.config.is_compressed(f.name) else "",
                dump_time=dump_time,
            )
            slot.add_diagnostic_log(entry)
