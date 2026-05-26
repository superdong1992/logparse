# Module1 Slot Sequence Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow `module1` logs to be parsed when a software version omits `No[n]`, while preserving `No`-based ordering and sequence diagnostics for versions that include it.

**Architecture:** Treat `No[n]` as a slot-family cycle capability rather than a required field. `Module1Plugin` still scans all module1 entries, `CycleDetector` still builds board cycles by slot with board-level and CPU-level entries together, and each cycle chooses one output ordering mode: sequence mode when all entries have `No`, timestamp mode when none do, timestamp fallback with a warning if the invariant is violated.

**Tech Stack:** Python 3.11+, Pydantic models, pytest, Click CLI, YAML configuration.

---

## Scope And Invariants

The implementation must enforce these domain rules:

- A `slot family` means one slot plus all CPU subcards under that slot. For example, `slot_1`, `slot_1_cpu_1`, and `slot_1_cpu_2` all belong to slot family `slot=1`.
- Within one startup/version cycle for a slot family, either all module1 entries have `No[n]` or none of them do.
- A single process lifecycle will not contain a mix of entries with and without `No[n]`.
- If a slot family cycle has `No[n]`, all module1 processes on the slot family have `No[n]`.
- A compressed log package can contain multiple startups with different software versions. One board cycle can be no-sequence timestamp mode, and a later board cycle can be sequence mode.
- `No[n]` is not required for module1 parsing. It is an enhancement for output ordering, missing sequence reporting, and journal sequence wrap detection.

Out of scope:

- Inferring restart boundaries purely from `No[n]` when PID and timestamps are unavailable.
- Introducing a new version detector. The version capability is inferred from parsed entries in each board cycle.
- Changing the output directory layout.

---

## Current Code Map

- Modify: `backend/parsing/mech_diag_scanner.py`
  - Current behavior drops diagnostic lines when `sequence_pattern` does not match.
  - New behavior keeps the line and stores `sequence=0`.

- Modify: `backend/parsing/mech_journal_scanner.py`
  - Current behavior assumes journal regex groups are `process_name`, `pid`, `sequence`, `context`.
  - New behavior accepts both positional 4-group patterns with sequence and positional 3-group patterns without sequence.

- Modify: `backend/config_validation.py`
  - Current journal validation requires at least four capture groups.
  - New validation accepts three groups for no-sequence journal formats and four groups for sequence formats.

- Modify: `backend/parsing/cycle_detector.py`
  - Current process log ordering always uses timestamp then sequence.
  - New ordering chooses per board cycle: `sequence` mode, `timestamp` mode, or mixed fallback.

- Modify: `cli.py`
  - Current `test-pattern` assumes journal group 3 is sequence and group 4 is context.
  - New behavior prints `序号: 无` for 3-group no-sequence journal patterns and prints context from the correct group.

- Modify: `config.yaml`
  - Add comments and examples showing how to configure journal patterns for logs without `No[n]`.

- Modify tests:
  - `tests/test_module1_plugin.py`
  - `tests/test_cycle_detector.py`
  - `tests/test_config_validation.py`
  - `tests/test_cli.py`

---

## Data Representation

Keep the existing `MechLogEntry.sequence: int = 0` field in `backend/models.py`.

Meaning:

- `sequence > 0`: the original log line contained a valid `No[n]`.
- `sequence == 0`: the original log line did not contain `No[n]`, or the configured sequence regex did not produce an integer.

Do not convert `sequence` to `int | None` in this change. The current writer already prints a four-dot placeholder when `sequence` is falsy, and existing tests and JSON output expect an integer field.

---

## Sorting Rules

Cycle mode is computed inside `CycleDetector._make_cycles(entries)` because that method receives one full board cycle containing the slot body and all CPU subcards.

Mode selection:

```python
def _sequence_mode(entries: list[MechLogEntry]) -> str:
    sequenced = sum(1 for e in entries if e.sequence > 0)
    if sequenced == len(entries):
        return "sequence"
    if sequenced == 0:
        return "timestamp"
    logger.warning(
        "module1 cycle has mixed sequence availability: %d/%d entries have sequence; "
        "falling back to timestamp ordering",
        sequenced,
        len(entries),
    )
    return "timestamp"
```

