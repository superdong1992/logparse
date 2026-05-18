"""纯函数工具，供插件和核心框架复用，不依赖 ConfigLoader。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


def glob_to_regex(pattern: str) -> re.Pattern:
    """将 glob 模式编译为正则。* -> .*  ? -> .  大小写不敏感。"""
    regex = re.escape(pattern)
    regex = regex.replace(r"\*", ".*")
    regex = regex.replace(r"\?", ".")
    return re.compile(f"^{regex}$", re.IGNORECASE)


def extract_slot_id(dir_name: str) -> str:
    """从目录名提取 slot ID。'slot_1' -> '1'。"""
    match = re.match(r"slot_(.+)", dir_name, re.IGNORECASE)
    return match.group(1) if match else dir_name


def extract_private_slot_info(dir_name: str) -> tuple[str, str | None]:
    """从 varlog 目录名提取 (slot_id, cpu_id)。
    'slot_1' -> ('1', None)
    'slot_1_cpu_2' -> ('1', '2')
    """
    match = re.match(r"slot_(.+?)_cpu_(.+)", dir_name, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2)
    match = re.match(r"slot_(.+)", dir_name, re.IGNORECASE)
    if match:
        return match.group(1), None
    return dir_name, None


def extract_dump_time(
    filename: str, filename_ts_regex: re.Pattern,
) -> datetime | None:
    """从诊断日志文件名提取转储时间戳。"""
    match = filename_ts_regex.match(filename)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def extract_journal_sequence(
    filename: str, seq_regex: re.Pattern,
) -> int:
    """从 journal 文件名提取序号。0=当前，N=历史轮转。"""
    match = seq_regex.match(filename)
    if not match:
        return 0
    seq_str = match.group(1)
    try:
        return int(seq_str) if seq_str else 0
    except ValueError:
        return 0


def extract_content_timestamps(
    text: str, ts_regex: re.Pattern,
) -> list[datetime]:
    """从日志文本中提取所有时间戳（含可选时区偏移）。"""
    stamps: list[datetime] = []
    for m in ts_regex.finditer(text):
        ts_str = m.group(1)
        tz_str = m.group(2)
        if tz_str:
            ts_str = ts_str + tz_str
        try:
            stamps.append(datetime.fromisoformat(ts_str))
        except ValueError:
            continue
    return stamps


def is_compressed(name: str, extensions: list[str]) -> bool:
    """检查文件名是否属于已知压缩格式。"""
    name_lower = name.lower()
    for ext in extensions:
        if name_lower.endswith(ext):
            return True
    return False


def read_text_file(file_path: Path) -> str:
    """读取文本文件，UTF-8 优先，GBK 兜底，都失败返回空串。"""
    try:
        return file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        try:
            return file_path.read_text(encoding="gbk", errors="replace")
        except Exception:
            return ""
