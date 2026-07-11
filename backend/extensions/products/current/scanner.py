"""当前产品目录与日志格式发现扩展。"""

from __future__ import annotations

import logging
import re
import hashlib
from pathlib import Path
from typing import Any

from backend.models import JournalLogFile, LogEntry, PrivateSlotInfo, SlotInfo
from backend.parsing.file_iter import iter_log_entry_lines
from backend.plugins.base import DirectoryDiscoveryPlugin
from backend.extensions.products.config_validation import validate_discovery_config
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
            for p in self.config.get("diag_file_patterns", ["diag.zip", "diaglog_*.log.zip"])
        ]
        loose_cfg = self.config.get("loose_diagnostics", {})
        if not isinstance(loose_cfg, dict):
            loose_cfg = {}
        self._loose_diag_enabled = bool(loose_cfg.get("enabled", False))
        self._loose_diag_file_patterns = [
            glob_to_regex(p)
            for p in loose_cfg.get("file_patterns", [])
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
        diag_slots = self._scan_diag(extracted_root)
        self._merge_loose_diagnostic_logs(extracted_root, diag_slots)
        return (
            diag_slots,
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
            extracted_dir = f.parent / f"{f.name}_extracted"
            entry = LogEntry(
                path=str(f),
                name=f.name,
                size_bytes=f.stat().st_size,
                compressed=compressed,
                original_format=f.suffix if compressed else "",
                extracted_path=str(extracted_dir) if compressed and extracted_dir.is_dir() else "",
                dump_time=dump_time,
            )
            slot.add_diagnostic_log(entry)

    def _merge_loose_diagnostic_logs(
        self, extracted_root: Path, slots: list[SlotInfo],
    ) -> None:
        if (
            not self._loose_diag_enabled
            or not self._loose_diag_file_patterns
            or not extracted_root.exists()
        ):
            return

        seen = self._diagnostic_fingerprints(slots)
        loose_slot = SlotInfo(
            slot_id="loose",
            name="slot_loose",
            path=str(extracted_root),
        )
        for path in sorted(extracted_root.rglob("*")):
            if not path.is_file():
                continue
            if not self._match_any(path.name, self._loose_diag_file_patterns):
                continue
            entry = self._build_diag_log_entry(path)
            fingerprint = self._diagnostic_fingerprint(entry)
            if fingerprint and fingerprint in seen:
                continue
            if fingerprint:
                seen.add(fingerprint)
            loose_slot.add_diagnostic_log(entry)

        if loose_slot.diagnostic_logs:
            slots.append(loose_slot)

    def _diagnostic_fingerprints(self, slots: list[SlotInfo]) -> set[str]:
        fingerprints: set[str] = set()
        for slot in slots:
            for entry in slot.diagnostic_logs:
                fingerprint = self._diagnostic_fingerprint(entry)
                if fingerprint:
                    fingerprints.add(fingerprint)
        return fingerprints

    def _build_diag_log_entry(self, path: Path) -> LogEntry:
        dump_time = extract_dump_time(path.name, self._filename_ts_regex)
        compressed = self._is_compressed(path.name)
        extracted_dir = path.parent / f"{path.name}_extracted"
        return LogEntry(
            path=str(path),
            name=path.name,
            size_bytes=path.stat().st_size,
            compressed=compressed,
            original_format=path.suffix if compressed else "",
            extracted_path=str(extracted_dir) if compressed and extracted_dir.is_dir() else "",
            dump_time=dump_time,
        )

    @staticmethod
    def _diagnostic_fingerprint(entry: LogEntry) -> str:
        digest = hashlib.md5()
        saw_content = False
        if (
            entry.extracted_path
            or not entry.compressed
            or Path(entry.path).suffix.lower() == ".gz"
        ):
            for line in iter_log_entry_lines(entry):
                saw_content = True
                digest.update(line.encode("utf-8", errors="replace"))
                digest.update(b"\n")
            if saw_content:
                return digest.hexdigest()

        path = Path(entry.path)
        if not path.is_file():
            return ""
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    # ── private / varlog ──────────────────────────────────

    def _scan_private(self, extracted_root: Path) -> list[PrivateSlotInfo]:
        private_slots: list[PrivateSlotInfo] = []
        if not extracted_root.exists():
            return private_slots

        varlog_dir = self._find_dir(extracted_root, self.config.get("private_dir", "varlog"))
        if varlog_dir is None:
            return private_slots

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

            # Scanner only consumes directories prepared by the unified
            # decompression stage. It must not extract varlog.zip itself.
            for subdir in entry.iterdir():
                if subdir.is_dir():
                    self._scan_journal_in_dir(subdir, private_slot)

            private_slots.append(private_slot)

        return private_slots

    def _scan_journal_in_dir(
        self, dir_path: Path, private_slot: PrivateSlotInfo,
    ) -> None:
        for scan_root in self._journal_scan_roots(dir_path):
            for f in sorted(scan_root.rglob("*")):
                if not f.is_file():
                    continue
                if not self._match_any(f.name, self._journal_file_patterns):
                    continue
                seq = extract_journal_sequence(f.name, self._journal_seq_regex)
                if any(j.path == str(f) for j in private_slot.journal_logs):
                    continue
                private_slot.journal_logs.append(JournalLogFile(
                    path=str(f),
                    name=f.name,
                    size_bytes=f.stat().st_size,
                    compressed=self._is_compressed(f.name),
                    sequence=seq,
                ))

    @staticmethod
    def _journal_scan_roots(dir_path: Path) -> list[Path]:
        varlog_roots = [
            path
            for path in sorted(dir_path.rglob("*"))
            if path.is_dir() and path.name.lower().startswith("varlog")
        ]
        if varlog_roots:
            return varlog_roots
        return [dir_path]

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
