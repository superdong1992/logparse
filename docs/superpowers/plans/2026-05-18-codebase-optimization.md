# Codebase Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add unit tests, migrate to new pipeline as default, split ParserPlugin into focused components, clean up dead code.

**Architecture:** Four sequential phases: P0 (tests for safety net) -> P1 (new pipeline default + delete legacy) -> P2 (split ParserPlugin into 4 components) -> P3 (small cleanup). Each phase produces working, testable state.

**Tech Stack:** Python 3.10+, pytest, Pydantic v2, Click, PyYAML

---

## File Structure

### New Files (P0: Tests)
- `tests/__init__.py` — empty package marker
- `tests/conftest.py` — shared pytest fixtures (sample ParseResult, sample config dicts)
- `tests/test_utils.py` — tests for `backend/utils.py`
- `tests/test_decompressor.py` — tests for `backend/decompressor.py`
- `tests/test_parser_plugin.py` — tests for `backend/plugins/default/parser.py`
- `tests/test_plugin_loader.py` — tests for `backend/plugins/loader.py`
- `tests/test_scanner_plugin.py` — tests for `backend/plugins/default/scanner.py`

### Files Modified (P1: Pipeline Switch)
- `requirements.txt` — add pytest
- `cli.py` — default to `--product default`, remove `_parse_legacy`, remove legacy imports
- `config.yaml` — remove legacy section
- `backend/metadata.py` — fix `aaa_results` key to `mech_results`

### Files Deleted (P1: Legacy Removal)
- `backend/scanner.py`
- `backend/mech_parser.py`
- `backend/log_parser.py`
- `backend/identifier.py`
- `backend/config.py`

### New Files (P2: ParserPlugin Split)
- `backend/parsing/timestamp_extractor.py` — timestamp extraction from files/entries
- `backend/parsing/cycle_detector.py` — reboot cycle detection (PID + sequence rollback)
- `backend/parsing/role_identifier.py` — board role determination (mech + fallback)
- `backend/parsing/output_writer.py` — three-level disk output
- `backend/parsing/__init__.py` — re-exports

### Files Modified (P2)
- `backend/plugins/default/parser.py` — slimmed to orchestrator calling the 4 components

### Files Modified (P3)
- `backend/pipeline.py` — remove dead code after return

---

## Phase P0: Unit Tests (Safety Net)

### Task 1: Test Infrastructure Setup

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Add pytest to requirements.txt**

Add `pytest>=7.0` to `requirements.txt`:

```
pyyaml>=6.0
pydantic>=2.0.0
click>=8.1.0
pytest>=7.0
```

- [ ] **Step 2: Install dependencies**

Run: `pip install -r requirements.txt`
Expected: All packages installed successfully

- [ ] **Step 3: Create tests/__init__.py**

Create empty file `tests/__init__.py`:

```python
```

- [ ] **Step 4: Create tests/conftest.py with shared fixtures**

```python
"""Shared pytest fixtures."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from backend.models import (
    ActivePeriod,
    BoardRole,
    LogEntry,
    MechLogEntry,
    ParseResult,
    PrivateSlotInfo,
    SlotInfo,
)


@pytest.fixture
def sample_config() -> dict:
    """Minimal parser config matching config.yaml structure."""
    return {
        "timestamp_regex": r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?",
        "active_period_gap_threshold": 300,
        "mechanism_modules": {
            "module1": {
                "module_name": "EXAMPLE",
                "enabled": True,
                "diag_pattern": r"Service=(?P<Service>[^;]+).*?Slot=(?P<Slot>[^;,)]+).*?CPU-Id=(?P<CPU_Id>[^;,)]+).*?ProcessName=(?P<ProcessName>[^;,)]+).*?Context=(?P<Context>.+?)\)$",
                "active_master_keyword": "MASTER_ACTIVE",
                "board_restart_indicator": "dhcp",
                "process_name_mapping": {},
                "journal": {
                    "line_pattern": r"^\S+\s+\S+\s+\S+?:\s+\[slotId\s*=\s*\d+,\s*cpuId\s*=\s*\d+,\s*processName\s*=\s*(\S+?)-(\d+)\]:\s+No\[(\d+)\](.+)$",
                    "line_pattern2": r"^\S+\s+\S+\s+(\S+?)(?:-(\d+))?:\s+No\[(\d+)\](.+)$",
                    "identifying_keyword": "EXAMPLE",
                },
                "sequence_pattern": r"No\[(\d+)\]",
            }
        },
    }


@pytest.fixture
def sample_slot() -> SlotInfo:
    """A slot with two diagnostic log entries."""
    slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp/slot_1")
    slot.add_diagnostic_log(LogEntry(
        path="/tmp/slot_1/diag.zip", name="diag.zip",
        size_bytes=1024, compressed=True,
    ))
    slot.add_diagnostic_log(LogEntry(
        path="/tmp/slot_1/diaglog_1_20260103000000.log.zip",
        name="diaglog_1_20260103000000.log.zip",
        size_bytes=2048, compressed=True,
    ))
    return slot


@pytest.fixture
def sample_parse_result(sample_slot) -> ParseResult:
    """Minimal ParseResult with one diagnostic slot."""
    return ParseResult(
        task_id="test_task",
        package_name="test.zip",
        extracted_root="/tmp/extracted",
        diagnostic_slots=[sample_slot],
        private_slots=[],
    )


@pytest.fixture
def tz_east8() -> timezone:
    return timezone(timedelta(hours=8))


@pytest.fixture
def sample_mech_entries(tz_east8) -> list[MechLogEntry]:
    """10 journal entries from a single process with a PID change at index 5."""
    entries = []
    base = datetime(2026, 1, 3, 0, 0, 0, tzinfo=tz_east8)
    # First lifecycle: dhcp PID=100, seq 1-5
    for i in range(1, 6):
        entries.append(MechLogEntry(
            timestamp=base + timedelta(minutes=i),
            source="journal", source_file="slot_1/journal.log",
            slot="1", cpu_id="",
            process_name="dhcp", pid="100",
            context="EXAMPLE msg", sequence=i,
            raw=f"Jan  3 00:{i:02d}:00 dhcp-100: No[{i}] EXAMPLE msg",
        ))
    # Second lifecycle: dhcp PID=200 (restart), seq 1-5
    for i in range(1, 6):
        entries.append(MechLogEntry(
            timestamp=base + timedelta(hours=1, minutes=i),
            source="journal", source_file="slot_1/journal.log",
            slot="1", cpu_id="",
            process_name="dhcp", pid="200",
            context="EXAMPLE msg", sequence=i,
            raw=f"Jan  3 01:{i:02d}:00 dhcp-200: No[{i}] EXAMPLE msg",
        ))
    return entries
```

- [ ] **Step 5: Verify pytest runs**

Run: `python -m pytest tests/ -v --co`
Expected: No collection errors (tests discovered or "no tests collected")

- [ ] **Step 6: Commit**

```bash
git add requirements.txt tests/__init__.py tests/conftest.py
git commit -m "test: add pytest infrastructure and shared fixtures"
```

---

### Task 2: Test backend/utils.py

**Files:**
- Create: `tests/test_utils.py`

- [ ] **Step 1: Write tests for all utils functions**