Process log ordering:

- `sequence` mode: sort each process lifecycle by `sequence`, then timestamp, source file, raw line.
- `timestamp` mode: sort each process lifecycle by timestamp, then source file, raw line.
- Missing sequence detection runs only in `sequence` mode.

Cycle splitting continues to use timestamp/PID ordering. This keeps restart detection stable for both old and new log versions.

---

### Task 1: Add Tests For No-Sequence Diagnostic Parsing

**Files:**
- Modify: `tests/test_module1_plugin.py`
- Test command: `python -m pytest tests/test_module1_plugin.py::test_module1_plugin_parses_diag_entries_without_no -q`

- [ ] **Step 1: Add a failing diagnostic parsing test**

Add this test to `tests/test_module1_plugin.py`:

```python
def test_module1_plugin_parses_diag_entries_without_no(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        "2026-01-03T00:01:00 EXAMPLE Service=SERVICE; Slot=1; CPU-Id=0; "
        "ProcessName=SERVICE-12345; Context=ACTIVE without sequence)\n",
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="1", name="slot_1", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(diagnostic_slots=[slot])
    plugin = Module1Plugin(
        _module1_config(),
        module_key="module1",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    cycle = mech.slots[0].board_cycles[0]
    proc = cycle.processes[0]
    assert proc.process_name == "SERVICE"
    assert proc.logs[0].sequence == 0
    assert proc.missing_sequences == []
    assert mech.active_master_slots == ["1"]
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
python -m pytest tests/test_module1_plugin.py::test_module1_plugin_parses_diag_entries_without_no -q
```

Expected: FAIL because `MechDiagScanner.scan()` currently skips lines without a sequence match.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_module1_plugin.py
git commit -m "test: cover module1 diagnostic logs without sequence"
```

---

### Task 2: Keep Diagnostic Entries When `No` Is Missing

**Files:**
- Modify: `backend/parsing/mech_diag_scanner.py`
- Test command: `python -m pytest tests/test_module1_plugin.py::test_module1_plugin_parses_diag_entries_without_no -q`

- [ ] **Step 1: Add a helper for optional sequence extraction**

In `backend/parsing/mech_diag_scanner.py`, add this method inside `MechDiagScanner`:

```python
    def _extract_sequence(self, line: str) -> int:
        sm = self._seq_re.search(line)
        if not sm:
            return 0
        try:
            return int(sm.group(1))
        except (IndexError, ValueError):
            return 0
```

- [ ] **Step 2: Replace the mandatory sequence block**

Replace this block:

```python
            sm = self._seq_re.search(line)
            if not sm:
                continue
            try:
                seq = int(sm.group(1))
            except ValueError:
                continue
```

with:

```python
            seq = self._extract_sequence(line)
```

- [ ] **Step 3: Run the focused test**

Run:

```bash
python -m pytest tests/test_module1_plugin.py::test_module1_plugin_parses_diag_entries_without_no -q
```

Expected: PASS.

- [ ] **Step 4: Commit the implementation**

```bash
git add backend/parsing/mech_diag_scanner.py
git commit -m "fix: keep module1 diagnostic logs without sequence"
```

---

### Task 3: Add Tests For No-Sequence Journal Parsing

**Files:**
- Modify: `tests/test_module1_plugin.py`
- Test command: `python -m pytest tests/test_module1_plugin.py::test_module1_plugin_parses_journal_entries_without_no -q`

- [ ] **Step 1: Add a journal config helper**

Add this helper to `tests/test_module1_plugin.py`:

```python
def _module1_journal_no_sequence_config() -> dict:
    cfg = _module1_config()
    cfg["diag_pattern"] = ""
    cfg["journal"] = {
        "line_pattern": "",
        "line_pattern2": r"^\S+\s+\S+\s+(\S+?)(?:-(\d+))?:\s+(.+)$",
        "identifying_keyword": "example",
    }
    return cfg
