# Module2 Dependent Cycle Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `module2` as a diagnostic-only mechanism module that writes module2 logs using `module1` lifecycle directories.

**Architecture:** Implement a new `Module2Plugin` that scans diagnostic logs with its own keyword and regex, parses bracket-style process IDs such as `hellokitty[123]`, and assigns each log to the matching `module1` board cycle by slot and timestamp. It returns a normal `MechResult`, so the existing `MechOutputWriter` writes the same slot/cycle/cpu/process layout as module1. Logs that cannot be assigned to a module1 cycle are written under `unknown/`.

**Tech Stack:** Python 3.11+, Pydantic models, pytest, existing mechanism plugin loader, existing `MechOutputWriter`.

---

## Scope And Requirements

Module2 behavior:

- Module2 appears only in diagnostic logs.
- Module2 lines contain a distinct `identifying_keyword`, for example `xxx`, used as a Stage1 prefilter.
- Module2 diagnostic regex extracts named groups:
  - `Slot`
  - `CPU_Id`
  - `ProcessName`
  - `Context`
- Process names use bracket PID syntax:
  - `hellokitty[123]` becomes `process_name="hellokitty"`, `pid="123"`
  - `hellokitty` becomes `process_name="hellokitty"`, `pid=""`
- `CPU-Id=0` or empty CPU means board-level output in the cycle directory root.
- Other CPU IDs write under `cpu_N/`, reusing existing writer behavior.
- Module2 depends on module1 cycles via config:

```yaml
depends_on_module: "module1"
```

- Module2 does not run `CycleDetector`.
- Module2 does not apply roles.
- Module2 does not read journal logs.
- Module2 logs whose timestamp does not match any module1 cycle, or whose timestamp is missing, are written to `unknown/`.

Assumptions:

- `mechanism_modules` config order matters. `module1` must appear before `module2` so `result.mech_results` already contains module1 cycles when module2 parses.
- Module2 uses global `timestamp_regex` through the existing `TimestampExtractor`.

---

## File Structure

- Create: `backend/plugins/mechanisms/module2.py`
  - Owns module2 config validation, diagnostic scanning, dependency lookup, and cycle assignment.

- Modify: `backend/models.py`
  - Add optional `module_key: str = ""` to `MechResult` so dependent modules can find upstream results by config key rather than by display `module_name`.

- Modify: `backend/plugins/mechanisms/module1.py`
  - Set `mech_result.module_key = self.module_key` when producing module1 result.

- Modify: `backend/config_validation.py`
  - Add `validate_module2_config()` or plugin-owned validation via `Module2Plugin.validate_config`.
  - No global hardcoding is required if `Module2Plugin.validate_config()` handles its own schema.

- Modify: `config.yaml`
  - Add a commented or enabled example `module2` entry after `module1`.

- Create: `tests/test_module2_plugin.py`
  - Covers scanning, bracket PID parsing, module1 cycle assignment, unknown fallback, and missing dependency error.

- Modify: `tests/test_metadata.py` or query tests only if `module_key` appears in serialized JSON expectations. If no direct expectation exists, no changes are needed.

---

## Data Model

Add this field to `MechResult`:

```python
module_key: str = ""
```

Reason:

- `module_name` is the visible output directory name, such as `EXAMPLE`.
- `module_key` is the stable config key, such as `module1`.
- Module2 should depend on `module1`, not whatever real string module1 writes as `module_name`.

Existing output remains unchanged because `MechOutputWriter` uses `mech_result.module_name`.

---

## Cycle Assignment Algorithm

Given module2 log entry `entry` and upstream module1 `MechResult`:

1. Find upstream `MechSlotOutput` with the same `slot_id`.
2. If `entry.timestamp` exists, scan that slot's `board_cycles`.
3. Assign to the first cycle where:

```python
cycle.start_time is not None
and cycle.end_time is not None
and cycle.start_time <= entry.timestamp <= cycle.end_time
```

4. If no cycle matches, assign to a synthetic unknown cycle:

```python
MechBoardCycle(dir_name="unknown", start_time=None, end_time=None)
```

5. Build processes per cycle using `(process_name, pid, cpu_id)` grouping.
6. Sort each process's logs by timestamp, then source file, then raw line.

Do not compute missing sequence numbers for module2. Module2 uses timestamp ordering.

---

### Task 1: Add Tests For Module2 Config Validation

**Files:**
- Create: `tests/test_module2_plugin.py`
- Test: `python -m pytest tests/test_module2_plugin.py::test_module2_validate_config_requires_fields -q`