```python
"""Tests for backend/utils.py pure functions."""
from __future__ import annotations

import re
from datetime import datetime

import pytest

from backend.utils import (
    extract_content_timestamps,
    extract_dump_time,
    extract_journal_sequence,
    extract_private_slot_info,
    extract_slot_id,
    glob_to_regex,
    is_compressed,
)


class TestGlobToRegex:
    def test_star_matches_any(self):
        pat = glob_to_regex("slot_*")
        assert pat.match("slot_1")
        assert pat.match("slot_abc")
        assert not pat.match("slot")

    def test_question_mark_matches_one(self):
        pat = glob_to_regex("file_?.log")
        assert pat.match("file_1.log")
        assert not pat.match("file_12.log")

    def test_case_insensitive(self):
        pat = glob_to_regex("diag.zip")
        assert pat.match("DIAG.ZIP")

    def test_literal_match(self):
        pat = glob_to_regex("journal.log")
        assert pat.match("journal.log")
        assert not pat.match("journal.log.1")


class TestExtractSlotId:
    def test_simple(self):
        assert extract_slot_id("slot_1") == "1"

    def test_with_cpu(self):
        assert extract_slot_id("slot_1_cpu_2") == "1_cpu_2"

    def test_passthrough(self):
        assert extract_slot_id("other") == "other"


class TestExtractPrivateSlotInfo:
    def test_board_slot(self):
        slot_id, cpu_id = extract_private_slot_info("slot_1")
        assert slot_id == "1"
        assert cpu_id is None

    def test_cpu_subcard(self):
        slot_id, cpu_id = extract_private_slot_info("slot_1_cpu_2")
        assert slot_id == "1"
        assert cpu_id == "2"

    def test_unknown(self):
        slot_id, cpu_id = extract_private_slot_info("other")
        assert slot_id == "other"
        assert cpu_id is None


class TestExtractDumpTime:
    def test_valid_filename(self):
        regex = re.compile(r".*_(\d{14})\..*")
        dt = extract_dump_time("diaglog_1_20260103000000.log.zip", regex)
        assert dt == datetime(2026, 1, 3, 0, 0, 0)

    def test_no_match(self):
        regex = re.compile(r".*_(\d{14})\..*")
        assert extract_dump_time("diag.zip", regex) is None


class TestExtractJournalSequence:
    def test_current(self):
        regex = re.compile(r"journal\.log(?:\.(\d+))?(?:\.gz)?", re.IGNORECASE)
        assert extract_journal_sequence("journal.log", regex) == 0

    def test_history(self):
        regex = re.compile(r"journal\.log(?:\.(\d+))?(?:\.gz)?", re.IGNORECASE)
        assert extract_journal_sequence("journal.log.3", regex) == 3

    def test_gz(self):
        regex = re.compile(r"journal\.log(?:\.(\d+))?(?:\.gz)?", re.IGNORECASE)
        assert extract_journal_sequence("journal.log.1.gz", regex) == 1


class TestExtractContentTimestamps:
    def test_with_timezone(self):
        regex = re.compile(
            r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?"
        )
        stamps = extract_content_timestamps(
            "2026-01-03T00:01:00.100000+08:00 some log line", regex
        )
        assert len(stamps) == 1
        assert stamps[0].tzinfo is not None

    def test_without_timezone(self):
        regex = re.compile(
            r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?"
        )
        stamps = extract_content_timestamps(
            "2026-01-03T00:01:00.100000 some log line", regex
        )
        assert len(stamps) == 1
        assert stamps[0].tzinfo is None

    def test_multiple_timestamps(self):
        regex = re.compile(
            r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?"
        )
        stamps = extract_content_timestamps(
            "2026-01-03T00:01:00 first\n2026-01-03T00:02:00+08:00 second", regex
        )
        assert len(stamps) == 2


class TestIsCompressed:
    EXTS = [".gz", ".zip", ".tar.gz", ".tgz", ".tar"]

    def test_zip(self):
        assert is_compressed("diag.zip", self.EXTS)

    def test_gz(self):
        assert is_compressed("journal.log.1.gz", self.EXTS)

    def test_not_compressed(self):
        assert not is_compressed("debug.log", self.EXTS)

    def test_case_insensitive(self):
        assert is_compressed("FILE.ZIP", self.EXTS)
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_utils.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_utils.py
git commit -m "test: add unit tests for backend/utils.py"
```

---

### Task 3: Test Decompressor (Security + Extraction)

**Files:**
- Create: `tests/test_decompressor.py`

- [ ] **Step 1: Write Decompressor tests**

```python
"""Tests for backend/decompressor.py."""
from __future__ import annotations

import gzip
import tarfile
import zipfile
from pathlib import Path

import pytest

from backend.decompressor import Decompressor, MAX_UNCOMPRESSED_SIZE


@pytest.fixture
def decompressor():
    return Decompressor()


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


def _create_zip(path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def _create_tar_gz(path: Path, files: dict[str, str]) -> None:
    with tarfile.open(path, "w:gz") as tf:
        import io
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content.encode())
            tf.addfile(info, io.BytesIO(content.encode()))


class TestIsCompressed:
    def test_zip(self, decompressor):
        assert decompressor.is_compressed("file.zip")

    def test_tar_gz(self, decompressor):
        assert decompressor.is_compressed("file.tar.gz")

    def test_plain(self, decompressor):
        assert not decompressor.is_compressed("file.log")


class TestSafePath:
    def test_normal_path(self):
        assert Decompressor._is_safe_path("dir/file.txt")

    def test_path_traversal(self):
        assert not Decompressor._is_safe_path("../etc/passwd")

    def test_absolute_unix(self):
        assert not Decompressor._is_safe_path("/etc/passwd")

    def test_absolute_windows(self):
        assert not Decompressor._is_safe_path("C:\\Windows\\system32")

    def test_nested_traversal(self):
        assert not Decompressor._is_safe_path("dir/../../etc/passwd")


class TestCheckZipBomb:
    def test_safe_file(self):
        assert Decompressor._check_zip_bomb(100, 1000, "safe.txt")

    def test_oversized_file(self):
        assert not Decompressor._check_zip_bomb(100, MAX_UNCOMPRESSED_SIZE + 1, "big.txt")

    def test_high_compression_ratio(self):
        assert not Decompressor._check_zip_bomb(1, 200, "bomb.txt")

    def test_zero_compressed(self):
        assert Decompressor._check_zip_bomb(0, 1000, "ok.txt")


class TestExtractZip:
    def test_basic_extraction(self, decompressor, tmp_dir):
        zip_path = tmp_dir / "test.zip"
        _create_zip(zip_path, {"hello.txt": "world"})

        dest = tmp_dir / "out"
        extracted = []
        decompressor._extract_zip(zip_path, dest, extracted)

        assert (dest / "hello.txt").read_text() == "world"
        assert len(extracted) == 1

    def test_skips_path_traversal(self, decompressor, tmp_dir):
        zip_path = tmp_dir / "evil.zip"
        _create_zip(zip_path, {"../escape.txt": "evil", "safe.txt": "ok"})

        dest = tmp_dir / "out"
        extracted = []
        decompressor._extract_zip(zip_path, dest, extracted)

        assert (dest / "safe.txt").exists()
        assert not (tmp_dir / "escape.txt").exists()
        assert len(extracted) == 1

    def test_skips_directories(self, decompressor, tmp_dir):
        zip_path = tmp_dir / "test.zip"
        _create_zip(zip_path, {"subdir/": ""})

        dest = tmp_dir / "out"
        extracted = []
        decompressor._extract_zip(zip_path, dest, extracted)

        assert len(extracted) == 0


class TestExtractGz:
    def test_basic_gz(self, decompressor, tmp_dir):
        gz_path = tmp_dir / "test.log.gz"
        with gzip.open(gz_path, "wb") as f:
            f.write(b"hello world")

        dest = tmp_dir / "out"
        extracted = []
        decompressor._extract_gz(gz_path, dest, extracted)

        out_file = dest / "test.log"
        assert out_file.read_text() == "hello world"
        assert len(extracted) == 1


class TestExtractAll:
    def test_non_recursive(self, decompressor, tmp_dir):
        zip_path = tmp_dir / "outer.zip"
        _create_zip(zip_path, {"inner.zip": "not-a-real-zip"})

        dest = tmp_dir / "out"
        decompressor.extract_all(zip_path, dest, recursive=False)

        # inner.zip should still exist (not recursively extracted)
        assert (dest / "inner.zip").exists()

    def test_recursive(self, decompressor, tmp_dir):
        inner_zip = tmp_dir / "inner.zip"
        _create_zip(inner_zip, {"data.txt": "content"})

        outer_zip = tmp_dir / "outer.zip"
        with zipfile.ZipFile(outer_zip, "w") as zf:
            zf.write(inner_zip, "inner.zip")

        dest = tmp_dir / "out"
        decompressor.extract_all(outer_zip, dest, recursive=True)

        # After recursive extraction, inner.zip should be extracted and removed
        inner_extracted = dest / "inner.zip_extracted"
        assert (inner_extracted / "data.txt").read_text() == "content"

    def test_empty_file_skipped(self, decompressor, tmp_dir):
        zip_path = tmp_dir / "empty.zip"
        zip_path.write_bytes(b"")

        dest = tmp_dir / "out"
        extracted = decompressor.extract_all(zip_path, dest)
        assert extracted == []
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_decompressor.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_decompressor.py
git commit -m "test: add unit tests for Decompressor (security + extraction)"
```

---

### Task 4: Test ParserPlugin Core Logic

**Files:**
- Create: `tests/test_parser_plugin.py`

- [ ] **Step 1: Write ParserPlugin tests**

