# Module1 Auto Journal No-Sequence Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `module1` journal parsing automatically support logs without `No[n]` while keeping the existing `config.yaml` patterns unchanged for logs that still include sequence numbers.

**Architecture:** Add a small shared journal pattern matcher that first tries the configured 4-group journal regexes exactly as today, then derives narrow 3-group no-sequence fallback regexes by removing the configured sequence regex from those same patterns. `MechJournalScanner` and `cli.py test-pattern` both use this matcher, so runtime parsing and debugging behavior stay aligned. The fallback is only attempted when the log line does not contain `No[`, preventing malformed sequence lines from being silently accepted as no-sequence logs.

**Tech Stack:** Python 3.11+, `re`, dataclasses, pytest, Click CLI, YAML configuration.

---

## Scope And Desired Behavior

This plan assumes the log format difference between old and new software versions is only the presence or absence of `No[n]`.

The user should not need to edit `config.yaml` when a package contains journal lines without `No[n]` as long as the configured journal pattern contains the standard sequence capture, for example:

```yaml
line_pattern2: '^\S+\s+\S+\s+(\S+?)(?:-(\d+))?:\s+No\[(\d+)\](.+)$'
```

Runtime behavior:

- A journal line with `No[n]` matches the configured pattern first and gets `sequence=n`.
- A journal line without `No[n]` fails the configured pattern, then matches the auto-derived fallback and gets `sequence=0`.
- A journal line containing literal `No[` but not matching the configured sequence pattern is rejected. This avoids silently treating malformed sequence lines as no-sequence lines.
- Manual 3-group no-sequence journal patterns remain supported for custom products, but they are no longer required for the standard "same format minus `No[n]`" case.

---

## File Structure

- Create: `backend/parsing/mech_journal_pattern.py`
  - Owns journal pattern matching and auto no-sequence fallback derivation.
  - Exposes `JournalPatternMatcher` and `JournalLineMatch`.

- Modify: `backend/parsing/mech_journal_scanner.py`
  - Replaces local positional group extraction with `JournalPatternMatcher`.
  - Keeps timestamp, role signal, process name resolving, and output entry construction unchanged.

- Modify: `cli.py`
  - Uses `JournalPatternMatcher` in `test-pattern -t journal`.
  - Prints whether the match used configured sequence mode or auto no-sequence fallback.

- Modify: `tests/test_module1_plugin.py`
  - Ensures default 4-group journal config parses no-sequence journal lines without manual config edits.

- Create: `tests/test_mech_journal_pattern.py`
  - Unit tests for fallback derivation, match ordering, malformed sequence rejection, and manual 3-group support.

- Modify: `tests/test_cli.py`
  - Changes the no-sequence journal CLI test so it keeps the existing config and proves no manual config edit is needed.

- Modify: `config.yaml`
  - Updates comments to say standard no-sequence journal logs are auto-derived from the sequence pattern.

- Modify: `README.md`
  - Updates the module1 no-sequence changelog note to clarify that journal fallback is automatic for the standard pattern.

---

## Matching Algorithm

The matcher builds candidates in this order:

1. Configured `journal.line_pattern`, if present.
2. Configured `journal.line_pattern2`, if present.
3. Auto-derived no-sequence fallback from `journal.line_pattern`, if derivation succeeds.
4. Auto-derived no-sequence fallback from `journal.line_pattern2`, if derivation succeeds.

Derivation is intentionally narrow:

```python
def _derive_no_sequence_pattern(pattern: str, seq_pattern: str) -> str | None:
    markers = [seq_pattern, r"No\[(\d+)\]"]
    for marker in markers:
        if marker and pattern.count(marker) == 1:
            return pattern.replace(marker, "", 1)
    return None
```

The derived regex is compiled and accepted only if it has exactly 3 capture groups:

- group 1: process name
- group 2: pid, possibly optional
- group 3: context

The fallback matcher is skipped for any line containing literal `No[`.

---

### Task 1: Add Unit Tests For Journal Pattern Matching

**Files:**
- Create: `tests/test_mech_journal_pattern.py`
- Test: `python -m pytest tests/test_mech_journal_pattern.py -q`

- [ ] **Step 1: Create the failing test file**

Create `tests/test_mech_journal_pattern.py` with this content:

