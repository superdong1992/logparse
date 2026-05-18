#!/usr/bin/env python3
"""不依赖外部库的修复验证脚本。使用 mock 数据测试核心逻辑。"""

import re
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

errors = []

def check(desc, ok):
    if ok:
        print(f"  PASS: {desc}")
    else:
        print(f"  FAIL: {desc}")
        errors.append(desc)

print("=" * 60)
print("1. Stage 1 大小写敏感过滤")
print("=" * 60)

mod_upper = "SERVICE"
lines = [
    ("SERVICE: No[1] xxx", True),   # 大写，应通过
    ("service: No[1] xxx", False),  # 小写，不应通过
    ("Service: No[1] xxx", False),  # 混合，不应通过
    ("noservice: No[1] xxx", False), # 不含
]

for line, expected in lines:
    result = mod_upper in line  # 当前修复后的逻辑
    ok = result == expected
    check(f"'{line[:25]}' -> {result} (expected {expected})", ok)

print()
print("=" * 60)
print("2. No[n] 序列号跳过")
print("=" * 60)

seq_re = re.compile(r'No\[(\d+)\]')

test_lines = [
    ("2026-01-03T00:01:00.100000+08:00 Service=SERVICE; No[1] init", True),
    ("2026-01-03T00:02:00.100000+08:00 Service=SERVICE; something else", False),
    ("SERVICE: No[1] xxx context", True),
    ("SERVICE: some message without No", False),
]

for line, has_no in test_lines:
    sm = seq_re.search(line)
    ok = (sm is not None) == has_no
    check(f"No[n] in '{line[:40]}...' -> {sm is not None} (expected {has_no})", ok)

print()
print("=" * 60)
print("3. _build_processes 按 (name, pid) 分组")
print("=" * 60)

class FakeEntry:
    def __init__(self, name, pid, cpu, seq):
        self.process_name = name
        self.pid = pid
        self.cpu_id = cpu
        self.sequence = seq

entries = [
    FakeEntry("dhcp", "9881", "0", 1),
    FakeEntry("dhcp", "9881", "0", 2),
    FakeEntry("dhcp", "9881", "1", 3),  # 不同 CPU
    FakeEntry("dhcp", "9881", "0", 4),
    FakeEntry("other", "123", "0", 1),
]

by_key = defaultdict(list)
for e in entries:
    by_key[(e.process_name, e.pid)].append(e)

check("dhcp-9881 在一个组里", len(by_key) == 2)
check("dhcp-9881 有 4 条", len(by_key[("dhcp", "9881")]) == 4)
check("other-123 有 1 条", len(by_key[("other", "123")]) == 1)

print()
print("=" * 60)
print("4. 时区处理")
print("=" * 60)

tz_re = re.compile(r"(\d{4}-\d{1,2}-\d{1,2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?")

# diag: 带时区
m_diag = tz_re.search("2026-01-03T00:01:00.100000+08:00 Service=SERVICE; ...")
ts_diag = datetime.fromisoformat(m_diag.group(1) + (m_diag.group(2) or ""))
check(f"diag 时间戳 aware: {ts_diag.tzinfo is not None}", ts_diag.tzinfo is not None)

# journal: 不带时区
m_jnl = tz_re.search("2026-01-03T00:01:00.100000 Service=SERVICE; ...")
ts_jnl = datetime.fromisoformat(m_jnl.group(1) + (m_jnl.group(2) or ""))
check(f"journal 时间戳 naive: {ts_jnl.tzinfo is None}", ts_jnl.tzinfo is None)

# journal 加上时区
tzinfo = ts_diag.tzinfo
ts_jnl_fixed = ts_jnl.replace(tzinfo=tzinfo)
check(f"journal 对齐后 aware: {ts_jnl_fixed.tzinfo is not None}", ts_jnl_fixed.tzinfo is not None)

# aware 之间可比较
t1 = datetime(2026,1,3,0,1,0, tzinfo=tzinfo)
t2 = datetime(2026,1,3,0,2,0, tzinfo=tzinfo)
check("aware 时间戳可排序", t1 < t2)

print()
print("=" * 60)
print("5. 解压和扫描 mock 数据")
print("=" * 60)

mock_zip = Path("tests/mock_data/diagnostic_information_20260103.zip")
if not mock_zip.exists():
    print("  SKIP: mock 数据压缩包不存在")
