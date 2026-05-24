"""流式文件读取：逐行迭代日志文件，避免一次性加载大文件到内存。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from backend.models import LogEntry


def iter_text_file_lines(path: Path, encoding: str = "utf-8") -> Iterable[str]:
    """逐行迭代文本文件。"""
    try:
        with path.open("r", encoding=encoding, errors="ignore") as f:
            for line in f:
                yield line.rstrip("\n")
    except OSError:
        return


def iter_log_entry_lines(log_entry: LogEntry) -> Iterable[str]:
    """逐行迭代 LogEntry，支持解压目录和单文件两种形式。"""
    if log_entry.extracted_path:
        ext_dir = Path(log_entry.extracted_path)
        if ext_dir.is_dir():
            for f in sorted(ext_dir.rglob("*")):
                if f.is_file():
                    yield from iter_text_file_lines(f)
            return

    file_path = Path(log_entry.path)
    if file_path.is_file():
        yield from iter_text_file_lines(file_path)