```

- [ ] **Step 2: Add a failing journal parsing test**

Add this test to `tests/test_module1_plugin.py`:

```python
def test_module1_plugin_parses_journal_entries_without_no(tmp_path):
    journal_file = tmp_path / "journal.log"
    journal_file.write_text(
        "2026-01-03T00:01:00 host SERVICE-12345: EXAMPLE started without sequence\n",
        encoding="utf-8",
    )
    private_slot = PrivateSlotInfo(
        dir_name="slot_1",
        slot_id="1",
        path=str(tmp_path),
        journal_logs=[
            JournalLogFile(path=str(journal_file), name="journal.log"),
        ],
    )
    result = ParseResult(private_slots=[private_slot])
    plugin = Module1Plugin(
        _module1_journal_no_sequence_config(),
        module_key="module1",
        ts_extractor=_timestamp_extractor(),
    )

    mech = plugin.parse(result)

    assert mech is not None
    assert mech.journal_entry_count == 1
    proc = mech.slots[0].board_cycles[0].processes[0]
    assert proc.process_name == "SERVICE"
    assert proc.pid == "12345"
    assert proc.logs[0].sequence == 0
    assert proc.logs[0].context == "EXAMPLE started without sequence"
```

Update the imports at the top of `tests/test_module1_plugin.py`:

```python
from backend.models import (
    BoardRole,
    JournalLogFile,
    LogEntry,
    MechResult,
    ParseResult,
    PrivateSlotInfo,
    SlotInfo,
)
```

- [ ] **Step 3: Run the test and verify it fails**

Run:

```bash
python -m pytest tests/test_module1_plugin.py::test_module1_plugin_parses_journal_entries_without_no -q
```

Expected: FAIL because `MechJournalScanner.scan()` currently reads group 3 as sequence and group 4 as context.

- [ ] **Step 4: Commit the failing test**

```bash
git add tests/test_module1_plugin.py
git commit -m "test: cover module1 journal logs without sequence"
```

---

### Task 4: Support 3-Group Journal Patterns Without `No`

**Files:**
- Modify: `backend/parsing/mech_journal_scanner.py`
- Test command: `python -m pytest tests/test_module1_plugin.py::test_module1_plugin_parses_journal_entries_without_no -q`

- [ ] **Step 1: Add a helper for positional journal fields**

Add this method inside `MechJournalScanner`:

```python
    @staticmethod
    def _extract_positional_fields(m: re.Match) -> tuple[str, str | None, int, str]:
        raw_name = m.group(1)
        raw_pid = m.group(2) if m.re.groups >= 2 else None

        if m.re.groups >= 4:
            seq_str = m.group(3)
            context = m.group(4)
            try:
                seq = int(seq_str)
            except (TypeError, ValueError):
                seq = 0
            return raw_name, raw_pid, seq, context

        context = m.group(3)
        return raw_name, raw_pid, 0, context
```

- [ ] **Step 2: Use the helper in `scan()`**

Replace this block:

```python
                raw_name = m.group(1)
                raw_pid = m.group(2)
                seq_str = m.group(3)
                context = m.group(4)

                try:
                    seq = int(seq_str)
                except ValueError:
                    seq = 0
```

with:

```python
                raw_name, raw_pid, seq, context = self._extract_positional_fields(m)
```

- [ ] **Step 3: Run focused scanner tests**

Run:

```bash
python -m pytest tests/test_module1_plugin.py::test_module1_plugin_parses_journal_entries_without_no tests/test_module1_plugin.py::test_module1_plugin_parses_diag_entries -q
```

Expected: both tests PASS.

- [ ] **Step 4: Commit the implementation**

```bash
git add backend/parsing/mech_journal_scanner.py
git commit -m "fix: support module1 journal logs without sequence"
```

---

### Task 5: Add Cycle-Level Sorting Tests

**Files:**
- Modify: `tests/test_cycle_detector.py`
- Test command: `python -m pytest tests/test_cycle_detector.py::TestSequenceModeSelection -q`

- [ ] **Step 1: Add a helper for entries without sequence**

Add this helper near the existing `_entry()` helper in `tests/test_cycle_detector.py`:

```python
def _entry_without_seq(
    proc: str,
    pid: str,
    ts: datetime,
    cpu_id: str = "",
    source: str = "diagnostic",
) -> MechLogEntry:
    return MechLogEntry(
        timestamp=ts,
        source=source,
        slot="1",
        cpu_id=cpu_id,
        process_name=proc,
        pid=pid,
        sequence=0,
        raw=f"{proc}-{pid}-no-sequence",
    )