```python
"""Tests for ParserPlugin: timestamps, ActivePeriod, cycle detection, role identification."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.models import (
    ActivePeriod,
    BoardRole,
    LogEntry,
    MechBoardCycle,
    MechLogEntry,
    MechProcessLifecycle,
    MechResult,
    ParseResult,
    PrivateSlotInfo,
    SlotInfo,
)
from backend.plugins.default.parser import ParserPlugin, SEQ_ROLLBACK_THRESHOLD


@pytest.fixture
def plugin(sample_config):
    return ParserPlugin(sample_config)


class TestExtractContentTimestamps:
    def test_with_tz(self, plugin):
        stamps = plugin._extract_content_timestamps(
            "2026-01-03T00:01:00.100000+08:00 EXAMPLE msg"
        )
        assert len(stamps) == 1
        assert stamps[0].tzinfo is not None

    def test_without_tz(self, plugin):
        stamps = plugin._extract_content_timestamps(
            "2026-01-03T00:01:00 EXAMPLE msg"
        )
        assert len(stamps) == 1
        assert stamps[0].tzinfo is None

    def test_empty(self, plugin):
        assert plugin._extract_content_timestamps("no timestamp here") == []


class TestBuildActivePeriods:
    def test_single_period(self, plugin):
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        base = datetime(2026, 1, 3, 0, 0, 0)
        entry = LogEntry(path="/tmp/f", name="f.log", size_bytes=100)
        entry.content_timestamps = [base + timedelta(minutes=i) for i in range(5)]
        slot.add_diagnostic_log(entry)

        periods = plugin._build_active_periods(slot)
        assert len(periods) == 1
        assert periods[0].start == base
        assert periods[0].end == base + timedelta(minutes=4)

    def test_gap_creates_two_periods(self, plugin):
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        base = datetime(2026, 1, 3, 0, 0, 0)
        entry = LogEntry(path="/tmp/f", name="f.log", size_bytes=100)
        # 5 minutes within, then 10 minute gap (threshold=300s=5min)
        entry.content_timestamps = [
            base,
            base + timedelta(minutes=4),
            # gap: 10 minutes > 5min threshold
            base + timedelta(minutes=14),
            base + timedelta(minutes=18),
        ]
        slot.add_diagnostic_log(entry)

        periods = plugin._build_active_periods(slot)
        assert len(periods) == 2

    def test_empty_slot(self, plugin):
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        assert plugin._build_active_periods(slot) == []


class TestParseDiagProcName:
    def test_simple_name_with_pid(self):
        assert ParserPlugin._parse_diag_proc_name("SERVICE-12345", {}) == ("SERVICE", "12345")

    def test_name_only(self):
        assert ParserPlugin._parse_diag_proc_name("SERVICE", {}) == ("SERVICE", "")

    def test_name_mapping(self):
        result = ParserPlugin._parse_diag_proc_name("DHCP-9881", {"DHCP": "dhcpd"})
        assert result == ("DHCP", "9881")

    def test_non_numeric_suffix(self):
        assert ParserPlugin._parse_diag_proc_name("SERVICE-abc", {}) == ("SERVICE-abc", "")


class TestBuildProcesses:
    def test_single_process(self, sample_mech_entries):
        # Only first 5 entries (same PID)
        procs = ParserPlugin._build_processes(sample_mech_entries[:5])
        assert len(procs) == 1
        assert procs[0].process_name == "dhcp"
        assert procs[0].pid == "100"
        assert procs[0].total_count == 5

    def test_missing_sequences(self):
        entries = [
            MechLogEntry(process_name="svc", pid="1", sequence=i, raw=f"line{i}")
            for i in [1, 2, 4, 5, 8]
        ]
        procs = ParserPlugin._build_processes(entries)
        assert len(procs) == 1
        assert procs[0].missing_sequences == [3, 6, 7]

    def test_grouped_by_name_and_pid(self):
        entries = [
            MechLogEntry(process_name="svc", pid="1", sequence=1, raw="a"),
            MechLogEntry(process_name="svc", pid="2", sequence=1, raw="b"),
            MechLogEntry(process_name="other", pid="1", sequence=1, raw="c"),
        ]
        procs = ParserPlugin._build_processes(entries)
        assert len(procs) == 3


class TestBuildCycles:
    def test_single_cycle_no_restart(self, plugin):
        entries = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0),
                source="journal", slot="1", cpu_id="",
                process_name="svc", pid="100", sequence=i,
                raw=f"line{i}",
            )
            for i in range(1, 6)
        ]
        cycles = plugin._build_cycles(entries, indicator=None)
        assert len(cycles) == 1

    def test_pid_change_creates_two_cycles(self, plugin):
        entries = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0),
                source="journal", slot="1", cpu_id="",
                process_name="dhcp", pid="100", sequence=i,
                raw=f"line{i}",
            )
            for i in range(1, 4)
        ] + [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 1, i, 0),
                source="journal", slot="1", cpu_id="",
                process_name="dhcp", pid="200", sequence=i,
                raw=f"line{i+10}",
            )
            for i in range(1, 4)
        ]
        cycles = plugin._build_cycles(entries, indicator="dhcp")
        assert len(cycles) == 2

    def test_cpu_subcard_isolation(self, plugin):
        """CPU-level PID change only splits that CPU group, not others."""
        board_entries = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0),
                source="journal", slot="1", cpu_id="",
                process_name="svc", pid="100", sequence=i,
                raw=f"board_line{i}",
            )
            for i in range(1, 6)
        ]
        cpu_entries = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0),
                source="journal", slot="1", cpu_id="1",
                process_name="dhcp", pid="50", sequence=i,
                raw=f"cpu_line{i}",
            )
            for i in range(1, 4)
        ] + [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 1, i, 0),
                source="journal", slot="1", cpu_id="1",
                process_name="dhcp", pid="60", sequence=i,
                raw=f"cpu_line2_{i}",
            )
            for i in range(1, 4)
        ]
        all_entries = board_entries + cpu_entries
        cycles = plugin._build_cycles(all_entries, indicator="dhcp")
        # Board has no PID change (svc not indicator) -> 1 board cycle
        # CPU has PID change -> 2 cpu cycles
        board_cycles = [c for c in cycles if any(
            p.pid == "100" for p in c.processes
        )]
        cpu_cycles = [c for c in cycles if any(
            p.pid in ("50", "60") for p in c.processes
        )]
        assert len(board_cycles) == 1
        assert len(cpu_cycles) == 2


class TestRoleIdentification:
    def test_mech_role_overrides(self, plugin, sample_parse_result):
        mech = MechResult(
            module_name="EXAMPLE",
            active_master_slots=["1"],
        )
        ParserPlugin._apply_mech_roles(mech, sample_parse_result)
        assert sample_parse_result.diagnostic_slots[0].role == BoardRole.ACTIVE

    def test_fallback_active(self):
        result = ParseResult()
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        slot.add_active_period(ActivePeriod(
            start=datetime(2026, 1, 3, 0, 0),
            end=datetime(2026, 1, 3, 1, 0),
        ))
        result.diagnostic_slots.append(slot)
        ParserPlugin._fallback_roles(result)
        assert slot.role == BoardRole.ACTIVE

    def test_fallback_standby(self):
        result = ParseResult()
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        slot.add_diagnostic_log(LogEntry(
            path="/tmp/f", name="f.log", size_bytes=100,
        ))
        result.diagnostic_slots.append(slot)
        ParserPlugin._fallback_roles(result)
        assert slot.role == BoardRole.STANDBY

    def test_fallback_unknown(self):
        result = ParseResult()
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        result.diagnostic_slots.append(slot)
        ParserPlugin._fallback_roles(result)
        assert slot.role == BoardRole.UNKNOWN


class TestFmtDir:
    def test_both_times(self):
        s = datetime(2026, 1, 3, 10, 37, 7)
        e = datetime(2026, 1, 3, 11, 37, 8)
        assert ParserPlugin._fmt_dir(s, e) == "20260103T103707-20260103T113708"

    def test_start_only(self):
        assert ParserPlugin._fmt_dir(datetime(2026, 1, 3, 0, 0, 0), None) == "20260103T000000"

    def test_none(self):
        assert ParserPlugin._fmt_dir(None, None) == "unknown"
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_parser_plugin.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_parser_plugin.py
git commit -m "test: add unit tests for ParserPlugin core logic"
```

---

### Task 5: Test Plugin Loader and Scanner

**Files:**
- Create: `tests/test_plugin_loader.py`
- Create: `tests/test_scanner_plugin.py`

- [ ] **Step 1: Write plugin loader tests**