```python
from __future__ import annotations

import re

from backend.parsing.mech_journal_pattern import JournalPatternMatcher


def _matcher() -> JournalPatternMatcher:
    return JournalPatternMatcher(
        journal_re=None,
        journal_re2=re.compile(r"^\S+\s+\S+\s+(\S+?)(?:-(\d+))?:\s+No\[(\d+)\](.+)$"),
        seq_re=re.compile(r"No\[(\d+)\]"),
    )


def test_matches_configured_sequence_pattern_first():
    matcher = _matcher()

    result = matcher.match("2026-01-03T00:01:00 host SERVICE-12345: No[7] EXAMPLE ok")

    assert result is not None
    assert result.pattern_name == "journal.line_pattern2"
    assert result.auto_no_sequence is False
    assert result.raw_name == "SERVICE"
    assert result.raw_pid == "12345"
    assert result.sequence == 7
    assert result.context == " EXAMPLE ok"


def test_auto_fallback_matches_without_sequence_from_same_config():
    matcher = _matcher()

    result = matcher.match("2026-01-03T00:01:00 host SERVICE-12345: EXAMPLE old version")

    assert result is not None
    assert result.pattern_name == "journal.line_pattern2.auto_no_sequence"
    assert result.auto_no_sequence is True
    assert result.raw_name == "SERVICE"
    assert result.raw_pid == "12345"
    assert result.sequence == 0
    assert result.context == "EXAMPLE old version"


def test_auto_fallback_is_not_used_when_line_contains_malformed_no():
    matcher = _matcher()

    result = matcher.match("2026-01-03T00:01:00 host SERVICE-12345: No[bad] EXAMPLE corrupt")

    assert result is None


def test_manual_three_group_pattern_still_matches():
    matcher = JournalPatternMatcher(
        journal_re=None,
        journal_re2=re.compile(r"^\S+\s+\S+\s+(\S+?)(?:-(\d+))?:\s+(.+)$"),
        seq_re=re.compile(r"No\[(\d+)\]"),
    )

    result = matcher.match("2026-01-03T00:01:00 host SERVICE-12345: EXAMPLE manual")

    assert result is not None
    assert result.pattern_name == "journal.line_pattern2"
    assert result.auto_no_sequence is False
    assert result.sequence == 0
    assert result.context == "EXAMPLE manual"
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
python -m pytest tests/test_mech_journal_pattern.py -q
```