```

- [ ] **Step 2: Add tests for timestamp mode and sequence mode**

Add this class to `tests/test_cycle_detector.py`:

```python
class TestSequenceModeSelection:
    def test_cycle_without_sequences_orders_process_logs_by_timestamp(self):
        det = CycleDetector(indicator=None)
        entries = [
            _entry_without_seq("svc", "100", _ts(1, 3, 0, 3)),
            _entry_without_seq("svc", "100", _ts(1, 3, 0, 1)),
            _entry_without_seq("svc", "100", _ts(1, 3, 0, 2), cpu_id="1"),
        ]

        cycles = det.detect(entries)

        assert len(cycles) == 1
        proc = [p for p in cycles[0].processes if p.pid == "100" and p.logs[0].cpu_id == ""][0]
        assert [log.timestamp for log in proc.logs] == [
            _ts(1, 3, 0, 1),
            _ts(1, 3, 0, 3),
        ]
        assert proc.missing_sequences == []

    def test_cycle_with_sequences_orders_process_logs_by_sequence(self):
        det = CycleDetector(indicator=None)
        entries = [
            _entry("svc", "100", 3, _ts(1, 3, 0, 1)),
            _entry("svc", "100", 1, _ts(1, 3, 0, 3)),
            _entry("svc", "100", 2, _ts(1, 3, 0, 2)),
        ]

        cycles = det.detect(entries)

        proc = cycles[0].processes[0]
        assert [log.sequence for log in proc.logs] == [1, 2, 3]
        assert proc.missing_sequences == []

    def test_mixed_sequence_availability_warns_and_uses_timestamp(self, caplog):
        det = CycleDetector(indicator=None)
        entries = [
            _entry("svc", "100", 3, _ts(1, 3, 0, 1)),
            _entry_without_seq("svc", "100", _ts(1, 3, 0, 2)),
        ]

        with caplog.at_level(logging.WARNING, logger="backend.parsing.cycle_detector"):
            cycles = det.detect(entries)

        proc = cycles[0].processes[0]
        assert [log.timestamp for log in proc.logs] == [
            _ts(1, 3, 0, 1),
            _ts(1, 3, 0, 2),
        ]
        assert proc.missing_sequences == []
        assert "mixed sequence availability" in caplog.text
```

- [ ] **Step 3: Run the tests and verify they fail**

Run:

```bash
python -m pytest tests/test_cycle_detector.py::TestSequenceModeSelection -q
```

Expected: at least `test_cycle_with_sequences_orders_process_logs_by_sequence` FAILS because current lifecycle ordering uses timestamp before sequence.

- [ ] **Step 4: Commit the failing tests**

```bash
git add tests/test_cycle_detector.py
git commit -m "test: cover module1 cycle sequence mode selection"
```

---

### Task 6: Implement Cycle-Level Sequence Mode

**Files:**
- Modify: `backend/parsing/cycle_detector.py`
- Test command: `python -m pytest tests/test_cycle_detector.py::TestSequenceModeSelection -q`

- [ ] **Step 1: Add `_sequence_mode()`**

Add this static method inside `CycleDetector`:

```python
    @staticmethod
    def _sequence_mode(entries: list[MechLogEntry]) -> str:
        if not entries:
            return "timestamp"
        sequenced = sum(1 for e in entries if e.sequence > 0)
        if sequenced == len(entries):
            return "sequence"
        if sequenced == 0:
            return "timestamp"
        logger.warning(
            "module1 cycle has mixed sequence availability: %d/%d entries have sequence; "
            "falling back to timestamp ordering",
            sequenced,
            len(entries),
        )
        return "timestamp"
```

- [ ] **Step 2: Pass mode from `_make_cycles()` to `_build_processes()`**

Replace:

```python
        procs = CycleDetector._build_processes(entries)