```python
"""Tests for backend/plugins/loader.py."""
from __future__ import annotations

import pytest

from backend.plugins.base import DirectoryDiscoveryPlugin, LogParserPlugin
from backend.plugins.loader import instantiate_plugin


class TestInstantiatePlugin:
    def test_load_scanner_plugin(self):
        plugin = instantiate_plugin(
            "backend.plugins.default.scanner.ScannerPlugin",
            DirectoryDiscoveryPlugin,
            {"diagnostic_dir": "diag", "private_dir": "varlog"},
        )
        assert isinstance(plugin, DirectoryDiscoveryPlugin)

    def test_load_parser_plugin(self, sample_config):
        plugin = instantiate_plugin(
            "backend.plugins.default.parser.ParserPlugin",
            LogParserPlugin,
            sample_config,
        )
        assert isinstance(plugin, LogParserPlugin)

    def test_wrong_base_class(self, sample_config):
        with pytest.raises(TypeError, match="not a subclass"):
            instantiate_plugin(
                "backend.plugins.default.parser.ParserPlugin",
                DirectoryDiscoveryPlugin,
                sample_config,
            )

    def test_invalid_module(self):
        with pytest.raises(ModuleNotFoundError):
            instantiate_plugin(
                "nonexistent.module.Class",
                DirectoryDiscoveryPlugin,
                {},
            )

    def test_invalid_class(self):
        with pytest.raises(AttributeError):
            instantiate_plugin(
                "backend.plugins.default.scanner.NonExistentClass",
                DirectoryDiscoveryPlugin,
                {},
            )
```

- [ ] **Step 2: Write scanner plugin tests**

```python
"""Tests for ScannerPlugin directory discovery."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from backend.plugins.default.scanner import ScannerPlugin


@pytest.fixture
def scanner():
    return ScannerPlugin(
        config={
            "diagnostic_dir": "diag",
            "private_dir": "varlog",
            "slot_dir_pattern": "slot_*",
            "diag_file_patterns": ["diag.zip", "diaglog_*.log.zip"],
            "filename_timestamp_regex": r".*_(\d{14})\..*",
            "private_dir_patterns": ["slot_*", "slot_*_cpu_*"],
            "archive_name": "varlog.zip",
            "journal_file_patterns": ["journal.log", "journal.log.*.gz"],
            "journal_sequence_regex": r"journal\.log(?:\.(\d+))?(?:\.gz)?",
            "compressed_extensions": [".gz", ".zip"],
        },
    )


def _create_mock_package(root: Path) -> Path:
    """Create a mock diagnostic package structure on disk."""
    diag_dir = root / "diag"
    diag_slot1 = diag_dir / "slot_1"
    diag_slot1.mkdir(parents=True)

    # diag.zip
    diag_zip = diag_slot1 / "diag.zip"
    with zipfile.ZipFile(diag_zip, "w") as zf:
        zf.writestr("diag_content.log", "2026-01-03T00:00:00 EXAMPLE msg")

    # diaglog with timestamp
    diaglog_zip = diag_slot1 / "diaglog_1_20260103000000.log.zip"
    with zipfile.ZipFile(diaglog_zip, "w") as zf:
        zf.writestr("diaglog_content.log", "2026-01-03T00:01:00 EXAMPLE msg")

    # varlog
    varlog_dir = root / "varlog" / "slot_1"
    varlog_dir.mkdir(parents=True)

    # varlog.zip containing varlog/journal.log
    varlog_zip = varlog_dir / "varlog.zip"
    with zipfile.ZipFile(varlog_zip, "w") as zf:
        zf.writestr("varlog/journal.log", "Jan  3 00:00:00 dhcp-100: No[1] EXAMPLE msg")

    return root


class TestScannerPlugin:
    def test_discover_finds_slots(self, scanner, tmp_path):
        pkg_root = _create_mock_package(tmp_path)
        diag_slots, private_slots = scanner.discover(pkg_root)

        assert len(diag_slots) == 1
        assert diag_slots[0].slot_id == "1"
        assert len(diag_slots[0].diagnostic_logs) == 2

    def test_discover_finds_journal(self, scanner, tmp_path):
        pkg_root = _create_mock_package(tmp_path)
        diag_slots, private_slots = scanner.discover(pkg_root)

        assert len(private_slots) == 1
        assert private_slots[0].slot_id == "1"
        assert len(private_slots[0].journal_logs) >= 1

    def test_empty_directory(self, scanner, tmp_path):
        diag_slots, private_slots = scanner.discover(tmp_path)
        assert diag_slots == []
        assert private_slots == []

    def test_missing_diag_dir(self, scanner, tmp_path):
        (tmp_path / "other").mkdir()
        diag_slots, private_slots = scanner.discover(tmp_path)
        assert diag_slots == []
```

- [ ] **Step 3: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_plugin_loader.py tests/test_scanner_plugin.py
git commit -m "test: add tests for plugin loader and ScannerPlugin"
```

---

## Phase P1: Switch to New Pipeline Default

### Task 6: Make `--product default` the Default

**Files:**
- Modify: `cli.py`

- [ ] **Step 1: Change parse command to default to new pipeline**

In `cli.py`, change the `parse` command's `--product` default from `None` to `"default"`:

Replace:
```python
@click.option("--product", "-p", default=None, help="产品名（使用新管道，如 default）")
```
With:
```python
@click.option("--product", "-p", default="default", help="产品名（default/compact）")
```

Replace the routing logic in `parse()`:
```python
    # 新管道（--product 指定时）
    if product:
        raw_config = {}
        if Path(config_path).exists():
            import yaml
            raw_config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        pipeline = Pipeline(raw_config)
        result = pipeline.run(source, output_dir, product=product, verbose=verbose)
        if result.errors:
            click.echo(f"\n⚠ {len(result.errors)} 个错误:")
            for e in result.errors:
                click.echo(f"  - {e}")
    else:
        result = _parse_legacy(source, output_dir, config_path, verbose)
```
With:
```python
    raw_config = {}
    if Path(config_path).exists():
        import yaml
        raw_config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    pipeline = Pipeline(raw_config)
    result = pipeline.run(source, output_dir, product=product, verbose=verbose)
    if result.errors:
        click.echo(f"\n⚠ {len(result.errors)} 个错误:")
        for e in result.errors:
            click.echo(f"  - {e}")
```

- [ ] **Step 2: Run tests to verify no regression**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add cli.py
git commit -m "feat: default to new plugin pipeline (--product default)"
```

---

### Task 7: Migrate check-config to New Config

**Files:**
- Modify: `cli.py` (check_config and test_pattern commands)

- [ ] **Step 1: Rewrite check_config to use new YAML structure**

Replace the `check_config` command body to read `products.default` config from YAML directly, without `ConfigLoader`. The new version should:

1. Load YAML file
2. Check `products.default` section exists
3. Validate regex patterns (diag_pattern, journal.line_pattern, journal.line_pattern2, sequence_pattern, timestamp_regex) are compilable
4. Validate glob patterns (diag_file_patterns, journal_file_patterns, private_dir_patterns, slot_dir_pattern) via `glob_to_regex`
5. Check mechanism_modules completeness

```python
@cli.command()
@click.option("--config", "-c", default="config.yaml", help="配置文件路径")
def check_config(config):
    """检查配置文件的有效性。"""
    config_path = Path(config)
    if not config_path.exists():
        click.echo(f"✗ 配置文件不存在: {config_path}")
        sys.exit(1)

    errors: list[str] = []
    warnings: list[str] = []

    try:
        import yaml
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        click.echo("✓ 配置加载成功")
    except Exception as e:
        click.echo(f"✗ 配置加载失败: {e}")
        sys.exit(1)

    products = raw.get("products", {})
    if not products:
        errors.append("无产品配置 (products 段为空)")
    else:
        for prod_name, prod_cfg in products.items():
            prefix = f"[{prod_name}]"

            # Discovery config
            disc = prod_cfg.get("discovery", {}).get("config", {})
            for label, pattern in [
                ("slot_dir_pattern", disc.get("slot_dir_pattern", "slot_*")),
            ]:
                try:
                    glob_to_regex(pattern)
                except Exception:
                    errors.append(f"{prefix} {label}: glob 无效 - {pattern}")

            for p in disc.get("diag_file_patterns", []):
                try:
                    glob_to_regex(p)
                except Exception:
                    errors.append(f"{prefix} diag_file_pattern: glob 无效 - {p}")

            # Parser config
            parser_cfg = prod_cfg.get("log_parser", {}).get("config", {})

            ts_re = parser_cfg.get("timestamp_regex", "")
            if ts_re:
                try:
                    re.compile(ts_re)
                except re.error as e:
                    errors.append(f"{prefix} timestamp_regex: 正则无效 - {e}")

            for mod_key, mod_cfg in parser_cfg.get("mechanism_modules", {}).items():
                mp = f"{prefix}[{mod_key}]"
                if not mod_cfg.get("module_name"):
                    warnings.append(f"{mp} module_name 为空")
                if mod_cfg.get("diag_pattern"):
                    try:
                        r = re.compile(mod_cfg["diag_pattern"])
                        required = {"Slot", "CPU_Id", "ProcessName", "Context"}
                        if not required.issubset(r.groupindex):
                            warnings.append(f"{mp} diag_pattern 缺少命名组: {required - set(r.groupindex)}")
                    except re.error as e:
                        errors.append(f"{mp} diag_pattern: 正则无效 - {e}")

                jnl = mod_cfg.get("journal", {})
                for pat_name in ("line_pattern", "line_pattern2"):
                    val = jnl.get(pat_name, "")
                    if val:
                        try:
                            re.compile(val)
                        except re.error as e:
                            errors.append(f"{mp} journal.{pat_name}: 正则无效 - {e}")

                seq_pat = mod_cfg.get("sequence_pattern", "")
                if seq_pat:
                    try:
                        re.compile(seq_pat)
                    except re.error as e:
                        errors.append(f"{mp} sequence_pattern: 正则无效 - {e}")

    if warnings:
        click.echo(f"\n⚠ {len(warnings)} 个警告:")
        for w in warnings:
            click.echo(f"  - {w}")

    if errors:
        click.echo(f"\n✗ {len(errors)} 个错误:")
        for e in errors:
            click.echo(f"  - {e}")
        sys.exit(1)
    else:
        click.echo("\n✓ 配置检查通过")
```