Expected: FAIL with `ModuleNotFoundError` because `backend.parsing.mech_journal_pattern` does not exist yet.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_mech_journal_pattern.py
git commit -m "test: cover automatic module1 journal no-sequence fallback"
```

---

### Task 2: Implement Shared Journal Pattern Matcher

**Files:**
- Create: `backend/parsing/mech_journal_pattern.py`
- Test: `python -m pytest tests/test_mech_journal_pattern.py -q`

- [ ] **Step 1: Create the matcher module**

Create `backend/parsing/mech_journal_pattern.py` with this content:

```python
"""Shared module1 journal pattern matching helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class JournalLineMatch:
    pattern_name: str
    raw_name: str
    raw_pid: str | None
    sequence: int
    context: str
    auto_no_sequence: bool = False


@dataclass(frozen=True)
class _PatternCandidate:
    name: str
    regex: re.Pattern
    has_sequence: bool
    auto_no_sequence: bool = False


class JournalPatternMatcher:
    def __init__(
        self,
        journal_re: re.Pattern | None,
        journal_re2: re.Pattern | None,
        seq_re: re.Pattern,
    ):
        configured = [
            _PatternCandidate("journal.line_pattern", journal_re, _has_sequence(journal_re))
            for journal_re in [journal_re]
            if journal_re is not None
        ] + [
            _PatternCandidate("journal.line_pattern2", journal_re2, _has_sequence(journal_re2))
            for journal_re2 in [journal_re2]
            if journal_re2 is not None
        ]
        self._configured = configured
        self._fallbacks = [
            candidate
            for configured_candidate in configured
            for candidate in [_derive_no_sequence_candidate(configured_candidate, seq_re)]
            if candidate is not None
        ]

    def match(self, line: str) -> JournalLineMatch | None:
        for candidate in self._configured:
            result = self._match_candidate(candidate, line)
            if result:
                return result

        if "No[" in line:
            return None

        for candidate in self._fallbacks:
            result = self._match_candidate(candidate, line)
            if result:
                return result

        return None

    @staticmethod
    def _match_candidate(candidate: _PatternCandidate, line: str) -> JournalLineMatch | None:
        m = candidate.regex.match(line)
        if not m:
            return None

        raw_name = m.group(1)
        raw_pid = m.group(2) if m.re.groups >= 2 else None

        if candidate.has_sequence:
            try:
                sequence = int(m.group(3))
            except (TypeError, ValueError):
                sequence = 0
            context = m.group(4)
        else:
            sequence = 0
            context = m.group(3)

        return JournalLineMatch(
            pattern_name=candidate.name,
            raw_name=raw_name,
            raw_pid=raw_pid,
            sequence=sequence,
            context=context,
            auto_no_sequence=candidate.auto_no_sequence,
        )


def _has_sequence(regex: re.Pattern | None) -> bool:
    return bool(regex and regex.groups >= 4)


def _derive_no_sequence_candidate(
    candidate: _PatternCandidate,
    seq_re: re.Pattern,
) -> _PatternCandidate | None:
    if not candidate.has_sequence:
        return None

    derived_pattern = _derive_no_sequence_pattern(candidate.regex.pattern, seq_re.pattern)
    if not derived_pattern:
        return None

    try:
        derived_re = re.compile(derived_pattern)
    except re.error:
        return None

    if derived_re.groups != 3:
        return None

    return _PatternCandidate(
        name=f"{candidate.name}.auto_no_sequence",
        regex=derived_re,
        has_sequence=False,
        auto_no_sequence=True,
    )


def _derive_no_sequence_pattern(pattern: str, seq_pattern: str) -> str | None:
    markers = [seq_pattern, r"No\[(\d+)\]"]
    for marker in markers:
        if marker and pattern.count(marker) == 1:
            return pattern.replace(marker, "", 1)
    return None
```

- [ ] **Step 2: Run matcher tests**

Run:

```bash
python -m pytest tests/test_mech_journal_pattern.py -q
```

Expected: PASS.

- [ ] **Step 3: Commit the matcher**

```bash
git add backend/parsing/mech_journal_pattern.py
git commit -m "feat: add module1 journal pattern matcher"
```

---

### Task 3: Use Matcher In `MechJournalScanner`

**Files:**
- Modify: `backend/parsing/mech_journal_scanner.py`
- Modify: `tests/test_module1_plugin.py`
- Test: `python -m pytest tests/test_module1_plugin.py tests/test_mech_journal_pattern.py -q`

- [ ] **Step 1: Add a plugin test proving no config edit is needed**

In `tests/test_module1_plugin.py`, add this helper:

```python
def _module1_journal_sequence_config() -> dict:
    cfg = _module1_config()
    cfg["diag_pattern"] = ""
    cfg["journal"] = {
        "line_pattern": "",
        "line_pattern2": r"^\S+\s+\S+\s+(\S+?)(?:-(\d+))?:\s+No\[(\d+)\](.+)$",
        "identifying_keyword": "example",
    }
    return cfg
```

Add this test:

```python
def test_module1_plugin_auto_parses_journal_entries_without_no_from_sequence_config(tmp_path):
    journal_file = tmp_path / "journal.log"
    journal_file.write_text(
        "2026-01-03T00:01:00 host SERVICE-12345: EXAMPLE old version without sequence\n",
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
        _module1_journal_sequence_config(),
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
    assert proc.logs[0].context == "EXAMPLE old version without sequence"
```

- [ ] **Step 2: Run the plugin test and verify it fails**

Run:

```bash
python -m pytest tests/test_module1_plugin.py::test_module1_plugin_auto_parses_journal_entries_without_no_from_sequence_config -q
```

Expected: FAIL because `MechJournalScanner` still only tries configured regexes.

- [ ] **Step 3: Wire `MechJournalScanner` to `JournalPatternMatcher`**

In `backend/parsing/mech_journal_scanner.py`, add this import:

```python
from backend.parsing.mech_journal_pattern import JournalPatternMatcher
```

In `MechJournalScanner.__init__`, add:

```python
        self._matcher = JournalPatternMatcher(journal_re, journal_re2, seq_re)
```

Replace this block in `scan()`:

```python
                m = self._journal_re.match(line) if self._journal_re else None
                if not m and self._journal_re2:
                    m = self._journal_re2.match(line)
                if not m:
                    continue

                raw_name, raw_pid, seq, context = self._extract_positional_fields(m)
```

with:

```python
                match = self._matcher.match(line)
                if not match:
                    continue

                raw_name = match.raw_name
                raw_pid = match.raw_pid
                seq = match.sequence
                context = match.context
```

Delete the `_extract_positional_fields()` method from `MechJournalScanner`, because the shared matcher now owns that responsibility.

- [ ] **Step 4: Run focused tests**

Run:

```bash
python -m pytest tests/test_module1_plugin.py tests/test_mech_journal_pattern.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit scanner integration**

```bash
git add backend/parsing/mech_journal_scanner.py tests/test_module1_plugin.py
git commit -m "fix: auto-match module1 journal logs without sequence"
```

---

### Task 4: Use Matcher In CLI `test-pattern`

**Files:**
- Modify: `cli.py`
- Modify: `tests/test_cli.py`
- Test: `python -m pytest tests/test_cli.py -q`

- [ ] **Step 1: Update CLI tests to prove config stays unchanged**

In `tests/test_cli.py`, replace the setup in `test_test_pattern_journal_without_sequence()` so it does not mutate the sample config journal patterns.

Use this full test body:

```python
def test_test_pattern_journal_without_sequence(sample_config, tmp_path):
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
    assert "journal.line_pattern2.auto_no_sequence" in result.output
    assert "SERVICE" in result.output
    assert "12345" in result.output
    assert "序号: 无" in result.output
    assert "EXAMPLE without sequence" in result.output
```

Add this malformed sequence test:

```python
def test_test_pattern_journal_with_malformed_sequence_does_not_fallback(sample_config, tmp_path):
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
    line = "2026-01-03T00:01:00 host SERVICE-12345: No[bad] EXAMPLE corrupt"

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

    assert result.exit_code == 1
    assert "不匹配 journal.line_pattern 及 line_pattern2" in result.output
```

- [ ] **Step 2: Run CLI tests and verify failure**

Run:

```bash
python -m pytest tests/test_cli.py -q
```

Expected: FAIL because `cli.py` does not use auto fallback yet.

- [ ] **Step 3: Update CLI journal matching logic**

In `cli.py`, add this import near the other imports:

```python
from backend.parsing.mech_journal_pattern import JournalPatternMatcher
```

Replace the journal branch matching block:

```python
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
        if m.re.groups >= 4:
            click.echo(f"  序号: {m.group(3)}")
            click.echo(f"  Context: {m.group(4)}")
        else:
            click.echo("  序号: 无")
            click.echo(f"  Context: {m.group(3)}")
```

with:

```python
        journal_re = re.compile(jnl["line_pattern"]) if jnl.get("line_pattern") else None
        journal_re2 = re.compile(jnl["line_pattern2"]) if jnl.get("line_pattern2") else None
        seq_pat = mod_cfg.get("sequence_pattern", r"No\[(\d+)\]")
        matcher = JournalPatternMatcher(journal_re, journal_re2, re.compile(seq_pat))
        match = matcher.match(line)
        if not match:
            click.echo("✗ 不匹配 journal.line_pattern 及 line_pattern2")
            sys.exit(1)
        click.echo(f"✓ 匹配 {match.pattern_name}")
        click.echo(f"  进程名: {match.raw_name}")
        if match.raw_pid:
            click.echo(f"  pid: {match.raw_pid}")
        if match.sequence:
            click.echo(f"  序号: {match.sequence}")
        else:
            click.echo("  序号: 无")
        click.echo(f"  Context: {match.context}")
```

- [ ] **Step 4: Run CLI tests**

Run:

```bash
python -m pytest tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit CLI integration**

```bash
git add cli.py tests/test_cli.py
git commit -m "fix: use module1 journal fallback in test-pattern"
```

---

### Task 5: Update Documentation And Config Comments

**Files:**
- Modify: `config.yaml`
- Modify: `README.md`
- Test: `python cli.py check-config -c config.yaml`

- [ ] **Step 1: Update `config.yaml` comments**

In `config.yaml`, replace the comment block above `journal.line_pattern` and `journal.line_pattern2` with:

```yaml
                # [按需修改] journal pattern supports two capture layouts:
                # 4 groups: process_name, pid, sequence, context
                # 3 groups: process_name, pid, context
                # Standard no-sequence logs are auto-supported by deriving a 3-group
                # fallback from the configured 4-group pattern. Keep the No[n]
                # pattern below unless the surrounding journal format also changes.
```

Keep the existing configured pattern unchanged:

```yaml
                line_pattern2: '^\S+\s+\S+\s+(\S+?)(?:-(\d+))?:\s+No\[(\d+)\](.+)$'
```

- [ ] **Step 2: Update README change note**

In `README.md`, update the no-sequence changelog item to:

```markdown
- 2026-05-26：支持 `module1` 无 `No[n]` 日志格式。诊断日志和 journal 日志不再强制要求序号；journal 会从现有 4 组 `No[n]` pattern 自动派生无序号 fallback，一般无需手动修改 `config.yaml`；按 slot family 的周期判断排序模式，有序号周期继续使用 `No[n]` 排序和缺号检测，无序号周期按时间排序，并对混合状态记录 warning。
```

- [ ] **Step 3: Run config validation**

Run:

```bash
python cli.py check-config -c config.yaml
```

Expected: exits 0 and prints configuration check success.

- [ ] **Step 4: Commit documentation**

```bash
git add config.yaml README.md
git commit -m "docs: document automatic module1 journal no-sequence fallback"
```

---

### Task 6: Regression Verification

**Files:**
- No source files modified in this task.
- Test: `python -m pytest tests/ -q`

- [ ] **Step 1: Run focused tests**

Run:

```bash
python -m pytest tests/test_mech_journal_pattern.py tests/test_module1_plugin.py tests/test_cli.py tests/test_config_validation.py tests/test_cycle_detector.py -q
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

Expected: PASS.

- [ ] **Step 4: Run diff whitespace check**

Run:

```bash
git diff --check HEAD
```

Expected: no trailing whitespace or conflict marker errors.

- [ ] **Step 5: Inspect final diff**

Run:

```bash
git diff --stat
git diff -- backend/parsing/mech_journal_pattern.py backend/parsing/mech_journal_scanner.py cli.py tests/test_mech_journal_pattern.py tests/test_module1_plugin.py tests/test_cli.py config.yaml README.md
```

Expected changes:

- New shared matcher module exists.
- Scanner and CLI both use `JournalPatternMatcher`.
- Existing `config.yaml` journal patterns remain sequence-first.
- Tests prove no manual config edit is required for no-sequence journal lines.
- Tests prove malformed `No[` lines do not fall back.

- [ ] **Step 6: Commit final changes if task commits were skipped**

If the earlier task-level commits were not created, run:

```bash
git add backend/parsing/mech_journal_pattern.py backend/parsing/mech_journal_scanner.py cli.py tests/test_mech_journal_pattern.py tests/test_module1_plugin.py tests/test_cli.py config.yaml README.md
git commit -m "fix: auto fallback module1 journal logs without sequence"
```

---

## Self-Review

Spec coverage:

- No manual `config.yaml` edit for standard no-sequence journal logs: Task 3 and Task 4.
- Sequence logs still match configured patterns first: Task 1.
- No-sequence logs use auto-derived fallback: Task 1, Task 3, Task 4.
- Malformed lines containing `No[` do not silently fall back: Task 1 and Task 4.
- Scanner and CLI share matching behavior: Task 2, Task 3, Task 4.
- Documentation tells users to keep the existing sequence pattern: Task 5.

Placeholder scan:

- The plan contains exact file paths, commands, code snippets, and expected outputs.
- No unspecified implementation steps are left.

Type consistency:

- `JournalPatternMatcher.match()` returns `JournalLineMatch | None`.
- `JournalLineMatch.sequence` remains `int`, with `0` meaning no sequence.
- `MechJournalScanner` consumes `raw_name`, `raw_pid`, `sequence`, and `context` from the matcher.
- CLI consumes the same matcher result and prints the pattern name for debuggability.