- [ ] **Step 1: Create the initial failing test file**

Create `tests/test_module2_plugin.py`:

```python
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from backend.models import (
    LogEntry,
    MechBoardCycle,
    MechResult,
    MechSlotOutput,
    ParseResult,
    SlotInfo,
)
from backend.parsing.timestamp_extractor import TimestampExtractor
from backend.plugins.mechanisms.module2 import Module2Plugin


def _ts(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 1, 3, hour, minute, 0, tzinfo=timezone(timedelta(hours=8)))


def _timestamp_extractor() -> TimestampExtractor:
    return TimestampExtractor(
        re.compile(r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?")
    )


def _module2_config() -> dict:
    return {
        "module_name": "MODULE2",
        "identifying_keyword": "xxx",
        "depends_on_module": "module1",
        "diag_pattern": (
            r"Slot=(?P<Slot>\d+),CPU-Id=(?P<CPU_Id>\d+),"
            r"ProcessName=(?P<ProcessName>[^,]+),Context=\"(?P<Context>.*?)\""
        ),
    }


def test_module2_validate_config_requires_fields():
    cfg = {}

    errors = Module2Plugin.validate_config("module2", cfg)

    assert any("module_name" in e for e in errors)
    assert any("identifying_keyword" in e for e in errors)
    assert any("depends_on_module" in e for e in errors)
    assert any("diag_pattern" in e for e in errors)
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
python -m pytest tests/test_module2_plugin.py::test_module2_validate_config_requires_fields -q
```

Expected: FAIL with `ModuleNotFoundError` because `backend.plugins.mechanisms.module2` does not exist.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_module2_plugin.py
git commit -m "test: cover module2 config validation"
```

---

### Task 2: Add `module_key` To `MechResult`

**Files:**
- Modify: `backend/models.py`
- Modify: `backend/plugins/mechanisms/module1.py`
- Test: `python -m pytest tests/test_module1_plugin.py -q`

- [ ] **Step 1: Add the model field**

In `backend/models.py`, update `MechResult`:

```python
class MechResult(BaseModel):
    """机制模块解析结果。"""
    module_name: str = ""
    module_key: str = ""
    slots: list[MechSlotOutput] = Field(default_factory=list)
    active_master_slots: list[str] = Field(default_factory=list)
    diag_entry_count: int = 0
    journal_entry_count: int = 0
```

- [ ] **Step 2: Set module key in module1**

In `backend/plugins/mechanisms/module1.py`, replace:

```python
        mech_result = MechResult(module_name=module_name)
```

with:

```python
        mech_result = MechResult(module_name=module_name, module_key=self.module_key)
```

- [ ] **Step 3: Add an assertion to existing module1 test**

In `tests/test_module1_plugin.py`, in `test_module1_plugin_parses_diag_entries`, add:

```python
    assert mech.module_key == "module1"
```

- [ ] **Step 4: Run module1 tests**

Run:

```bash
python -m pytest tests/test_module1_plugin.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit model support**

```bash
git add backend/models.py backend/plugins/mechanisms/module1.py tests/test_module1_plugin.py
git commit -m "feat: store mechanism result config key"
```

---

### Task 3: Implement Minimal Module2 Plugin Validation

**Files:**
- Create: `backend/plugins/mechanisms/module2.py`
- Test: `python -m pytest tests/test_module2_plugin.py::test_module2_validate_config_requires_fields -q`

- [ ] **Step 1: Create module2 plugin shell**

Create `backend/plugins/mechanisms/module2.py`:

```python
"""Module 2 mechanism plugin."""

from __future__ import annotations

import re
from typing import Any

from backend.models import MechResult, ParseResult
from backend.plugins.mechanisms.base import MechanismModulePlugin


class Module2Plugin(MechanismModulePlugin):
    """Diagnostic-only module that reuses another module's board cycles."""

    @classmethod
    def validate_config(cls, module_key: str, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        for field in ("module_name", "identifying_keyword", "depends_on_module", "diag_pattern"):
            if not config.get(field):
                errors.append(f"mechanism_modules.{module_key}.{field} 不能为空")

        pattern = config.get("diag_pattern")
        if pattern:
            try:
                diag_re = re.compile(pattern)
            except re.error as e:
                errors.append(f"mechanism_modules.{module_key}.diag_pattern 正则非法: {e}")
            else:
                required = {"Slot", "CPU_Id", "ProcessName", "Context"}
                missing = required - set(diag_re.groupindex)
                if missing:
                    errors.append(
                        f"mechanism_modules.{module_key}.diag_pattern 缺少命名组: {sorted(missing)}"
                    )

        return errors

    def parse(self, result: ParseResult) -> MechResult | None:
        errors = self.validate_config(self.module_key, self.config)
        if errors:
            result.errors.extend(errors)
            return None
        return None
```