```

with:

```python
        sequence_mode = CycleDetector._sequence_mode(entries)
        procs = CycleDetector._build_processes(entries, sequence_mode)
```

- [ ] **Step 3: Change `_build_processes()` signature**

Replace:

```python
    def _build_processes(
        entries: list[MechLogEntry],
    ) -> list[MechProcessLifecycle]:
```

with:

```python
    def _build_processes(
        entries: list[MechLogEntry],
        sequence_mode: str,
    ) -> list[MechProcessLifecycle]:
```

- [ ] **Step 4: Add lifecycle sort helpers**

Add these static methods inside `CycleDetector`:

```python
    @staticmethod
    def _timestamp_sort_key(e: MechLogEntry) -> tuple[int, float, str, str]:
        return (
            0 if e.timestamp else 1,
            e.timestamp.timestamp() if e.timestamp else 0,
            e.source_file,
            e.raw,
        )

    @staticmethod
    def _sequence_sort_key(e: MechLogEntry) -> tuple[int, int, int, float, str, str]:
        return (
            0 if e.sequence > 0 else 1,
            e.sequence if e.sequence > 0 else 0,
            0 if e.timestamp else 1,
            e.timestamp.timestamp() if e.timestamp else 0,
            e.source_file,
            e.raw,
        )
```

- [ ] **Step 5: Use the mode in lifecycle sorting and missing sequence detection**

Replace:

```python
            logs.sort(key=lambda e: (
                0 if e.timestamp else 1,
                e.timestamp.timestamp() if e.timestamp else 0,
                e.sequence,
            ))
            seqs = [l.sequence for l in logs if l.sequence > 0]
            missing: list[int] = []
            if len(seqs) >= 2:
                full = set(range(min(seqs), max(seqs) + 1))
                missing = sorted(full - set(seqs))
```

with:

```python
            if sequence_mode == "sequence":
                logs.sort(key=CycleDetector._sequence_sort_key)
                seqs = [l.sequence for l in logs if l.sequence > 0]
                missing: list[int] = []
                if len(seqs) >= 2:
                    full = set(range(min(seqs), max(seqs) + 1))
                    missing = sorted(full - set(seqs))
            else:
                logs.sort(key=CycleDetector._timestamp_sort_key)
                missing = []
```

- [ ] **Step 6: Run focused cycle tests**

Run:

```bash
python -m pytest tests/test_cycle_detector.py::TestSequenceModeSelection -q
```

Expected: PASS.

- [ ] **Step 7: Run existing cycle detector tests**

Run:

```bash
python -m pytest tests/test_cycle_detector.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit the implementation**

```bash
git add backend/parsing/cycle_detector.py
git commit -m "fix: choose module1 ordering mode per slot cycle"
```

---

### Task 7: Relax Journal Pattern Validation

**Files:**
- Modify: `tests/test_config_validation.py`
- Modify: `backend/config_validation.py`
- Test command: `python -m pytest tests/test_config_validation.py::TestValidateMechanismModuleConfig -q`

- [ ] **Step 1: Add validation tests**

Add these tests to `TestValidateMechanismModuleConfig` in `tests/test_config_validation.py`:

```python
    def test_journal_pattern_with_three_groups_passes_for_no_sequence(self):
        cfg = {
            "module_name": "EXAMPLE",
            "journal": {"line_pattern2": r"(\S+?)(?:-(\d+))?:\s+(.+)"},
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert not any("捕获组" in e or "崟鑾风粍" in e for e in errors)

    def test_journal_pattern_with_two_groups_fails(self):
        cfg = {
            "module_name": "EXAMPLE",
            "journal": {"line_pattern2": r"(\S+):\s+(.+)"},
        }
        errors = validate_mechanism_module_config("module1", cfg)
        assert errors
        assert "journal.line_pattern2" in errors[0]
```

- [ ] **Step 2: Run validation tests and verify failure**

Run:

```bash
python -m pytest tests/test_config_validation.py::TestValidateMechanismModuleConfig -q
```

Expected: FAIL because 3-group journal patterns are currently rejected.

- [ ] **Step 3: Change capture group validation**