- [ ] **Step 2: Run check-config to verify**

Run: `python cli.py check-config -c config.yaml`
Expected: "✓ 配置检查通过"

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add cli.py
git commit -m "refactor: migrate check-config to new YAML structure"
```

---

### Task 8: Migrate test-pattern to New Config

**Files:**
- Modify: `cli.py` (test_pattern command)

- [ ] **Step 1: Rewrite test_pattern to use YAML directly**

Replace the `test_pattern` command to load config from YAML products section instead of ConfigLoader:

```python
@cli.command()
@click.option("--config", "-c", default="config.yaml", help="配置文件路径")
@click.option("--module", "-m", required=True, help="机制模块 key")
@click.option("--type", "-t", "log_type", type=click.Choice(["diag", "journal"]), required=True)
@click.argument("line")
def test_pattern(config, module, log_type, line):
    """用配置的正则测试一条日志行。"""
    import yaml
    config_path = Path(config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {} if config_path.exists() else {}

    # Find module config from first product that has it
    mod_cfg = None
    for prod_name, prod_cfg in raw.get("products", {}).items():
        parser_cfg = prod_cfg.get("log_parser", {}).get("config", {})
        modules = parser_cfg.get("mechanism_modules", {})
        if module in modules:
            mod_cfg = modules[module]
            break

    if mod_cfg is None:
        click.echo(f"✗ 模块 '{module}' 未配置", err=True)
        sys.exit(1)

    if log_type == "diag":
        if not mod_cfg.get("diag_pattern"):
            click.echo("✗ diag_pattern 未配置", err=True)
            sys.exit(1)
        pat = re.compile(mod_cfg["diag_pattern"])
        m = pat.search(line)
        if not m:
            click.echo("✗ 不匹配 diag_pattern")
            sys.exit(1)
        click.echo("✓ 匹配 diag_pattern")
        mod_name = mod_cfg.get("module_name", "")
        click.echo(f"  模块名预过滤: {mod_name} {'✓' if mod_name in line else '✗ (Stage1 会被过滤)'}")
        for name, value in m.groupdict().items():
            click.echo(f"  {name}: {value}")
        seq_pat = mod_cfg.get("sequence_pattern", "")
        if seq_pat:
            seq_m = re.search(seq_pat, line)
            if seq_m:
                click.echo(f"  序号: {seq_m.group(1)}")
        master_kw = mod_cfg.get("active_master_keyword", "")
        if master_kw and re.search(master_kw, line):
            click.echo(f"  ✓ 命中主控关键字: {master_kw}")

    else:  # journal
        jnl = mod_cfg.get("journal", {})
        if not jnl.get("line_pattern") and not jnl.get("line_pattern2"):
            click.echo("✗ journal.line_pattern 和 line_pattern2 均未配置", err=True)
            sys.exit(1)
        pat_name = "journal.line_pattern"
        pat = re.compile(jnl["line_pattern"]) if jnl.get("line_pattern") else None
        m = pat.match(line) if pat else None
        if not m and jnl.get("line_pattern2"):
            pat_name = "journal.line_pattern2"
            pat = re.compile(jnl["line_pattern2"])
            m = pat.match(line)
        if not m:
            click.echo("✗ 不匹配 journal.line_pattern 及 line_pattern2")
            sys.exit(1)
        click.echo(f"✓ 匹配 {pat_name}")
        click.echo(f"  进程名: {m.group(1)}")
        if m.group(2):
            click.echo(f"  pid: {m.group(2)}")
        click.echo(f"  序号: {m.group(3)}")
        click.echo(f"  Context: {m.group(4)}")
        keyword = jnl.get("identifying_keyword", "")
        if keyword:
            click.echo(f"  识别关键字 '{keyword}': {'✓' if keyword in line.lower() else '✗ (Stage1 会被过滤)'}")
        mod_name = mod_cfg.get("module_name", "")
        click.echo(f"  模块名预过滤: {mod_name} {'✓' if mod_name in line else '✗ (Stage1 会被过滤)'}")
```

- [ ] **Step 2: Verify with mock data**

Run: `python cli.py test-pattern -m module1 -t diag "2026-01-03T00:01:00.100000+08:00 Service=EXAMPLE; Slot=1; CPU-Id=0; ProcessName=dhcp-9881; Context=init)"`
Expected: Shows match info

- [ ] **Step 3: Commit**

```bash
git add cli.py
git commit -m "refactor: migrate test-pattern to new YAML config structure"
```

---

### Task 9: Delete Legacy Modules and Clean Imports

**Files:**
- Delete: `backend/scanner.py`, `backend/mech_parser.py`, `backend/log_parser.py`, `backend/identifier.py`, `backend/config.py`
- Modify: `cli.py` — remove legacy imports and `_parse_legacy` function and `_extract_inner_contents` helper

- [ ] **Step 1: Remove legacy imports from cli.py**

Remove these lines from cli.py:
```python
from backend.mech_parser import MechParser
from backend.config import BoardConfig, ConfigLoader, glob_to_regex
from backend.decompressor import Decompressor
from backend.identifier import Identifier
from backend.log_parser import LogParser
from backend.scanner import Scanner
```

Keep only:
```python
from backend.pipeline import Pipeline
from backend.models import ParseResult
from backend.metadata import MetadataGenerator
from backend.utils import glob_to_regex
```

Remove `_parse_legacy` and `_extract_inner_contents` functions entirely.

- [ ] **Step 2: Delete legacy files**

```bash
git rm backend/scanner.py backend/mech_parser.py backend/log_parser.py backend/identifier.py backend/config.py
```

- [ ] **Step 3: Update requirements.txt — remove pydantic if no longer needed**

Check if any remaining code imports pydantic. `backend/models.py` still uses it, so keep it.

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Verify CLI still works**

Run: `python cli.py check-config`
Expected: "✓ 配置检查通过"

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: remove legacy pipeline modules (scanner, mech_parser, log_parser, identifier, config)"
```

---

### Task 10: Clean config.yaml — Remove Legacy Section

**Files:**
- Modify: `config.yaml`

- [ ] **Step 1: Remove legacy config section**

Remove everything after the `# ── 旧版扁平配置（保留向后兼容） ──────────────────────────` comment through end of file. That is, remove the `output:`, `package:`, `boards:`, `diagnostic_files:`, `log_content:`, `private_logs:`, `mechanism_modules:`, and `compressed_extensions:` sections.

The resulting config.yaml should contain only:
- `pipeline:` section
- `products:` section

- [ ] **Step 2: Verify check-config still passes**

Run: `python cli.py check-config`
Expected: "✓ 配置检查通过"

- [ ] **Step 3: Commit**

```bash
git add config.yaml
git commit -m "refactor: remove legacy config section from config.yaml"
```

---

## Phase P2: Split ParserPlugin

### Task 11: Extract TimestampExtractor

**Files:**
- Create: `backend/parsing/__init__.py`
- Create: `backend/parsing/timestamp_extractor.py`
- Modify: `backend/plugins/default/parser.py`
- Create: `tests/test_timestamp_extractor.py`

- [ ] **Step 1: Write tests for TimestampExtractor**

```python
"""Tests for TimestampExtractor."""
from __future__ import annotations

import gzip
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from backend.models import LogEntry, SlotInfo


class TestTimestampExtractor:
    @pytest.fixture(autouse=True)
    def setup(self):
        from backend.parsing.timestamp_extractor import TimestampExtractor
        import re
        self.extractor = TimestampExtractor(
            ts_regex=re.compile(
                r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?"
            )
        )

    def test_extract_from_text_with_tz(self):
        stamps = self.extractor.extract_from_text("2026-01-03T00:01:00+08:00 msg")
        assert len(stamps) == 1
        assert stamps[0].tzinfo is not None

    def test_extract_from_text_without_tz(self):
        stamps = self.extractor.extract_from_text("2026-01-03T00:01:00 msg")
        assert len(stamps) == 1
        assert stamps[0].tzinfo is None

    def test_extract_from_file(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("2026-01-03T00:01:00 line1\n2026-01-03T00:02:00 line2", encoding="utf-8")
        stamps = self.extractor.extract_from_file(f)
        assert len(stamps) == 2

    def test_extract_from_gz_file(self, tmp_path):
        f = tmp_path / "test.log.gz"
        with gzip.open(f, "wt", encoding="utf-8") as fh:
            fh.write("2026-01-03T00:01:00 gz line")
        stamps = self.extractor.extract_from_file(f)
        assert len(stamps) == 1

    def test_extract_from_entry_plain_file(self, tmp_path):
        f = tmp_path / "plain.log"
        f.write_text("2026-01-03T00:01:00 plain", encoding="utf-8")
        entry = LogEntry(path=str(f), name="plain.log", size_bytes=100)
        stamps = self.extractor.extract_from_entry(entry)
        assert len(stamps) == 1

    def test_extract_from_entry_compressed_dir(self, tmp_path):
        ext_dir = tmp_path / "extracted"
        ext_dir.mkdir()
        (ext_dir / "inner.log").write_text("2026-01-03T00:01:00 inner", encoding="utf-8")
        entry = LogEntry(
            path=str(tmp_path / "fake.zip"),
            name="fake.zip",
            size_bytes=100,
            compressed=True,
            extracted_path=str(ext_dir),
        )
        stamps = self.extractor.extract_from_entry(entry)
        assert len(stamps) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_timestamp_extractor.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Create backend/parsing/__init__.py**

```python
```

- [ ] **Step 4: Implement TimestampExtractor**

Create `backend/parsing/timestamp_extractor.py`:

```python
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
        text = self._read_file(file_path)
        if not text:
            return []
        return self.extract_from_text(text)

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
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_timestamp_extractor.py -v`
Expected: All PASS

- [ ] **Step 6: Wire into ParserPlugin**

In `backend/plugins/default/parser.py`, add import:

```python
from backend.parsing.timestamp_extractor import TimestampExtractor
```

In `__init__`, create the extractor:
```python
self._ts_extractor = TimestampExtractor(self._ts_regex)
```

Replace `_extract_all_timestamps` to delegate:
```python
def _extract_all_timestamps(self, slots: list[SlotInfo]) -> None:
    for slot in slots:
        for entry in slot.diagnostic_logs:
            entry.content_timestamps = self._ts_extractor.extract_from_entry(entry)
```

Remove `_extract_ts_from_entry`, `_extract_ts_from_file`, `_extract_content_timestamps`, `_read_entry`, `_read_file` from ParserPlugin. Update `_extract_first_ts` to use the extractor:
```python
def _extract_first_ts(self, line: str) -> datetime | None:
    stamps = self._ts_extractor.extract_from_text(line)
    return stamps[0] if stamps else None
```

- [ ] **Step 7: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add backend/parsing/ backend/plugins/default/parser.py tests/test_timestamp_extractor.py
git commit -m "refactor: extract TimestampExtractor from ParserPlugin"
```

---

### Task 12: Extract CycleDetector

**Files:**
- Create: `backend/parsing/cycle_detector.py`
- Modify: `backend/plugins/default/parser.py`
- Create: `tests/test_cycle_detector.py`

- [ ] **Step 1: Write tests for CycleDetector**

```python
"""Tests for CycleDetector."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.models import MechBoardCycle, MechLogEntry
from backend.parsing.cycle_detector import CycleDetector


@pytest.fixture
def detector():
    return CycleDetector(indicator="dhcp")


class TestCycleDetector:
    def test_single_cycle(self, detector):
        tz = timezone(timedelta(hours=8))
        entries = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0, tzinfo=tz),
                source="journal", slot="1", cpu_id="",
                process_name="svc", pid="100", sequence=i,
                raw=f"line{i}",
            )
            for i in range(1, 6)
        ]
        cycles = detector.detect(entries)
        assert len(cycles) == 1
        assert len(cycles[0].processes) == 1

    def test_pid_change_splits(self, detector):
        tz = timezone(timedelta(hours=8))
        entries = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0, tzinfo=tz),
                source="journal", slot="1", cpu_id="",
                process_name="dhcp", pid="100", sequence=i,
                raw=f"line{i}",
            )
            for i in range(1, 4)
        ] + [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 1, i, 0, tzinfo=tz),
                source="journal", slot="1", cpu_id="",
                process_name="dhcp", pid="200", sequence=i,
                raw=f"line2_{i}",
            )
            for i in range(1, 4)
        ]
        cycles = detector.detect(entries)
        assert len(cycles) == 2

    def test_no_indicator(self):
        detector = CycleDetector(indicator=None)
        tz = timezone(timedelta(hours=8))
        entries = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0, tzinfo=tz),
                source="journal", slot="1", cpu_id="",
                process_name="svc", pid="100", sequence=i,
                raw=f"line{i}",
            )
            for i in range(1, 11)
        ]
        cycles = detector.detect(entries)
        assert len(cycles) == 1

    def test_empty_entries(self, detector):
        assert detector.detect([]) == []

    def test_cpu_subcard_isolation(self, detector):
        tz = timezone(timedelta(hours=8))
        board = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0, tzinfo=tz),
                source="journal", slot="1", cpu_id="",
                process_name="svc", pid="100", sequence=i,
                raw=f"board_{i}",
            )
            for i in range(1, 6)
        ]
        cpu = [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 0, i, 0, tzinfo=tz),
                source="journal", slot="1", cpu_id="1",
                process_name="dhcp", pid="50", sequence=i,
                raw=f"cpu_{i}",
            )
            for i in range(1, 4)
        ] + [
            MechLogEntry(
                timestamp=datetime(2026, 1, 3, 1, i, 0, tzinfo=tz),
                source="journal", slot="1", cpu_id="1",
                process_name="dhcp", pid="60", sequence=i,
                raw=f"cpu2_{i}",
            )
            for i in range(1, 4)
        ]
        cycles = detector.detect(board + cpu)
        assert len(cycles) >= 3  # 1 board + 2 cpu
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cycle_detector.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement CycleDetector**

Create `backend/parsing/cycle_detector.py` — move `_build_cycles`, `_find_seq_wrap_boundary`, `_make_cycles`, `_build_processes`, `_fmt_dir` from ParserPlugin into this class. The `detect(entries) -> list[MechBoardCycle]` method is the public entry point.

```python
"""重启周期检测：PID 变化 + 序号回绕反向扫描。"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from backend.models import MechBoardCycle, MechLogEntry, MechProcessLifecycle

SEQ_ROLLBACK_THRESHOLD = 3


class CycleDetector:
    def __init__(self, indicator: str | None = None):
        self._indicator = indicator

    def detect(self, entries: list[MechLogEntry]) -> list[MechBoardCycle]:
        return self._build_cycles(entries, self._indicator)

    def _build_cycles(
        self, entries: list[MechLogEntry], indicator: str | None,
    ) -> list[MechBoardCycle]:
        if not entries:
            return []

        by_cpu: dict[str, list[MechLogEntry]] = defaultdict(list)
        for e in entries:
            cpu_key = e.cpu_id or ""
            by_cpu[cpu_key].append(e)

        for cpu_key in by_cpu:
            by_cpu[cpu_key].sort(key=lambda e: (
                0 if e.timestamp else 1,
                e.timestamp.timestamp() if e.timestamp else 0,
                e.sequence,
            ))

        board_splits: list[datetime] = []
        if "" in by_cpu and indicator:
            group = by_cpu[""]
            max_seq: dict[tuple[str, str], int] = {}
            seg_start = 0
            prev_pid = None
            for i, e in enumerate(group):
                if e.sequence > 0:
                    key = (e.process_name.lower(), e.pid or "")
                    max_seq[key] = max(max_seq.get(key, 0), e.sequence)
                if indicator in e.process_name.lower():
                    if prev_pid and e.pid and prev_pid != e.pid:
                        boundary = self._find_seq_wrap_boundary(
                            group, i - 1, seg_start, max_seq,
                        )
                        board_splits.append(
                            group[boundary].timestamp if group[boundary].timestamp else e.timestamp
                        )
                        seg_start = boundary
                    if e.pid:
                        prev_pid = e.pid

        cycles: list[MechBoardCycle] = []
        for cpu_key in sorted(by_cpu.keys()):
            group = by_cpu[cpu_key]
            max_seq: dict[tuple[str, str], int] = {}

            local_splits: set[int] = set()
            seg_start = 0
            if indicator:
                prev_pid = None
                for i, e in enumerate(group):
                    if e.sequence > 0:
                        key = (e.process_name.lower(), e.pid or "")
                        max_seq[key] = max(max_seq.get(key, 0), e.sequence)
                    if indicator in e.process_name.lower():
                        if prev_pid and e.pid and prev_pid != e.pid:
                            boundary = self._find_seq_wrap_boundary(
                                group, i - 1, seg_start, max_seq,
                            )
                            local_splits.add(boundary)
                            seg_start = boundary
                        if e.pid:
                            prev_pid = e.pid

            if cpu_key != "" and board_splits:
                for split_ts in board_splits:
                    if split_ts is None:
                        continue
                    for i, e in enumerate(group):
                        if e.timestamp and e.timestamp >= split_ts:
                            boundary = self._find_seq_wrap_boundary(
                                group, i, seg_start, max_seq,
                            )
                            local_splits.add(boundary)
                            seg_start = boundary
                            break

            all_splits = sorted(local_splits)
            seg_start = 0
            for split_i in all_splits:
                if split_i > seg_start:
                    cycles.extend(self._make_cycles(group[seg_start:split_i]))
                seg_start = split_i
            if seg_start < len(group):
                cycles.extend(self._make_cycles(group[seg_start:]))

        return cycles

    @staticmethod
    def _find_seq_wrap_boundary(
        group: list[MechLogEntry], search_end: int, search_start: int,
        max_seq: dict[tuple[str, str], int],
    ) -> int:
        boundary = search_end + 1
        for j in range(search_end, search_start - 1, -1):
            e = group[j]
            if e.sequence > 0:
                key = (e.process_name.lower(), e.pid or "")
                prev_max = max_seq.get(key, 0)
                if prev_max - e.sequence > SEQ_ROLLBACK_THRESHOLD:
                    boundary = j
        return boundary

    @staticmethod
    def _make_cycles(entries: list[MechLogEntry]) -> list[MechBoardCycle]:
        if not entries:
            return []
        procs = CycleDetector._build_processes(entries)
        times = [e.timestamp for e in entries if e.timestamp]
        start = min(times) if times else None
        end = max(times) if times else None
        dir_name = CycleDetector._fmt_dir(start, end)
        return [MechBoardCycle(
            dir_name=dir_name, start_time=start, end_time=end,
            processes=procs,
        )]

    @staticmethod
    def _build_processes(
        entries: list[MechLogEntry],
    ) -> list[MechProcessLifecycle]:
        by_key: dict[tuple[str, str], list[MechLogEntry]] = defaultdict(list)
        for e in entries:
            by_key[(e.process_name, e.pid)].append(e)

        lifecycles: list[MechProcessLifecycle] = []
        for (proc_name, pid), logs in sorted(by_key.items()):
            logs.sort(key=lambda e: e.sequence)
            seqs = [l.sequence for l in logs if l.sequence > 0]
            missing: list[int] = []
            if len(seqs) >= 2:
                full = set(range(min(seqs), max(seqs) + 1))
                missing = sorted(full - set(seqs))
            lifecycles.append(MechProcessLifecycle(
                process_name=proc_name, pid=pid, logs=logs,
                total_count=len(logs), missing_sequences=missing,
            ))
        return lifecycles

    @staticmethod
    def _fmt_dir(start: datetime | None, end: datetime | None) -> str:
        if start and end:
            return f"{start.strftime('%Y%m%dT%H%M%S')}-{end.strftime('%Y%m%dT%H%M%S')}"
        if start:
            return start.strftime('%Y%m%dT%H%M%S')
        return "unknown"
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_cycle_detector.py -v`
Expected: All PASS

- [ ] **Step 5: Wire into ParserPlugin**

In `backend/plugins/default/parser.py`:
- Add `from backend.parsing.cycle_detector import CycleDetector`
- In `_parse_one_mech`, create `detector = CycleDetector(indicator=...)` and call `detector.detect(entries)` instead of `self._build_cycles(entries, indicator)`
- Remove `_build_cycles`, `_find_seq_wrap_boundary`, `_make_cycles`, `_build_processes`, `_fmt_dir` from ParserPlugin
- Remove `SEQ_ROLLBACK_THRESHOLD` constant

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add backend/parsing/cycle_detector.py backend/plugins/default/parser.py tests/test_cycle_detector.py
git commit -m "refactor: extract CycleDetector from ParserPlugin"
```

---

### Task 13: Extract RoleIdentifier

**Files:**
- Create: `backend/parsing/role_identifier.py`
- Modify: `backend/plugins/default/parser.py`
- Create: `tests/test_role_identifier.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for RoleIdentifier."""
from __future__ import annotations

from datetime import datetime

import pytest

from backend.models import (
    ActivePeriod, BoardRole, LogEntry, MechResult, ParseResult, SlotInfo,
)
from backend.parsing.role_identifier import RoleIdentifier


@pytest.fixture
def identifier():
    return RoleIdentifier()


class TestRoleIdentifier:
    def test_mech_active(self, identifier):
        result = ParseResult()
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        result.diagnostic_slots.append(slot)
        mech = MechResult(module_name="MOD", active_master_slots=["1"])
        identifier.apply_mech_roles(mech, result)
        assert slot.role == BoardRole.ACTIVE

    def test_fallback_active(self, identifier):
        result = ParseResult()
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        slot.add_active_period(ActivePeriod(
            start=datetime(2026, 1, 3, 0, 0), end=datetime(2026, 1, 3, 1, 0),
        ))
        result.diagnostic_slots.append(slot)
        identifier.fallback_roles(result)
        assert slot.role == BoardRole.ACTIVE

    def test_fallback_standby(self, identifier):
        result = ParseResult()
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        slot.add_diagnostic_log(LogEntry(path="/tmp/f", name="f.log", size_bytes=100))
        result.diagnostic_slots.append(slot)
        identifier.fallback_roles(result)
        assert slot.role == BoardRole.STANDBY

    def test_fallback_unknown(self, identifier):
        result = ParseResult()
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        result.diagnostic_slots.append(slot)
        identifier.fallback_roles(result)
        assert slot.role == BoardRole.UNKNOWN

    def test_no_override_existing(self, identifier):
        result = ParseResult()
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        slot.role = BoardRole.ACTIVE
        result.diagnostic_slots.append(slot)
        identifier.fallback_roles(result)
        assert slot.role == BoardRole.ACTIVE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_role_identifier.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement RoleIdentifier**

Create `backend/parsing/role_identifier.py`:

```python
"""板卡角色判定：机制模块优先 + 兜底逻辑。"""
from __future__ import annotations

from backend.models import BoardRole, MechResult, ParseResult


class RoleIdentifier:
    @staticmethod
    def apply_mech_roles(mech_result: MechResult, result: ParseResult) -> None:
        if not mech_result.active_master_slots:
            return
        for slot in result.diagnostic_slots:
            if slot.slot_id in mech_result.active_master_slots:
                slot.role = BoardRole.ACTIVE

    @staticmethod
    def fallback_roles(result: ParseResult) -> None:
        for slot in result.diagnostic_slots:
            if slot.role != BoardRole.UNKNOWN:
                continue
            if slot.active_periods:
                slot.role = BoardRole.ACTIVE
            elif slot.diagnostic_logs:
                slot.role = BoardRole.STANDBY
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_role_identifier.py -v`
Expected: All PASS

- [ ] **Step 5: Wire into ParserPlugin**

In `backend/plugins/default/parser.py`:
- Add `from backend.parsing.role_identifier import RoleIdentifier`
- Replace `self._apply_mech_roles(mech, result)` with `RoleIdentifier.apply_mech_roles(mech, result)`
- Replace `self._fallback_roles(result)` with `RoleIdentifier.fallback_roles(result)`
- Remove `_apply_mech_roles` and `_fallback_roles` from ParserPlugin

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add backend/parsing/role_identifier.py backend/plugins/default/parser.py tests/test_role_identifier.py
git commit -m "refactor: extract RoleIdentifier from ParserPlugin"
```

---

### Task 14: Extract MechOutputWriter

**Files:**
- Create: `backend/parsing/output_writer.py`
- Modify: `backend/plugins/default/parser.py`
- Create: `tests/test_output_writer.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for MechOutputWriter."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from backend.models import (
    MechBoardCycle, MechLogEntry, MechProcessLifecycle,
    MechResult, MechSlotOutput,
)
from backend.parsing.output_writer import MechOutputWriter


