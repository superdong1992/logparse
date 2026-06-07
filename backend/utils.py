"""纯函数工具，供插件和核心框架复用，不依赖 ConfigLoader。"""

from __future__ import annotations

import re
import hashlib
from datetime import datetime


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


def safe_path_segment(value: object) -> str:
    """Return a deterministic, collision-resistant path segment."""
    text = "" if value is None else str(value)
    if not text:
        return "unknown"
    encoded = "".join(
        ch
        if ch.isascii() and (ch.isalnum() or ch in "_-")
        else f"~U{ord(ch):08x}"
        for ch in text
    )
    if any(not (ch.isascii() and (ch.isdigit() or ch in "_-")) for ch in text):
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        encoded = f"{encoded}~H{digest}"
    return encoded or "unknown"


def safe_log_filename(process_name: object, pid: object = "") -> str:
    """Return the mechanism log filename for a process lifecycle."""
    name = safe_path_segment(process_name)
    pid_text = "" if pid is None else str(pid)
    if pid_text:
        return f"{name}~P{safe_path_segment(pid_text)}.log"
    return f"{name}.log"


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


def is_compressed(name: str, extensions: list[str]) -> bool:
    """检查文件名是否属于已知压缩格式。"""
    name_lower = name.lower()
    for ext in extensions:
        if name_lower.endswith(ext):
            return True
    return False