In `backend/config_validation.py`, replace:

```python
            if compiled.groups < 4:
                errors.append(
                    f"mechanism_modules.{module_key}.journal.{field} 至少需要 4 个捕获组: "
                    "process_name, pid, sequence, context"
                )
```

with:

```python
            if compiled.groups not in (3, 4):
                errors.append(
                    f"mechanism_modules.{module_key}.journal.{field} 需要 3 或 4 个捕获组: "
                    "3组=process_name, pid, context；4组=process_name, pid, sequence, context"
                )
```

The repository currently contains mojibake in some Chinese comments and messages. Preserve nearby text style if editing with an existing encoding, but keep the new validation message readable UTF-8 if the file remains UTF-8.

- [ ] **Step 4: Run validation tests**

Run:

```bash
python -m pytest tests/test_config_validation.py::TestValidateMechanismModuleConfig -q
```

Expected: PASS.

- [ ] **Step 5: Commit validation changes**

```bash
git add backend/config_validation.py tests/test_config_validation.py
git commit -m "fix: allow module1 journal patterns without sequence"
```

---

### Task 8: Update CLI `test-pattern`

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `cli.py`
- Test command: `python -m pytest tests/test_cli.py -q`

- [ ] **Step 1: Add a CLI regression test for no-sequence journal pattern**

Add this test to `tests/test_cli.py`:

```python
def test_test_pattern_journal_without_sequence(sample_config, tmp_path):
    sample_config["mechanism_modules"]["module1"]["config"]["journal"] = {
        "line_pattern": "",
        "line_pattern2": r"^\S+\s+\S+\s+(\S+?)(?:-(\d+))?:\s+(.+)$",
        "identifying_keyword": "example",
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "products": {
                    "default": {
                        "log_parser": {
                            "config": sample_config,
                        },
                    },
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    line = "2026-01-03T00:01:00 host SERVICE-12345: EXAMPLE without sequence"

    result = CliRunner().invoke(
        cli,
        [
            "test-pattern",
            "-c",
            str(config_path),
            "-m",
            "module1",
            "-t",
            "journal",
            line,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "SERVICE" in result.output
    assert "12345" in result.output
    assert "EXAMPLE without sequence" in result.output
```

- [ ] **Step 2: Run the CLI test and verify it fails**

Run:

```bash
python -m pytest tests/test_cli.py::test_test_pattern_journal_without_sequence -q
```

Expected: FAIL because `cli.py` currently reads `m.group(4)` for context.

- [ ] **Step 3: Update journal output logic**

In `cli.py`, replace:

```python
        click.echo(f"  进程名: {m.group(1)}")
        if m.group(2):
            click.echo(f"  pid: {m.group(2)}")
        click.echo(f"  序号: {m.group(3)}")
        click.echo(f"  Context: {m.group(4)}")
```

with:

```python
        click.echo(f"  进程名: {m.group(1)}")
        if m.group(2):
            click.echo(f"  pid: {m.group(2)}")
        if m.re.groups >= 4:
            click.echo(f"  序号: {m.group(3)}")
            click.echo(f"  Context: {m.group(4)}")
        else:
            click.echo("  序号: 无")
            click.echo(f"  Context: {m.group(3)}")
```

The checked-in file currently displays mojibake for some Chinese strings in PowerShell output. Edit the semantic block, not the rendered mojibake, and verify the test output contains the expected ASCII fragments.

- [ ] **Step 4: Run CLI tests**

Run:

```bash
python -m pytest tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit CLI changes**

```bash
git add cli.py tests/test_cli.py
git commit -m "fix: show no-sequence module1 journal pattern results"
```

---

### Task 9: Update Config Examples

**Files:**
- Modify: `config.yaml`
- Test command: `python cli.py check-config -c config.yaml`

- [ ] **Step 1: Update journal comments for no-sequence patterns**

In `config.yaml`, update the comments above `journal.line_pattern` and `journal.line_pattern2` to state:

```yaml
                # journal pattern supports two capture layouts:
                # 4 groups: process_name, pid, sequence, context
                # 3 groups: process_name, pid, context (used by versions without No)