@pytest.fixture
def writer():
    return MechOutputWriter()


def _make_mech_result() -> MechResult:
    tz = timezone(timedelta(hours=8))
    result = MechResult(module_name="EXAMPLE")
    slot = MechSlotOutput(slot_id="1")
    slot.board_cycles.append(MechBoardCycle(
        dir_name="20260103T000100-20260103T000200",
        start_time=datetime(2026, 1, 3, 0, 1, 0, tzinfo=tz),
        end_time=datetime(2026, 1, 3, 0, 2, 0, tzinfo=tz),
        processes=[
            MechProcessLifecycle(
                process_name="svc", pid="100",
                logs=[
                    MechLogEntry(
                        timestamp=datetime(2026, 1, 3, 0, 1, 30, tzinfo=tz),
                        source="journal", source_file="slot_1/journal.log",
                        slot="1", cpu_id="",
                        process_name="svc", pid="100",
                        context="msg", sequence=1, raw="raw line 1",
                    ),
                ],
                total_count=1,
            ),
        ],
    ))
    result.slots.append(slot)
    return result


class TestMechOutputWriter:
    def test_creates_directory_structure(self, writer, tmp_path):
        mech_result = _make_mech_result()
        output_dir = writer.write(mech_result, tmp_path)

        expected_log = (
            tmp_path / "mech_modules" / "EXAMPLE" / "slot_1"
            / "20260103T000100-20260103T000200" / "svc-100.log"
        )
        assert expected_log.exists()

    def test_log_file_content(self, writer, tmp_path):
        mech_result = _make_mech_result()
        writer.write(mech_result, tmp_path)

        log_path = (
            tmp_path / "mech_modules" / "EXAMPLE" / "slot_1"
            / "20260103T000100-20260103T000200" / "svc-100.log"
        )
        content = log_path.read_text(encoding="utf-8")
        assert "[0001]" in content
        assert "journal|slot_1/journal.log" in content
        assert "raw line 1" in content

    def test_cpu_subdirectory(self, writer, tmp_path):
        tz = timezone(timedelta(hours=8))
        result = MechResult(module_name="EXAMPLE")
        slot = MechSlotOutput(slot_id="1")
        slot.board_cycles.append(MechBoardCycle(
            dir_name="20260103T000100-20260103T000200",
            start_time=datetime(2026, 1, 3, 0, 1, 0, tzinfo=tz),
            end_time=datetime(2026, 1, 3, 0, 2, 0, tzinfo=tz),
            processes=[
                MechProcessLifecycle(
                    process_name="svc", pid="100",
                    logs=[
                        MechLogEntry(
                            source="journal", source_file="s/j.log",
                            slot="1", cpu_id="1",
                            process_name="svc", pid="100",
                            sequence=1, raw="cpu line",
                        ),
                    ],
                    total_count=1,
                ),
            ],
        ))
        result.slots.append(slot)

        writer.write(result, tmp_path)

        expected = (
            tmp_path / "mech_modules" / "EXAMPLE" / "slot_1"
            / "20260103T000100-20260103T000200" / "cpu_1" / "svc-100.log"
        )
        assert expected.exists()

    def test_returns_output_dir(self, writer, tmp_path):
        mech_result = _make_mech_result()
        result = writer.write(mech_result, tmp_path)
        assert result == tmp_path / "mech_modules" / "EXAMPLE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_output_writer.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement MechOutputWriter**

