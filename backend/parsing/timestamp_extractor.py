"""时间戳提取：从文本、文件、LogEntry 中提取内容时间戳。"""
from __future__ import annotations

import gzip
import logging
import re
from datetime import datetime
from pathlib import Path

from backend.models import LogEntry

logger = logging.getLogger(__name__)


class TimestampExtractor:
    def __init__(self, ts_regex: re.Pattern):
        self._ts_regex = ts_regex

    def extract_from_text(self, text: str) -> list[datetime]:
        stamps: list[datetime] = []
        for m in self._ts_regex.finditer(text):
            ts_str = m.group(1)
            tz_str = m.group(2)
            if tz_str:
                ts_str = ts_str + tz_str
            try:
                stamps.append(datetime.fromisoformat(ts_str))
            except ValueError:
                continue
        return stamps

    def extract_from_file(self, file_path: Path) -> list[datetime]:
        stamps: list[datetime] = []
        try:
            if file_path.suffix == ".gz":
                with gzip.open(file_path, "rt", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        stamps.extend(self.extract_from_text(line))
            else:
                with file_path.open("r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        stamps.extend(self.extract_from_text(line))
        except Exception:
            try:
                with file_path.open("r", encoding="gbk", errors="replace") as fh:
                    for line in fh:
                        stamps.extend(self.extract_from_text(line))
            except Exception:
                logger.warning("无法读取文件 (UTF-8/GBK 均失败): %s", file_path)
        return stamps

    def extract_from_entry(self, entry: LogEntry) -> list[datetime]:
        stamps: list[datetime] = []
        if entry.extracted_path:
            ext_dir = Path(entry.extracted_path)
            if ext_dir.is_dir():
                for f in sorted(ext_dir.rglob("*")):
                    if f.is_file():
                        stamps.extend(self.extract_from_file(f))
                return sorted(stamps)
        file_path = Path(entry.path)
        if file_path.is_file():
            return sorted(self.extract_from_file(file_path))
        return stamps

    @staticmethod
    def _read_file(file_path: Path) -> str:
        if not file_path.exists():
            return ""
        try:
            if file_path.suffix == ".gz":
                try:
                    with gzip.open(file_path, "rt", encoding="utf-8", errors="replace") as fh:
                        return fh.read()
                except Exception:
                    logger.warning("gzip 解压失败，跳过: %s", file_path)
                    return ""
            return file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            try:
                return file_path.read_text(encoding="gbk", errors="replace")
            except Exception:
                logger.warning("无法读取文件 (UTF-8/GBK 均失败): %s", file_path)
                return ""