```

- [ ] **Step 2: Add commented examples**

Add these commented examples under the existing default `line_pattern2`:

```yaml
                # Example without No:
                # line_pattern2: '^\S+\s+\S+\s+(\S+?)(?:-(\d+))?:\s+(.+)$'
                # Example with metadata but without No:
                # line_pattern: '^\S+\s+\S+\s+\S+?:\s+\[slotId\s*=\s*\d+,\s*cpuId\s*=\s*\d+,\s*processName\s*=\s*(\S+?)(?:-(\d+))?\]:\s+(.+)$'
```

- [ ] **Step 3: Run config validation**

Run:

```bash
python cli.py check-config -c config.yaml
```

Expected: command exits 0 and reports no config errors.

- [ ] **Step 4: Commit config docs**

```bash
git add config.yaml
git commit -m "docs: document module1 no-sequence journal patterns"
```

---

### Task 10: Full Regression Verification

**Files:**
- No source files modified in this task.
- Test command: `python -m pytest tests/ -q`

- [ ] **Step 1: Run focused module1 tests**

Run:

```bash
python -m pytest tests/test_module1_plugin.py tests/test_cycle_detector.py tests/test_config_validation.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
python -m pytest tests/ -q
```

Expected: PASS.

- [ ] **Step 3: Run config check**

Run:

```bash
python cli.py check-config -c config.yaml
```

Expected: PASS with no config errors.

- [ ] **Step 4: Inspect git diff**

Run:

```bash
git diff --stat
git diff -- backend/parsing/mech_diag_scanner.py backend/parsing/mech_journal_scanner.py backend/parsing/cycle_detector.py backend/config_validation.py cli.py config.yaml tests/test_module1_plugin.py tests/test_cycle_detector.py tests/test_config_validation.py tests/test_cli.py
```

Expected:

- Diagnostic scanner keeps no-sequence lines.
- Journal scanner supports 3-group no-sequence patterns.
- Cycle detector selects sequence mode per full slot-family cycle.
- Missing sequence detection is skipped in timestamp mode.
- CLI and validation understand 3-group journal patterns.

- [ ] **Step 5: Final commit if previous tasks were not committed separately**

If task-level commits were skipped during execution, create one final commit:

```bash
git add backend/parsing/mech_diag_scanner.py backend/parsing/mech_journal_scanner.py backend/parsing/cycle_detector.py backend/config_validation.py cli.py config.yaml tests/test_module1_plugin.py tests/test_cycle_detector.py tests/test_config_validation.py tests/test_cli.py
git commit -m "fix: support module1 logs without sequence numbers"
```

---

## Self-Review

Spec coverage:

- Parses module1 diagnostic logs without `No[n]`: Task 1 and Task 2.
- Parses module1 journal logs without `No[n]`: Task 3 and Task 4.
- Treats `No` availability as full slot-family cycle state: Task 6 computes mode in `_make_cycles()` after timestamp segmentation across board and CPU entries.
- Supports mixed software versions in one compressed package: Task 6 computes mode per cycle, not globally per package.
- Preserves `No` sorting for versions with `No`: Task 5 and Task 6 sequence-mode tests and implementation.
- Falls back to timestamp sorting for versions without `No`: Task 5 and Task 6 timestamp-mode tests and implementation.
- Skips sequence missing diagnostics in no-sequence cycles: Task 5 and Task 6.
- Keeps journal sequence wrap optimization only where sequence exists: existing `_find_seq_jump_for_process()` returns no candidate when `old_max_seq <= 0`, and no task changes that behavior.
- Updates user-facing config and CLI tooling: Task 7, Task 8, and Task 9.

Placeholder scan:

- No incomplete sections are left for the implementer.
- Each code-changing task includes exact file paths, code snippets, commands, and expected results.

Type consistency:

- `MechLogEntry.sequence` remains `int`.
- `sequence == 0` consistently means sequence unavailable.
- `_build_processes()` receives `sequence_mode: str` from `_make_cycles()`.
- Journal positional parsing returns `(raw_name, raw_pid, seq, context)` for both 3-group and 4-group regex layouts.