Create `backend/parsing/output_writer.py`:

```python
"""机制模块日志三层落盘。"""
from __future__ import annotations

from pathlib import Path

from backend.models import MechResult


class MechOutputWriter:
    def write(self, mech_result: MechResult, output_dir: Path) -> Path:
        mech_dir = output_dir / "mech_modules" / mech_result.module_name
        mech_dir.mkdir(parents=True, exist_ok=True)

        for slot in mech_result.slots:
            for cycle in slot.board_cycles:
                cycle_dir = mech_dir / f"slot_{slot.slot_id}" / cycle.dir_name
                cpu_procs: dict[str, list] = {}
                for proc in cycle.processes:
                    cpu_id = proc.logs[0].cpu_id if proc.logs else None
                    key = cpu_id or ""
                    cpu_procs.setdefault(key, []).append(proc)

                for cpu_key, procs in cpu_procs.items():
                    out_dir = cycle_dir
                    if cpu_key:
                        out_dir = cycle_dir / f"cpu_{cpu_key}"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    for proc in procs:
                        fname = f"{proc.process_name}-{proc.pid}.log"
                        out_path = out_dir / fname
                        with open(out_path, "w", encoding="utf-8") as fh:
                            for log in proc.logs:
                                seq = f"[{log.sequence:04d}]" if log.sequence else "[....]"
                                fh.write(
                                    f"{seq} [{log.source}|{log.source_file}] {log.raw}\n"
                                )

        return mech_dir
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_output_writer.py -v`
Expected: All PASS