- [ ] **Step 2: Run validation test**

Run:

```bash
python -m pytest tests/test_module2_plugin.py::test_module2_validate_config_requires_fields -q
```

Expected: PASS.

- [ ] **Step 3: Commit validation shell**

```bash
git add backend/plugins/mechanisms/module2.py tests/test_module2_plugin.py
git commit -m "feat: add module2 plugin validation"
```

---

### Task 4: Test Module2 Diagnostic Scanning And Process Parsing

**Files:**
- Modify: `tests/test_module2_plugin.py`
- Test: `python -m pytest tests/test_module2_plugin.py::test_module2_scans_diag_logs_and_parses_bracket_pid -q`

- [ ] **Step 1: Add helper to create module1 result**

Add this helper to `tests/test_module2_plugin.py`:

```python
def _module1_result() -> MechResult:
    return MechResult(
        module_name="EXAMPLE",
        module_key="module1",
        slots=[
            MechSlotOutput(
                slot_id="2",
                board_cycles=[
                    MechBoardCycle(
                        dir_name="20260103T000000-20260103T010000",
                        start_time=_ts(0),
                        end_time=_ts(1),
                    )
                ],
            )
        ],
    )
```

- [ ] **Step 2: Add failing scan test**

Add this test:

```python
def test_module2_scans_diag_logs_and_parses_bracket_pid(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:10:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="xxxxx"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    assert mech.module_name == "MODULE2"
    assert mech.module_key == "module2"
    assert mech.diag_entry_count == 1
    cycle = mech.slots[0].board_cycles[0]
    assert cycle.dir_name == "20260103T000000-20260103T010000"
    proc = cycle.processes[0]
    assert proc.process_name == "hellokitty"
    assert proc.pid == "123"
    assert proc.logs[0].cpu_id == "3"
    assert proc.logs[0].context == "xxxxx"
```

- [ ] **Step 3: Run the test and verify it fails**

Run:

```bash
python -m pytest tests/test_module2_plugin.py::test_module2_scans_diag_logs_and_parses_bracket_pid -q
```

Expected: FAIL because `Module2Plugin.parse()` returns `None`.

- [ ] **Step 4: Commit failing scan test**

```bash
git add tests/test_module2_plugin.py
git commit -m "test: cover module2 diagnostic scanning"
```

---

### Task 5: Implement Module2 Diagnostic Scan And Cycle Assignment

**Files:**
- Modify: `backend/plugins/mechanisms/module2.py`
- Test: `python -m pytest tests/test_module2_plugin.py -q`

- [ ] **Step 1: Replace module2 implementation**

Replace `backend/plugins/mechanisms/module2.py` with:

```python
"""Module 2 mechanism plugin."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from backend.models import (
    LogEntry,
    MechBoardCycle,
    MechLogEntry,
    MechProcessLifecycle,
    MechResult,
    MechSlotOutput,
    ParseResult,
)
from backend.parsing.file_iter import iter_log_entry_lines
from backend.plugins.mechanisms.base import MechanismModulePlugin


class Module2Plugin(MechanismModulePlugin):
    """Diagnostic-only module that reuses another module's board cycles."""

    @classmethod
    def validate_config(cls, module_key: str, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        for field in ("module_name", "identifying_keyword", "depends_on_module", "diag_pattern"):
            if not config.get(field):
                errors.append(f"mechanism_modules.{module_key}.{field} 不能为空")

        pattern = config.get("diag_pattern")
        if pattern:
            try:
                diag_re = re.compile(pattern)
            except re.error as e:
                errors.append(f"mechanism_modules.{module_key}.diag_pattern 正则非法: {e}")
            else:
                required = {"Slot", "CPU_Id", "ProcessName", "Context"}
                missing = required - set(diag_re.groupindex)
                if missing:
                    errors.append(
                        f"mechanism_modules.{module_key}.diag_pattern 缺少命名组: {sorted(missing)}"
                    )

        return errors

    def parse(self, result: ParseResult) -> MechResult | None:
        errors = self.validate_config(self.module_key, self.config)
        if errors:
            result.errors.extend(errors)
            return None

        upstream = self._find_dependency(result)
        if upstream is None:
            result.errors.append(
                f"{self.module_key}: depends_on_module={self.config['depends_on_module']!r} result not found"
            )
            return None

        entries = self._scan_diagnostic_entries(result)
        if not entries:
            return None

        return self._build_result(entries, upstream)

    def _find_dependency(self, result: ParseResult) -> MechResult | None:
        depends_on = self.config["depends_on_module"]
        for mech in result.mech_results:
            if mech.module_key == depends_on:
                return mech
        return None

    def _scan_diagnostic_entries(self, result: ParseResult) -> list[MechLogEntry]:
        diag_re = re.compile(self.config["diag_pattern"])
        keyword = str(self.config["identifying_keyword"])
        entries: list[MechLogEntry] = []

        for slot in result.diagnostic_slots:
            for log_entry in slot.diagnostic_logs:
                entries.extend(self._scan_log_entry(log_entry, slot.slot_id, diag_re, keyword))

        return entries

    def _scan_log_entry(
        self,
        log_entry: LogEntry,
        source_slot_id: str,
        diag_re: re.Pattern,
        keyword: str,
    ) -> list[MechLogEntry]:
        entries: list[MechLogEntry] = []
        for line in iter_log_entry_lines(log_entry):
            if keyword not in line:
                continue
            m = diag_re.search(line)
            if not m:
                continue

            slot = m.group("Slot")
            cpu_id = m.group("CPU_Id")
            if cpu_id == "0":
                cpu_id = ""
            process_name, pid = _parse_bracket_process_name(m.group("ProcessName"))
            context = m.group("Context")
            timestamp = self._extract_first_ts(line)
            source_file = f"slot_{source_slot_id}/{log_entry.name}"

            entries.append(MechLogEntry(
                timestamp=timestamp,
                source="diagnostic",
                source_file=source_file,
                slot=slot,
                cpu_id=cpu_id,
                process_name=process_name,
                pid=pid,
                context=context,
                sequence=0,
                raw=line.strip()[:500],
            ))

        return entries

    def _extract_first_ts(self, line: str) -> datetime | None:
        stamps = self.ts_extractor.extract_from_text(line)
        return stamps[0] if stamps else None

    def _build_result(self, entries: list[MechLogEntry], upstream: MechResult) -> MechResult:
        by_slot: dict[str, list[MechLogEntry]] = defaultdict(list)
        for entry in entries:
            by_slot[entry.slot].append(entry)

        mech_result = MechResult(module_name=self.config["module_name"], module_key=self.module_key)
        for slot_id, slot_entries in sorted(by_slot.items()):
            slot_output = MechSlotOutput(slot_id=slot_id)
            upstream_slot = _find_upstream_slot(upstream, slot_id)
            grouped = _assign_entries_to_cycles(slot_entries, upstream_slot)
            slot_output.board_cycles = [
                _build_cycle(cycle_template, cycle_entries)
                for cycle_template, cycle_entries in grouped
            ]
            mech_result.slots.append(slot_output)

        mech_result.diag_entry_count = len(entries)
        return mech_result


def _parse_bracket_process_name(raw: str) -> tuple[str, str]:
    m = re.match(r"^(?P<name>.+?)\[(?P<pid>\d+)\]$", raw)
    if not m:
        return raw, ""
    return m.group("name"), m.group("pid")


def _find_upstream_slot(upstream: MechResult, slot_id: str) -> MechSlotOutput | None:
    for slot in upstream.slots:
        if slot.slot_id == slot_id:
            return slot
    return None


def _assign_entries_to_cycles(
    entries: list[MechLogEntry],
    upstream_slot: MechSlotOutput | None,
) -> list[tuple[MechBoardCycle, list[MechLogEntry]]]:
    buckets: list[tuple[MechBoardCycle, list[MechLogEntry]]] = []
    unknown = MechBoardCycle(dir_name="unknown")

    for entry in entries:
        cycle = _find_matching_cycle(entry, upstream_slot) or unknown
        for existing_cycle, existing_entries in buckets:
            if existing_cycle.dir_name == cycle.dir_name:
                existing_entries.append(entry)
                break
        else:
            buckets.append((cycle, [entry]))

    return buckets


def _find_matching_cycle(
    entry: MechLogEntry,
    upstream_slot: MechSlotOutput | None,
) -> MechBoardCycle | None:
    if upstream_slot is None or entry.timestamp is None:
        return None
    for cycle in upstream_slot.board_cycles:
        if cycle.start_time is None or cycle.end_time is None:
            continue
        if cycle.start_time <= entry.timestamp <= cycle.end_time:
            return cycle
    return None


def _build_cycle(
    cycle_template: MechBoardCycle,
    entries: list[MechLogEntry],
) -> MechBoardCycle:
    return MechBoardCycle(
        dir_name=cycle_template.dir_name,
        start_time=cycle_template.start_time,
        end_time=cycle_template.end_time,
        processes=_build_processes(entries),
    )


def _build_processes(entries: list[MechLogEntry]) -> list[MechProcessLifecycle]:
    by_key: dict[tuple[str, str, str], list[MechLogEntry]] = defaultdict(list)
    for entry in entries:
        by_key[(entry.process_name, entry.pid, entry.cpu_id or "")].append(entry)

    processes: list[MechProcessLifecycle] = []
    for (process_name, pid, _cpu_id), logs in sorted(by_key.items()):
        logs.sort(key=lambda e: (
            0 if e.timestamp else 1,
            e.timestamp.timestamp() if e.timestamp else 0,
            e.source_file,
            e.raw,
        ))
        processes.append(MechProcessLifecycle(
            process_name=process_name,
            pid=pid,
            logs=logs,
            total_count=len(logs),
            missing_sequences=[],
        ))
    return processes
```