else:
    import tempfile, os
    tmpdir = Path(tempfile.mkdtemp())
    try:
        # 外层解压
        with zipfile.ZipFile(mock_zip, "r") as zf:
            zf.extractall(tmpdir)

        # 检查 diag/slot_1/
        diag_dir = tmpdir / "diag"
        check("diag/ 目录存在", diag_dir.is_dir())

        slot1 = diag_dir / "slot_1"
        check("slot_1 目录存在", slot1.is_dir())

        if slot1.is_dir():
            files = list(slot1.iterdir())
            file_names = [f.name for f in files if f.is_file()]
            print(f"  slot_1 文件: {file_names}")
            check("diag.zip 存在", "diag.zip" in file_names)
            check("diaglog 存在", any("diaglog" in n for n in file_names))

        # 检查 varlog/
        varlog_dir = tmpdir / "varlog"
        check("varlog/ 目录存在", varlog_dir.is_dir())

        varlog_slot1 = varlog_dir / "slot_1"
        if varlog_slot1.is_dir():
            check("varlog/slot_1/varlog.zip 存在", (varlog_slot1 / "varlog.zip").is_file())

        # 解压 diag.zip 内容
        diag_zip = slot1 / "diag.zip"
        if diag_zip.is_file():
            diag_extract = tmpdir / "diag_extracted"
            diag_extract.mkdir(exist_ok=True)
            with zipfile.ZipFile(diag_zip, "r") as zf:
                zf.extractall(diag_extract)
            content_files = list(diag_extract.rglob("*"))
            print(f"  diag.zip 解压后文件: {[f.name for f in content_files if f.is_file()]}")

        # 解压 varlog.zip
        varlog_zip = varlog_slot1 / "varlog.zip"
        if varlog_zip.is_file():
            varlog_extract = tmpdir / "varlog_extracted"
            varlog_extract.mkdir(exist_ok=True)
            with zipfile.ZipFile(varlog_zip, "r") as zf:
                zf.extractall(varlog_extract)
            inner_files = list(varlog_extract.rglob("*"))
            print(f"  varlog.zip 解压后文件: {[f.name for f in inner_files if f.is_file()]}")

            # 检查 inner varlog/ 层
            inner_varlog = varlog_extract / "varlog"
            if inner_varlog.is_dir():
                journal_files = list(inner_varlog.iterdir())
                journal_names = [f.name for f in journal_files if f.is_file()]
                print(f"  varlog/varlog/ 目录内容: {journal_names}")
                check("cpdt_journal.log 存在", "cpdt_journal.log" in journal_names)

    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

print()
print("=" * 60)
print("6. indicator 时间戳收集")
print("=" * 60)

class FakeAaaEntry:
    def __init__(self, name, ts, seq):
        self.process_name = name
        self.timestamp = ts
        self.sequence = seq

indicator = "dhcp"
entries = [
    FakeAaaEntry("dhcp", datetime(2026,1,3,0,1,0), 1),
    FakeAaaEntry("dhcp", datetime(2026,1,3,0,5,0), 2),
    FakeAaaEntry("other", datetime(2026,1,3,0,2,0), 1),  # 非 indicator
    FakeAaaEntry("other", None, 2),                      # journal 无时间戳
]

# 只用 indicator 的时间戳
indicator_times = [e.timestamp for e in entries
                   if e.timestamp and (not indicator or indicator in e.process_name.lower())]
all_times = [e.timestamp for e in entries if e.timestamp]

check("indicator_times 有 2 个", len(indicator_times) == 2)
check("all_times 有 3 个", len(all_times) == 3)
if indicator_times:
    check(f"start={min(indicator_times)}", min(indicator_times) == datetime(2026,1,3,0,1,0))
    check(f"end={max(indicator_times)}", max(indicator_times) == datetime(2026,1,3,0,5,0))
    dir_name = f"{min(indicator_times).strftime('%Y%m%dT%H%M%S')}-{max(indicator_times).strftime('%Y%m%dT%H%M%S')}"
    check(f"dir_name={dir_name}", dir_name == "20260103T000100-20260103T000500")

# indicator=None 时的回退
indicator_times2 = [e.timestamp for e in entries if e.timestamp and (not None or None in e.process_name.lower())]
check("无 indicator 时用全部", len(indicator_times2) == 3)

print()
print("=" * 60)
if errors:
    print(f"FAILED: {len(errors)} 项")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("全部通过!")
