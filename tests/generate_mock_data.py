#!/usr/bin/env python3
"""
生成模拟日志压缩包，匹配实际产品目录结构。

场景:
  slot_1 在 00:00~06:25 为主控，06:25 发生倒换
  slot_2 在 06:30 之后成为新主控

结构:
  diagnostic_information_20260103.zip
  └── diag/
      ├── slot_1/
      │   ├── diag.zip
      │   └── diaglog_1_20260103070000.log.zip  (转储时间 07:00)
      ├── slot_2/
      │   └── diaglog_2_20260103070000.log.zip  (转储时间 07:00)
      └── other_folder/  (应被忽略)
"""

import os
import shutil
import zipfile
from pathlib import Path


def gzip_compress(text: str) -> bytes:
    import gzip
    return gzip.compress(text.encode("utf-8"))


def create_zip_with_content(zip_path: Path, files: dict[str, str | bytes]) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for internal_path, content in files.items():
            if isinstance(content, bytes):
                zf.writestr(internal_path, content)
            else:
                zf.writestr(internal_path, content)


def main():
    base = Path(__file__).parent / "mock_data"
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)

    # === slot_1 诊断日志内容 ===
    # 时间戳覆盖 00:00~06:25，每 5 分钟一条
    slot1_log_lines = []
    for h in range(0, 7):
        for m in range(0, 60, 5):
            if h == 6 and m > 25:
                break
            slot1_log_lines.append(
                f"2026-01-03T{h:02d}:{m:02d}:00.123456 [SLOT_1] "
                f"System running as ACTIVE, CPU: 35%, MEM: 3.2G/8G"
            )
    slot1_log_lines.append(
        "2026-01-03T06:25:00.654321 [SLOT_1] Health check: preparing switchover..."
    )
    slot1_content = "\n".join(slot1_log_lines)

    # 机制模块诊断日志行（空格分隔时间戳，模拟实际日志格式）
    aaa_diag_lines = [
        "2026-01-03 00:01:00.100000 Service=SERVICE; Slot=1; CPU-Id=0; ProcessName=SERVICE-12345; Context=No[1] EXAMPLE init ok)",
        "2026-01-03 00:02:00.200000 Service=SERVICE; Slot=1; CPU-Id=0; ProcessName=SERVICE-12345; Context=No[2] EXAMPLE heartbeat)",
        "2026-01-03 06:00:00.300000 Service=SERVICE; Slot=1; CPU-Id=0; ProcessName=SERVICE-12345; Context=No[3] MASTER_CONFIRMED slot_1 is active)",
        "2026-01-03 06:30:00.400000 Service=SERVICE; Slot=2; CPU-Id=0; ProcessName=SERVICE-54321; Context=No[1] EXAMPLE takeover)",
        "2026-01-03 06:31:00.500000 Service=SERVICE; Slot=2; CPU-Id=0; ProcessName=SERVICE-54321; Context=No[2] MASTER_CONFIRMED slot_2 is active)",
    ]

    # diag.zip 中的内容（含 AAA 行）
    slot1_diag_content = (
        "2026-01-03T00:00:00.000001 [SLOT_1] System boot as ACTIVE master\n"
        + aaa_diag_lines[0] + "\n"
        + aaa_diag_lines[1] + "\n"
        "2026-01-03T00:05:00.100000 [SLOT_1] Board init OK\n"
        "2026-01-03T03:00:00.200000 [SLOT_1] Interface LPU-1 OK, LPU-2 OK\n"
        + aaa_diag_lines[2] + "\n"
        "2026-01-03T06:00:00.300000 [SLOT_1] Periodic check passed\n"
    )

    # diag.zip 内文件
    slot1_diag_files = {"slot_1/diag_raw.log": slot1_diag_content}
    slot1_diag_zip_path = base / "_tmp_slot1_diag.zip"
    create_zip_with_content(slot1_diag_zip_path, slot1_diag_files)

    # diaglog 内文件
    slot1_diaglog_files = {"slot_1/diaglog_full.log": slot1_content}
    slot1_diaglog_zip_path = base / "_tmp_diaglog_1_20260103070000.log.zip"
    create_zip_with_content(slot1_diaglog_zip_path, slot1_diaglog_files)

    # === slot_2 诊断日志内容 ===
    # 倒换后 06:30 开始，每 5 分钟一条
    slot2_log_lines = []
    slot2_log_lines.append(
        "2026-01-03T06:30:00.000001 [SLOT_2] Switchover detected, taking over as ACTIVE"
    )
    for h in range(6, 8):
        for m in range(0, 60, 5):
            if h == 6 and m < 35:
                continue
            slot2_log_lines.append(
                f"2026-01-03T{h:02d}:{m:02d}:00.123456 [SLOT_2] "
                f"Running as ACTIVE, CPU: 28%, MEM: 2.8G/8G"
            )
    # 插入 AAA 行
    slot2_log_lines.insert(0, aaa_diag_lines[3])
    slot2_log_lines.insert(1, aaa_diag_lines[4])
    slot2_content = "\n".join(slot2_log_lines)

    slot2_diaglog_files = {"slot_2/diaglog.log": slot2_content}
    slot2_diaglog_zip_path = base / "_tmp_diaglog_2_20260103070000.log.zip"
    create_zip_with_content(slot2_diaglog_zip_path, slot2_diaglog_files)

    # === varlog 私有日志 ===
    # slot_1 的 journal 日志（含 EXAMPLE 关键字用于 Stage1 预过滤）
    slot1_journal_current = (
        "2026-01-03T00:00:01 [JOURNAL] service started\n"
        "2026-01-03T00:01:00 [JOURNAL] SERVICE: No[1] EXAMPLE journal entry 1\n"
        "2026-01-03T00:02:00 [JOURNAL] SERVICE: No[2] EXAMPLE journal entry 2\n"
        "2026-01-03T06:25:00 [JOURNAL] checkpoint\n"
    )
    slot1_journal_hist1 = "2026-01-02T23:55:00 [JOURNAL] service stopped\n2026-01-02T23:59:59 [JOURNAL] shutdown\n"

    slot1_varlog_files = {
        "slot_1/varlog/journal.log": slot1_journal_current,
        "slot_1/varlog/journal.log.1.gz": gzip_compress(slot1_journal_hist1),
    }
    slot1_varlog_zip = base / "_tmp_varlog_slot1.zip"
    create_zip_with_content(slot1_varlog_zip, slot1_varlog_files)

    # slot_2 的 journal 日志
    slot2_journal_current = (
        "2026-01-03T06:30:01 [JOURNAL] service started\n"
        "2026-01-03T06:31:00 [JOURNAL] SERVICE: No[1] EXAMPLE journal slot2 entry 1\n"
        "2026-01-03T07:00:00 [JOURNAL] checkpoint\n"
    )
    slot2_varlog_files = {"slot_2/varlog/journal.log": slot2_journal_current}
    slot2_varlog_zip = base / "_tmp_varlog_slot2.zip"
    create_zip_with_content(slot2_varlog_zip, slot2_varlog_files)

    # slot_1_cpu_0 的 journal 日志（slot_1 的 CPU 子卡）
    slot1_cpu0_journal = "2026-01-03T00:00:01 [CPU0] cpu service started\n2026-01-03T06:00:00 [CPU0] cpu health OK\n"
    slot1_cpu0_varlog_files = {"slot_1_cpu_0/varlog/journal.log": slot1_cpu0_journal}
    slot1_cpu0_varlog_zip = base / "_tmp_varlog_slot1_cpu0.zip"
    create_zip_with_content(slot1_cpu0_varlog_zip, slot1_cpu0_varlog_files)

    # === 打包外层 zip ===
    outer_files = {}
    outer_files["diag/slot_1/diag.zip"] = slot1_diag_zip_path.read_bytes()
    outer_files["diag/slot_1/diaglog_1_20260103070000.log.zip"] = slot1_diaglog_zip_path.read_bytes()
    outer_files["diag/slot_2/diaglog_2_20260103070000.log.zip"] = slot2_diaglog_zip_path.read_bytes()
    outer_files["diag/other_folder/readme.txt"] = "Ignore this folder."
    outer_files["varlog/slot_1/varlog.zip"] = slot1_varlog_zip.read_bytes()
    outer_files["varlog/slot_2/varlog.zip"] = slot2_varlog_zip.read_bytes()
    outer_files["varlog/slot_1_cpu_0/varlog.zip"] = slot1_cpu0_varlog_zip.read_bytes()

    outer_zip_path = base / "diagnostic_information_20260103.zip"
    create_zip_with_content(outer_zip_path, outer_files)

    # 清理临时
    for tmp in [slot1_diag_zip_path, slot1_diaglog_zip_path, slot2_diaglog_zip_path,
                slot1_varlog_zip, slot2_varlog_zip, slot1_cpu0_varlog_zip]:
        tmp.unlink()

    # 同时创建 raw 目录用于直接查看
    raw_dir = base / "mock_raw"
    (raw_dir / "diag" / "slot_1").mkdir(parents=True)
    (raw_dir / "diag" / "slot_2").mkdir(parents=True)
    (raw_dir / "diag" / "other_folder").mkdir(parents=True)
    (raw_dir / "varlog" / "slot_1" / "varlog").mkdir(parents=True)
    (raw_dir / "varlog" / "slot_2" / "varlog").mkdir(parents=True)
    (raw_dir / "varlog" / "slot_1_cpu_0" / "varlog").mkdir(parents=True)
    (raw_dir / "diag" / "slot_1" / "diag_raw.log").write_text(slot1_diag_content, encoding="utf-8")
    (raw_dir / "diag" / "slot_1" / "diaglog_full.log").write_text(slot1_content, encoding="utf-8")
    (raw_dir / "diag" / "slot_2" / "diaglog.log").write_text(slot2_content, encoding="utf-8")
    (raw_dir / "varlog" / "slot_1" / "varlog" / "journal.log").write_text(slot1_journal_current, encoding="utf-8")
    (raw_dir / "varlog" / "slot_2" / "varlog" / "journal.log").write_text(slot2_journal_current, encoding="utf-8")
    (raw_dir / "varlog" / "slot_1_cpu_0" / "varlog" / "journal.log").write_text(slot1_cpu0_journal, encoding="utf-8")

    print("模拟数据已生成:")
    print(f"  压缩包: {outer_zip_path}")
    print(f"  原始目录: {raw_dir}")
    print()
    print("文件结构:")
    for path in sorted(outer_files.keys()):
        size = len(outer_files[path])
        print(f"  {path} ({size} bytes)")
    print()
    print(f"slot_1 日志内容时间戳: {len(slot1_log_lines) + 4} 条 (00:00:00 ~ 06:25:00)")
    print(f"slot_2 日志内容时间戳: {len(slot2_log_lines)} 条 (06:30:00 ~ 07:55:00)")
    print()
    print("预期解析结果:")
    print("  slot_1: ACTIVE, ActivePeriod 1 段 [00:00:00 ~ 06:25:00]")
    print("  slot_2: ACTIVE, ActivePeriod 1 段 [06:30:00 ~ 07:55:00]")
    print("  私有日志 slot_1: 2 个 journal 文件 (当前 + 历史#1)")
    print("  私有日志 slot_2: 1 个 journal 文件 (当前)")
    print("  私有日志 slot_1_cpu_0: 1 个 journal 文件 (CPU 子卡)")
    print("  机制模块 EXAMPLE 日志: slot_1 诊断3条 + journal2条, slot_2 诊断2条 + journal1条")


if __name__ == "__main__":
    main()
