#!/usr/bin/env python3
"""生成 Compact 产品模拟日志包。

结构:
  compact_package_20260103.zip
  ├── boards/
  │   ├── slot_1/
  │   │   ├── debug_main.log
  │   │   └── debug_20260103.log
  │   └── slot_2/
  │       └── debug_20260103.log
  └── logs/
      ├── slot_1/
      │   └── syslog.log
      └── slot_2/
          └── syslog.log
"""

import shutil
import zipfile
from pathlib import Path


def create_zip_with_content(zip_path: Path, files: dict[str, str]) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for internal_path, content in files.items():
            zf.writestr(internal_path, content)


def main():
    base = Path(__file__).parent / "mock_data_compact"
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)

    # === slot_1 诊断日志 ===
    slot1_main = "\n".join([
        "2026-01-03T00:00:00 [BOOT] System boot on slot 1",
        "2026-01-03T00:01:00 COMPACT svc_master proc=svc_master-100 slot 1 cpu 0 |No[1] init complete",
        "2026-01-03T00:02:00 COMPACT svc_master proc=svc_master-100 slot 1 cpu 0 |No[2] heartbeat ok",
        "2026-01-03T01:00:00 [SLOT1] Periodic check passed",
        "2026-01-03T02:00:00 [SLOT1] Periodic check passed",
        "2026-01-03T03:00:00 COMPACT svc_master proc=svc_master-100 slot 1 cpu 0 |No[3] periodic check passed",
        "2026-01-03T04:00:00 [SLOT1] Periodic check passed",
        "2026-01-03T05:00:00 [SLOT1] Periodic check passed",
    ])
    slot1_debug = "\n".join([
        "2026-01-03T00:00:00 [SLOT1] ACTIVE master",
        "2026-01-03T00:05:00 [SLOT1] Interface UP: LPU-1",
        "2026-01-03T01:00:00 [SLOT1] Interface UP: LPU-2",
        "2026-01-03T02:00:00 [SLOT1] Health check OK",
        "2026-01-03T03:00:00 [SLOT1] Health check OK",
        "2026-01-03T04:00:00 [SLOT1] Health check OK",
        "2026-01-03T05:00:00 [SLOT1] Health check OK",
        "2026-01-03T06:00:00 [SLOT1] Health check OK",
        "2026-01-03T06:25:00 [SLOT1] Preparing maintenance mode",
    ])

    # === slot_2 诊断日志 ===
    slot2_debug = "\n".join([
        "2026-01-03T06:30:00 [SLOT2] Switchover: taking over as ACTIVE",
        "2026-01-03T06:30:01 COMPACT svc_master proc=svc_master-200 slot 2 cpu 0 |No[1] takeover started",
        "2026-01-03T06:31:00 COMPACT svc_master proc=svc_master-200 slot 2 cpu 0 |No[2] become active",
        "2026-01-03T06:35:00 [SLOT2] Periodic check passed",
        "2026-01-03T07:00:00 [SLOT2] Running as ACTIVE, CPU: 28%",
        "2026-01-03T07:30:00 [SLOT2] Health check OK",
        "2026-01-03T07:55:00 [SLOT2] Checkpoint saved",
    ])

    # === 私有日志 (syslog) ===
    slot1_syslog = "\n".join([
        "2026-01-03T00:00:01 syslog service started",
        "2026-01-03T00:01:01 syslog svc_master: COMPACT No[1] syslog entry slot1",
        "2026-01-03T00:02:01 syslog svc_master: COMPACT No[2] syslog entry slot1",
        "2026-01-03T06:25:00 syslog checkpoint",
    ])

    slot2_syslog = "\n".join([
        "2026-01-03T06:30:01 syslog service started",
        "2026-01-03T06:31:01 syslog svc_master: COMPACT No[1] syslog entry slot2",
        "2026-01-03T07:00:00 syslog checkpoint",
    ])

    # === 打包 ===
    outer_files = {
        "boards/slot_1/debug_main.log": slot1_main,
        "boards/slot_1/debug_20260103.log": slot1_debug,
        "boards/slot_2/debug_20260103.log": slot2_debug,
        "logs/slot_1/syslog.log": slot1_syslog,
        "logs/slot_2/syslog.log": slot2_syslog,
    }

    outer_zip_path = base / "compact_package_20260103.zip"
    create_zip_with_content(outer_zip_path, outer_files)

    print("Compact mock data generated:")
    print(f"  zip: {outer_zip_path}")
    print()
    for path, content in sorted(outer_files.items()):
        print(f"  {path} ({len(content)} bytes)")
    print()
    print("Expected:")
    print("  slot_1: ACTIVE, 1 period [00:00:00 ~ 06:25:00]")
    print("  slot_2: ACTIVE, 1 period [06:30:00 ~ 07:55:00]")
    print("  COMPACT module: slot_1 diag=3 + syslog=2, slot_2 diag=2 + syslog=1")


if __name__ == "__main__":
    main()