- [ ] **Step 5: Wire into ParserPlugin**

In `backend/plugins/default/parser.py`:
- Add `from backend.parsing.output_writer import MechOutputWriter`
- In `write_output`, delegate to `MechOutputWriter().write(mech_result, output_dir)`
- Replace the `write_output` method body:

```python
def write_output(self, mech_result: MechResult, output_dir: Path) -> Path:
    return MechOutputWriter().write(mech_result, output_dir)
```

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add backend/parsing/output_writer.py backend/plugins/default/parser.py tests/test_output_writer.py
git commit -m "refactor: extract MechOutputWriter from ParserPlugin"
```

---

### Task 15: Update backend/parsing/__init__.py re-exports

**Files:**
- Modify: `backend/parsing/__init__.py`

- [ ] **Step 1: Add re-exports**

```python
from backend.parsing.cycle_detector import CycleDetector
from backend.parsing.output_writer import MechOutputWriter
from backend.parsing.role_identifier import RoleIdentifier
from backend.parsing.timestamp_extractor import TimestampExtractor

__all__ = [
    "CycleDetector",
    "MechOutputWriter",
    "RoleIdentifier",
    "TimestampExtractor",
]
```

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add backend/parsing/__init__.py
git commit -m "refactor: add parsing package re-exports"
```

---

## Phase P3: Small Cleanup

### Task 16: Remove Dead Code in pipeline.py

**Files:**
- Modify: `backend/pipeline.py`

- [ ] **Step 1: Remove duplicate Step 6 block**

Delete lines 143-148 (the duplicate Step 6 code block after `return result`):

```python
        # Step 6: 元数据
        if self.pipeline_config.get("generate_metadata", True):
            _safe("元数据生成",
                  lambda: self.metadata_gen.generate(result, output_dir / task_id))

        return result
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add backend/pipeline.py
git commit -m "fix: remove dead code after return in Pipeline.run()"
```

---

### Task 17: Fix metadata.py Key Name

**Files:**
- Modify: `backend/metadata.py`

- [ ] **Step 1: Rename `aaa_results` to `mech_results`**

In `backend/metadata.py` line 23, change:
```python
            "aaa_results": [self._mech_to_dict(a) for a in result.mech_results] if result.mech_results else [],
```
To:
```python
            "mech_results": [self._mech_to_dict(a) for a in result.mech_results] if result.mech_results else [],
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add backend/metadata.py
git commit -m "fix: rename aaa_results to mech_results in metadata output"
```

---

## Self-Review Checklist

### 1. Spec Coverage
- P0 (tests): utils.py ✓, decompressor.py ✓, parser_plugin ✓, plugin_loader ✓, scanner_plugin ✓
- P1 (pipeline switch): default product ✓, check-config ✓, test-pattern ✓, delete legacy ✓, clean config ✓
- P2 (split parser): TimestampExtractor ✓, CycleDetector ✓, RoleIdentifier ✓, MechOutputWriter ✓
- P3 (cleanup): dead code ✓, metadata key ✓

### 2. Placeholder Scan
- No TBD/TODO/fill-in-details found
- No "add appropriate error handling" patterns
- All code steps contain actual implementation
- No "similar to Task N" shortcuts

### 3. Type Consistency
- `CycleDetector.detect()` returns `list[MechBoardCycle]` — matches ParserPlugin usage
- `MechOutputWriter.write()` returns `Path` — matches `LogParserPlugin.write_output()` return type
- `RoleIdentifier` static methods match ParserPlugin's removed methods exactly
- `TimestampExtractor.extract_from_entry()` returns `list[datetime]` — matches ParserPlugin usage