- [ ] **Step 2: Run module2 tests**

Run:

```bash
python -m pytest tests/test_module2_plugin.py -q
```

Expected: PASS for validation and scan tests.

- [ ] **Step 3: Commit implementation**

```bash
git add backend/plugins/mechanisms/module2.py tests/test_module2_plugin.py
git commit -m "feat: assign module2 diagnostic logs to module1 cycles"
```

---

### Task 6: Test Unknown Cycle Fallback

**Files:**
- Modify: `tests/test_module2_plugin.py`
- Test: `python -m pytest tests/test_module2_plugin.py::test_module2_logs_outside_module1_cycle_go_to_unknown -q`

- [ ] **Step 1: Add unknown fallback test**

Add this test:

```python
def test_module2_logs_outside_module1_cycle_go_to_unknown(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T02:10:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="outside cycle"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    cycle = mech.slots[0].board_cycles[0]
    assert cycle.dir_name == "unknown"
    assert cycle.start_time is None
    assert cycle.end_time is None
    assert cycle.processes[0].logs[0].context == "outside cycle"
```

- [ ] **Step 2: Add missing dependency test**

Add this test:

```python
def test_module2_missing_dependency_records_error(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:10:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="xxxxx"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(diagnostic_slots=[slot])
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is None
    assert any("depends_on_module='module1' result not found" in e for e in result.errors)
```

- [ ] **Step 3: Run tests**

Run:

```bash
python -m pytest tests/test_module2_plugin.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit fallback tests**

```bash
git add tests/test_module2_plugin.py
git commit -m "test: cover module2 unknown cycle fallback"
```

---

### Task 7: Test Existing Writer Layout With Module2

**Files:**
- Modify: `tests/test_module2_plugin.py`
- Test: `python -m pytest tests/test_module2_plugin.py::test_module2_output_uses_existing_mech_layout -q`

- [ ] **Step 1: Add writer test**

Add imports:

```python
from backend.parsing.output_writer import MechOutputWriter
```

Add this test:

```python
def test_module2_output_uses_existing_mech_layout(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        '2026-01-03T00:10:00+08:00 xxx Slot=2,CPU-Id=3,'
        'ProcessName=hellokitty[123],Context="xxxxx"\n',
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="2", name="slot_2", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(
        diagnostic_slots=[slot],
        mech_results=[_module1_result()],
    )
    plugin = Module2Plugin(
        _module2_config(),
        module_key="module2",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)
    assert mech is not None
    mech_dir = MechOutputWriter().write(mech, tmp_path / "output")

    out_file = (
        mech_dir
        / "slot_2"
        / "20260103T000000-20260103T010000"
        / "cpu_3"
        / "hellokitty-123.log"
    )
    assert out_file.is_file()
    assert "Context=\"xxxxx\"" in out_file.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run writer test**

Run:

```bash
python -m pytest tests/test_module2_plugin.py::test_module2_output_uses_existing_mech_layout -q
```

Expected: PASS.

- [ ] **Step 3: Commit writer coverage**

```bash
git add tests/test_module2_plugin.py
git commit -m "test: cover module2 output layout"
```

---

### Task 8: Add Config Example And README Notes

**Files:**
- Modify: `config.yaml`
- Modify: `README.md`
- Test: `python cli.py check-config -c config.yaml`

- [ ] **Step 1: Add module2 config example after module1**

In `config.yaml`, after the `module1` config block and before the `compact` product section, add this commented example:

```yaml
          # module2:
          #   plugin: "backend.plugins.mechanisms.module2.Module2Plugin"
          #   enabled: true
          #   config:
          #     module_name: "MODULE2"
          #     identifying_keyword: "xxx"
          #     depends_on_module: "module1"
          #     diag_pattern: 'Slot=(?P<Slot>\d+),CPU-Id=(?P<CPU_Id>\d+),ProcessName=(?P<ProcessName>[^,]+),Context="(?P<Context>.*?)"'
```

Keep it commented unless the product should parse module2 by default. Since user asked to add module2, uncomment it only if module2 should run for every default parse.

- [ ] **Step 2: Update README workflow description**

In `README.md`, add a paragraph near the mechanism module section:

```markdown
`module2` 是诊断日志-only 的机制模块示例。它依赖 `module1` 的生命周期切分结果，不自行切周期；解析到的 module2 日志会按 slot 和时间归入 module1 对应周期，无法匹配周期的日志写入 `unknown/`。
```

Add changelog item:

```markdown
- 2026-05-26：新增 `module2` 机制模块设计。module2 只扫描诊断日志，复用 module1 生命周期切分结果落盘；未匹配周期的日志写入 `unknown/`。
```

- [ ] **Step 3: Run config check**

Run:

```bash
python cli.py check-config -c config.yaml
```

Expected: PASS.

- [ ] **Step 4: Commit docs/config**

```bash
git add config.yaml README.md
git commit -m "docs: document module2 dependent cycle module"
```

---

### Task 9: Full Regression Verification

**Files:**
- No source files modified in this task.
- Test: `python -m pytest tests/ -q`

- [ ] **Step 1: Run focused module tests**

Run:

```bash
python -m pytest tests/test_module2_plugin.py tests/test_module1_plugin.py tests/test_plugin_loader.py tests/test_config_validation.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
python -m pytest tests/ -q
```

Expected: PASS.

- [ ] **Step 3: Run config validation**

Run:

```bash
python cli.py check-config -c config.yaml
```

Expected: PASS.

- [ ] **Step 4: Run whitespace check**

Run:

```bash
git diff --check HEAD
```

Expected: no trailing whitespace or conflict marker errors.

- [ ] **Step 5: Inspect final diff**

Run:

```bash
git diff --stat
git diff -- backend/models.py backend/plugins/mechanisms/module1.py backend/plugins/mechanisms/module2.py tests/test_module2_plugin.py config.yaml README.md
```

Expected changes:

- `MechResult` has `module_key`.
- Module1 sets `module_key`.
- Module2 plugin scans diagnostic logs only.
- Module2 depends on module1 cycles.
- Unknown fallback exists.
- Existing writer layout is reused.

- [ ] **Step 6: Commit if task commits were skipped**

If the earlier task commits were not created, run:

```bash
git add backend/models.py backend/plugins/mechanisms/module1.py backend/plugins/mechanisms/module2.py tests/test_module1_plugin.py tests/test_module2_plugin.py config.yaml README.md
git commit -m "feat: add module2 dependent cycle output"
```

---

## Self-Review

Spec coverage:

- Module2 only scans diagnostic logs: Task 5.
- Stage1 keyword prefilter: Task 5.
- Slot, CPU, process name, context named groups: Task 1 and Task 3 validation.
- `hellokitty[123]` bracket PID parsing: Task 4 and Task 5.
- Reuses module1 lifecycle directories: Task 4 and Task 5.
- Unmatched logs go to `unknown/`: Task 6.
- Output layout matches module1: Task 7.
- Missing module1 dependency reports error: Task 6.

Placeholder scan:

- No unspecified code blocks remain.
- Every test and implementation step includes exact files and commands.

Type consistency:

- `MechResult.module_key` is a string with default `""`.
- `depends_on_module` matches `MechResult.module_key`.
- Module2 returns normal `MechResult`, so no writer changes are needed.
- Unknown cycle is represented by `MechBoardCycle(dir_name="unknown")`.
