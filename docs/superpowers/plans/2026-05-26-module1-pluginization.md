# Module1 Mechanism Pluginization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `module1` from a `config.yaml` regex block into a real mechanism module plugin that owns its special log parsing, cycle detection, and active-role identification.

**Architecture:** `ParserPlugin` remains the product-level orchestrator: it extracts timestamps, builds `ActivePeriod`, loads mechanism plugins, and applies fallback roles. `Module1Plugin` owns the current module1-specific `MechDiagScanner`, `MechJournalScanner`, `CycleDetector`, and active-role application. Other mechanism modules can provide their own plugins without inheriting module1 lifecycle behavior.

**Tech Stack:** Python 3.12, Pydantic models in `backend/models.py`, Click CLI, pytest, YAML config, existing dynamic loader in `backend/plugins/loader.py`.

---

## File Structure

- Create `backend/plugins/mechanisms/__init__.py`
  - Package marker for mechanism module plugins.
- Create `backend/plugins/mechanisms/base.py`
  - Defines `MechanismModulePlugin`, the new base class.
- Create `backend/plugins/mechanisms/module1.py`
  - Implements `Module1Plugin` by moving current module1 parse/cycle/role logic out of `ParserPlugin`.
- Modify `backend/plugins/default/parser.py`
  - Remove direct module1 parsing responsibility; load and call `MechanismModulePlugin` instances.
- Modify `backend/config_validation.py`
  - Validate mechanism module plugin path and nested plugin config.
  - Delegate module-specific config validation to `Module1Plugin.validate_config`.
- Modify `backend/plugins/base.py`
  - Update comments to remove outdated implication that `LogParserPlugin` owns all mechanism details.
- Modify `config.yaml`
  - Change `mechanism_modules.module1` to `{plugin, enabled, config}`.
- Modify `cli.py`
  - Make `test-pattern` read nested mechanism config while preserving command UX.
- Modify `tests/conftest.py`
  - Update `sample_config` to the new mechanism plugin shape.
- Modify `tests/test_config_validation.py`
  - Cover plugin path validation and nested module config validation.
- Modify `tests/test_parser_plugin.py`
  - Cover parser orchestration and ensure module1 internals are no longer parser-owned.
- Create `tests/test_module1_plugin.py`
  - Cover `Module1Plugin.parse()` and `Module1Plugin.apply_roles()`.
- Modify `README.md`
  - Document mechanism module plugins and add a change-record entry.

---

### Task 1: Add Mechanism Module Plugin Interface

**Files:**
- Create: `backend/plugins/mechanisms/__init__.py`
- Create: `backend/plugins/mechanisms/base.py`
- Test: `tests/test_plugin_loader.py`

- [ ] **Step 1: Write failing loader/base tests**

Add these tests to `tests/test_plugin_loader.py`:

```python
def test_instantiate_mechanism_module_plugin():
    from backend.plugins.loader import instantiate_plugin
    from backend.plugins.mechanisms.base import MechanismModulePlugin

    plugin = instantiate_plugin(
        "backend.plugins.mechanisms.module1.Module1Plugin",
        MechanismModulePlugin,
        {"module_name": "EXAMPLE"},
        module_key="module1",
        ts_extractor=None,
    )

    assert plugin.module_key == "module1"
    assert plugin.module_name == "EXAMPLE"


def test_mechanism_plugin_base_rejects_wrong_class():
    import pytest

    from backend.plugins.loader import instantiate_plugin
    from backend.plugins.mechanisms.base import MechanismModulePlugin

    with pytest.raises(TypeError):
        instantiate_plugin(
            "backend.plugins.default.parser.ParserPlugin",
            MechanismModulePlugin,
            {},
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_plugin_loader.py::test_instantiate_mechanism_module_plugin tests/test_plugin_loader.py::test_mechanism_plugin_base_rejects_wrong_class -q
```

Expected: fail because `backend.plugins.mechanisms` and `Module1Plugin` do not exist yet.

- [ ] **Step 3: Create the base interface**

Create `backend/plugins/mechanisms/__init__.py`:

```python
"""Mechanism module plugins."""
```

Create `backend/plugins/mechanisms/base.py`:

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from backend.models import MechResult, ParseResult
from backend.parsing.output_writer import MechOutputWriter


class MechanismModulePlugin(ABC):
    """Base class for mechanism-specific log parsers.

    Product parsers load these plugins and orchestrate them. Each mechanism
    plugin owns any special parsing, lifecycle splitting, and role signals for
    its module.
    """

    def __init__(
        self,
        config: dict[str, Any],
        module_key: str = "",
        ts_extractor: Any = None,
    ):
        self.config = config
        self.module_key = module_key
        self.ts_extractor = ts_extractor

    @property
    def module_name(self) -> str:
        return str(self.config.get("module_name", ""))

    @classmethod
    def validate_config(cls, module_key: str, config: dict[str, Any]) -> list[str]:
        return []

    @abstractmethod
    def parse(self, result: ParseResult) -> MechResult | None:
        ...

    def apply_roles(self, result: ParseResult, mech_result: MechResult) -> None:
        return None

    def write_output(self, mech_result: MechResult, output_dir: Path) -> Path:
        return MechOutputWriter().write(mech_result, output_dir)
```

- [ ] **Step 4: Add a temporary minimal `Module1Plugin` shell**

Create `backend/plugins/mechanisms/module1.py`:

```python
from __future__ import annotations

from backend.models import MechResult, ParseResult
from backend.plugins.mechanisms.base import MechanismModulePlugin


class Module1Plugin(MechanismModulePlugin):
    def parse(self, result: ParseResult) -> MechResult | None:
        return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
python -m pytest tests/test_plugin_loader.py::test_instantiate_mechanism_module_plugin tests/test_plugin_loader.py::test_mechanism_plugin_base_rejects_wrong_class -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add backend/plugins/mechanisms tests/test_plugin_loader.py
git commit -m "Add mechanism module plugin base"
```

---

### Task 2: Convert Mechanism Module Config Shape and Validation

**Files:**
- Modify: `backend/config_validation.py`
- Modify: `tests/test_config_validation.py`
- Modify: `config.yaml`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write failing config validation tests**

Update `_valid_product_config()` in `tests/test_config_validation.py` so `mechanism_modules.module1` uses the new shape:

```python
"mechanism_modules": {
    "module1": {
        "plugin": "backend.plugins.mechanisms.module1.Module1Plugin",
        "enabled": True,
        "config": {
            "module_name": "EXAMPLE",
            "journal": {
                "line_pattern": r"(\S+)\s+No\[(\d+)\]\s+(\S+)\s+(.*)",
            },
        },
    },
},
```

Add these tests:

```python
def test_mechanism_module_requires_plugin(self):
    cfg = _valid_product_config()
    del cfg["log_parser"]["config"]["mechanism_modules"]["module1"]["plugin"]

    errors = validate_config({"products": {"default": cfg}})

    assert any("mechanism_modules.module1.plugin" in e for e in errors)


def test_mechanism_module_plugin_must_be_loadable(self):
    cfg = _valid_product_config()
    cfg["log_parser"]["config"]["mechanism_modules"]["module1"]["plugin"] = "bad.module.Plugin"

    errors = validate_config({"products": {"default": cfg}})

    assert any("bad.module.Plugin" in e for e in errors)


def test_mechanism_module_nested_config_is_validated(self):
    cfg = _valid_product_config()
    mod = cfg["log_parser"]["config"]["mechanism_modules"]["module1"]
    mod["config"]["diag_pattern"] = r"Slot=(?P<Slot>\d+)"

    errors = validate_config({"products": {"default": cfg}})

    assert any("CPU_Id" in e and "ProcessName" in e for e in errors)
```

- [ ] **Step 2: Run validation tests to verify they fail**

Run:

```bash
python -m pytest tests/test_config_validation.py -q
```

Expected: fail because `validate_config()` still treats module config as the old flat shape.

- [ ] **Step 3: Implement mechanism plugin validation**

In `backend/config_validation.py`, import the mechanism base:

```python
from backend.plugins.mechanisms.base import MechanismModulePlugin
```

Add a helper:

```python
def _validate_mechanism_plugin_config(module_key: str, module_cfg: Any) -> list[str]:
    path = f"mechanism_modules.{module_key}"
    if not isinstance(module_cfg, dict):
        return [f"{path} 必须是对象"]

    if module_cfg.get("enabled", True) is False:
        return []

    plugin_path = module_cfg.get("plugin")
    if not isinstance(plugin_path, str) or not plugin_path.strip():
        return [f"{path}.plugin 必须是非空字符串"]

    cfg = module_cfg.get("config", {})
    if not isinstance(cfg, dict):
        return [f"{path}.config 必须是对象"]

    errors = _validate_plugin_loadable_for_base(
        path=path,
        plugin_path=plugin_path,
        expected_base=MechanismModulePlugin,
        expected_methods=["parse"],
    )
    if errors:
        return errors

    try:
        import importlib
        module_path, class_name = plugin_path.rsplit(".", 1)
        cls = getattr(importlib.import_module(module_path), class_name)
        validator = getattr(cls, "validate_config", None)
        if callable(validator):
            errors.extend(validator(module_key, cfg))
    except Exception as e:
        errors.append(f"{path}.plugin={plugin_path!r} 配置校验失败: {type(e).__name__}: {e}")

    return errors
```

Refactor `_validate_plugin_loadable()` into a reusable helper named `_validate_plugin_loadable_for_base(...)`. Keep `_validate_plugin_loadable(product_name, kind, plugin_path)` as a wrapper for product discovery/parser plugins.

In `_validate_log_parser_config()`, replace:

```python
validate_mechanism_module_config(module_key, module_cfg)
```

with:

```python
_validate_mechanism_plugin_config(module_key, module_cfg)
```

In `backend/plugins/mechanisms/module1.py`, add:

```python
from typing import Any

from backend.config_validation import validate_mechanism_module_config

    @classmethod
    def validate_config(cls, module_key: str, config: dict[str, Any]) -> list[str]:
        return validate_mechanism_module_config(module_key, config)
```

- [ ] **Step 4: Update `config.yaml` module1 shape**

Change:

```yaml
mechanism_modules:
  module1:
    module_name: "EXAMPLE"
    enabled: true
```

to:

```yaml
mechanism_modules:
  module1:
    plugin: "backend.plugins.mechanisms.module1.Module1Plugin"
    enabled: true
    config:
      module_name: "EXAMPLE"
```

Indent the existing module1-specific fields under `config:`:

```yaml
      diag_pattern: ...
      active_master_keyword: ""
      board_restart_indicator: ""
      board_restart_whitelist: []
      process_name_mapping: {}
      journal:
        line_pattern: ...
        line_pattern2: ...
        identifying_keyword: "EXAMPLE"
      sequence_pattern: ...
```

Apply the same shape to the `compact` product module:

```yaml
mechanism_modules:
  ctrl:
    plugin: "backend.plugins.mechanisms.module1.Module1Plugin"
    enabled: true
    config:
      module_name: "COMPACT"
      ...
```

- [ ] **Step 5: Update `tests/conftest.py` sample config**

Wrap each mechanism module with `plugin`, `enabled`, and nested `config`, matching `config.yaml`.

- [ ] **Step 6: Run validation tests**

Run:

```bash
python -m pytest tests/test_config_validation.py -q
python cli.py check-config -c config.yaml
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/config_validation.py backend/plugins/mechanisms/module1.py config.yaml tests/conftest.py tests/test_config_validation.py
git commit -m "Validate mechanism module plugins"
```

---

### Task 3: Move Module1 Parsing Into `Module1Plugin`

**Files:**
- Modify: `backend/plugins/mechanisms/module1.py`
- Create: `tests/test_module1_plugin.py`

- [ ] **Step 1: Write failing module1 plugin parse test**

Create `tests/test_module1_plugin.py`:

```python
from __future__ import annotations

from datetime import datetime

from backend.models import LogEntry, ParseResult, SlotInfo
from backend.parsing.timestamp_extractor import TimestampExtractor
from backend.plugins.mechanisms.module1 import Module1Plugin


def _module1_config():
    return {
        "module_name": "EXAMPLE",
        "diag_pattern": r"Service=(?P<Service>[^;]+).*?Slot=(?P<Slot>[^;,)]+).*?CPU-Id=(?P<CPU_Id>[^;,)]+).*?ProcessName=(?P<ProcessName>[^;,)]+).*?Context=(?P<Context>.+?)\)$",
        "active_master_keyword": "ACTIVE",
        "board_restart_indicator": "",
        "board_restart_whitelist": [],
        "process_name_mapping": {},
        "journal": {
            "line_pattern": "",
            "line_pattern2": "",
            "identifying_keyword": "EXAMPLE",
        },
        "sequence_pattern": r"No\[(\d+)\]",
    }


def test_module1_plugin_parses_diag_entries(tmp_path):
    log_file = tmp_path / "diag.log"
    log_file.write_text(
        "2026-01-03T00:01:00 EXAMPLE Service=SERVICE; Slot=1; CPU-Id=0; "
        "ProcessName=SERVICE-12345; Context=No[1] ACTIVE)\n",
        encoding="utf-8",
    )
    slot = SlotInfo(slot_id="1", name="slot_1", path=str(tmp_path))
    slot.add_diagnostic_log(LogEntry(path=str(log_file), name="diag.log"))
    result = ParseResult(diagnostic_slots=[slot])
    ts = TimestampExtractor(
        __import__("re").compile(r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?")
    )
    plugin = Module1Plugin(_module1_config(), module_key="module1", ts_extractor=ts)

    mech = plugin.parse(result)

    assert mech is not None
    assert mech.module_name == "EXAMPLE"
    assert mech.diag_entry_count == 1
    assert mech.active_master_slots == ["1"]
    assert mech.slots[0].board_cycles[0].processes[0].process_name == "SERVICE"
```

- [ ] **Step 2: Write failing module1 role test**

Add to `tests/test_module1_plugin.py`:

```python
from backend.models import BoardRole, MechResult


def test_module1_plugin_applies_roles(sample_parse_result):
    plugin = Module1Plugin(_module1_config(), module_key="module1", ts_extractor=None)
    mech = MechResult(module_name="EXAMPLE", active_master_slots=["1"])

    plugin.apply_roles(sample_parse_result, mech)

    assert sample_parse_result.diagnostic_slots[0].role == BoardRole.ACTIVE
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_module1_plugin.py -q
```

Expected: fail because `Module1Plugin.parse()` still returns `None`.

- [ ] **Step 4: Implement `Module1Plugin` by moving parser logic**

Replace `backend/plugins/mechanisms/module1.py` with the current logic from `ParserPlugin._parse_one_mech`, adjusted to use instance fields:

```python
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from backend.config_validation import validate_mechanism_module_config
from backend.models import MechLogEntry, MechResult, MechSlotOutput, ParseResult
from backend.parsing.cycle_detector import CycleDetector
from backend.parsing.mech_diag_scanner import MechDiagScanner
from backend.parsing.mech_journal_scanner import MechJournalScanner
from backend.parsing.process_name_resolver import ProcessNameResolver
from backend.parsing.role_identifier import RoleIdentifier
from backend.plugins.mechanisms.base import MechanismModulePlugin


class Module1Plugin(MechanismModulePlugin):
    @classmethod
    def validate_config(cls, module_key: str, config: dict[str, Any]) -> list[str]:
        return validate_mechanism_module_config(module_key, config)

    def parse(self, result: ParseResult) -> MechResult | None:
        cfg = self.config
        errors = self.validate_config(self.module_key, cfg)
        if errors:
            result.errors.extend(errors)
            return None

        module_name: str = cfg["module_name"]
        mod_upper = module_name.upper()

        diag_re = re.compile(cfg["diag_pattern"]) if cfg.get("diag_pattern") else None
        jnl_cfg: dict = cfg.get("journal", {})
        journal_re = re.compile(jnl_cfg["line_pattern"]) if jnl_cfg.get("line_pattern") else None
        journal_re2 = re.compile(jnl_cfg["line_pattern2"]) if jnl_cfg.get("line_pattern2") else None
        journal_keyword = (
            jnl_cfg.get("identifying_keyword", "").lower()
            if jnl_cfg.get("identifying_keyword")
            else None
        )
        seq_re = re.compile(cfg.get("sequence_pattern", r"No\[(\d+)\]"))
        master_keyword = (
            re.compile(cfg["active_master_keyword"])
            if cfg.get("active_master_keyword")
            else None
        )
        indicator = (
            cfg.get("board_restart_indicator", "").lower()
            if cfg.get("board_restart_indicator")
            else None
        )
        whitelist = cfg.get("board_restart_whitelist", [])
        name_map: dict[str, str] = cfg.get("process_name_mapping", {})

        whitelist_set = {w.lower() for w in whitelist}
        map_keys = {k.lower() for k in name_map}
        conflict = whitelist_set & map_keys
        if conflict:
            result.errors.append(
                f"{self.module_key}: board_restart_whitelist conflicts with process_name_mapping: {sorted(conflict)}"
            )
            return None

        all_entries: list[MechLogEntry] = []
        resolver = ProcessNameResolver(name_map)

        if diag_re:
            diag_scanner = MechDiagScanner(
                diag_re,
                seq_re,
                master_keyword,
                resolver,
                mod_upper,
                self.ts_extractor,
            )
            for slot in result.diagnostic_slots:
                for log_entry in slot.diagnostic_logs:
                    all_entries.extend(diag_scanner.scan(log_entry, slot.slot_id))

        diag_tz = next(
            (e.timestamp.tzinfo for e in all_entries if e.timestamp and e.timestamp.tzinfo),
            None,
        )

        if (journal_re or journal_re2) and journal_keyword:
            journal_scanner = MechJournalScanner(
                journal_re,
                journal_re2,
                journal_keyword,
                seq_re,
                master_keyword,
                resolver,
                indicator,
                mod_upper,
                self.ts_extractor,
            )
            for private_slot in result.private_slots:
                all_entries.extend(journal_scanner.scan(private_slot, diag_tz))

        if not all_entries:
            return None

        tzinfo = next(
            (e.timestamp.tzinfo for e in all_entries if e.timestamp and e.timestamp.tzinfo),
            None,
        )
        if tzinfo:
            for entry in all_entries:
                if entry.timestamp and entry.timestamp.tzinfo is None:
                    entry.timestamp = entry.timestamp.replace(tzinfo=tzinfo)

        by_slot: dict[str, list[MechLogEntry]] = defaultdict(list)
        for entry in all_entries:
            by_slot[entry.slot].append(entry)

        mech_result = MechResult(module_name=module_name)
        for slot_id, entries in sorted(by_slot.items()):
            slot_output = MechSlotOutput(slot_id=slot_id)
            detector = CycleDetector(indicator=indicator, whitelist=whitelist)
            slot_output.board_cycles = detector.detect(entries)
            mech_result.slots.append(slot_output)

        active_slots = {entry.slot for entry in all_entries if entry.is_active_signal}
        mech_result.active_master_slots = sorted(active_slots)
        mech_result.diag_entry_count = sum(1 for entry in all_entries if entry.source == "diagnostic")
        mech_result.journal_entry_count = sum(1 for entry in all_entries if entry.source == "journal")

        return mech_result

    def apply_roles(self, result: ParseResult, mech_result: MechResult) -> None:
        RoleIdentifier.apply_mech_roles(mech_result, result)
```

- [ ] **Step 5: Run module1 plugin tests**

Run:

```bash
python -m pytest tests/test_module1_plugin.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add backend/plugins/mechanisms/module1.py tests/test_module1_plugin.py
git commit -m "Move module1 parsing into mechanism plugin"
```

---

### Task 4: Make `ParserPlugin` Orchestrate Mechanism Plugins

**Files:**
- Modify: `backend/plugins/default/parser.py`
- Modify: `tests/test_parser_plugin.py`

- [ ] **Step 1: Write failing parser orchestration test**

Add to `tests/test_parser_plugin.py`:

```python
class TestMechanismPluginOrchestration:
    def test_parser_loads_mechanism_plugin(self, sample_config, sample_parse_result):
        sample_config["mechanism_modules"] = {
            "module1": {
                "plugin": "backend.plugins.mechanisms.module1.Module1Plugin",
                "enabled": True,
                "config": {
                    "module_name": "EXAMPLE",
                    "diag_pattern": "",
                    "journal": {"line_pattern": "", "line_pattern2": "", "identifying_keyword": ""},
                    "sequence_pattern": r"No\[(\d+)\]",
                },
            },
        }
        plugin = ParserPlugin(sample_config)

        result = plugin.parse(sample_parse_result)

        assert result is sample_parse_result

    def test_parser_no_longer_exposes_module_specific_parse_method(self, plugin):
        assert not hasattr(plugin, "_parse_one_mech")
```

- [ ] **Step 2: Run parser tests to verify they fail**

Run:

```bash
python -m pytest tests/test_parser_plugin.py::TestMechanismPluginOrchestration -q
```

Expected: second test fails because `_parse_one_mech` still exists.

- [ ] **Step 3: Update `ParserPlugin.parse()`**

In `backend/plugins/default/parser.py`, add imports:

```python
from backend.plugins.mechanisms.base import MechanismModulePlugin
from backend.plugins.loader import instantiate_plugin
```

Replace the mechanism parsing loop with:

```python
mechanism_plugins = []
for module_key, module_entry in self._mech_modules.items():
    if not module_entry.get("enabled", True):
        continue

    plugin = instantiate_plugin(
        module_entry["plugin"],
        MechanismModulePlugin,
        module_entry.get("config", {}),
        module_key=module_key,
        ts_extractor=self._ts_extractor,
    )
    mechanism_plugins.append(plugin)

for mechanism in mechanism_plugins:
    mech = mechanism.parse(result)
    if mech:
        result.mech_results.append(mech)
        mechanism.apply_roles(result, mech)

RoleIdentifier.fallback_roles(result)
```

Delete `_parse_one_mech()` from `ParserPlugin`.

Keep `ParserPlugin.write_output()`:

```python
def write_output(self, mech_result: MechResult, output_dir: Path) -> Path:
    return MechOutputWriter().write(mech_result, output_dir)
```

- [ ] **Step 4: Remove unused imports from parser**

Remove imports that become module1-owned:

```python
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from backend.config_validation import validate_mechanism_module_config
from backend.models import MechLogEntry, MechSlotOutput, LogEntry, PrivateSlotInfo
from backend.parsing.cycle_detector import CycleDetector
from backend.parsing.mech_diag_scanner import MechDiagScanner
from backend.parsing.mech_journal_scanner import MechJournalScanner
from backend.parsing.process_name_resolver import ProcessNameResolver
```

Keep only imports used by timestamp extraction, active period building, output writing, role fallback, models, and plugin loading.

- [ ] **Step 5: Run parser tests**

Run:

```bash
python -m pytest tests/test_parser_plugin.py tests/test_module1_plugin.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add backend/plugins/default/parser.py tests/test_parser_plugin.py
git commit -m "Orchestrate mechanism plugins from parser"
```

---

### Task 5: Preserve `test-pattern` CLI Behavior With Nested Config

**Files:**
- Modify: `cli.py`
- Test: manual CLI commands

- [ ] **Step 1: Write a lightweight helper in `cli.py`**

Add near the top of `cli.py`:

```python
def _mechanism_config(module_entry: dict) -> dict:
    return module_entry.get("config", module_entry)
```

- [ ] **Step 2: Update `test_pattern()` module lookup**

Replace:

```python
mod_cfg = modules[module]
```

with:

```python
mod_cfg = _mechanism_config(modules[module])
```

- [ ] **Step 3: Run manual CLI pattern tests**

Run:

```bash
python cli.py test-pattern -m module1 -t diag "2026-01-03T00:01:00 EXAMPLE Service=SERVICE; Slot=1; CPU-Id=0; ProcessName=SERVICE-12345; Context=No[1] hello)"
```

Expected: command prints that `diag_pattern` matched and shows `Slot`, `CPU_Id`, `ProcessName`, and `Context`.

Run:

```bash
python cli.py check-config -c config.yaml
```

Expected: config check passes.

- [ ] **Step 4: Commit**

```bash
git add cli.py
git commit -m "Read nested mechanism config in CLI"
```

---

### Task 6: Update Documentation and Remove Old Architecture Wording

**Files:**
- Modify: `README.md`
- Modify: `backend/plugins/base.py`

- [ ] **Step 1: Update `backend/plugins/base.py` comments**

In `DirectoryDiscoveryPlugin` docstring, remove the outdated lines saying scanner may use `self.decompressor` to extract nested archives and that `LogEntry.path` is supplied for pipeline inner extraction.

Replace with:

```python
      - 负责：找到 slot 目录、匹配诊断日志文件、提取私有 journal 日志文件
      - 不负责：解压归档包；归档解压由 Decompressor 的统一解压阶段完成
```

In `LogParserPlugin` docstring, replace the mechanism ownership wording with:

```python
      - 负责：读取日志内容、提取时间戳、构建 ActivePeriod、编排机制模块插件
      - 机制模块自身负责特殊日志解析、周期切分和角色信号
```

- [ ] **Step 2: Update README workflow**

In `README.md`, change the workflow block to:

```text
外层压缩包
  → Decompressor 统一解压归档包（外层 + 内层 zip/tar/tgz/tar.gz）
  → DirectoryDiscoveryPlugin 扫描已解压工作区
  → LogParserPlugin 提取基础时间戳和 ActivePeriod，编排机制模块插件
  → MechanismModulePlugin 解析特殊机制模块日志
  → MechOutputWriter 写出机制模块日志
  → MetadataGenerator 生成 metadata.json，CLI 写出 result.json
```

Add a paragraph:

```markdown
机制模块通过 `MechanismModulePlugin` 扩展。`module1` 是机制模块插件，拥有自己的日志扫描、周期切分和主控角色信号；其他模块如果没有周期切分或主控判定需求，可以只实现自己的解析逻辑。
```

Add a change-record line:

```markdown
- 2026-05-26：`module1` 机制模块插件化。`ParserPlugin` 只负责编排机制模块插件，module1 自己拥有特殊日志解析、周期切分和主控判定逻辑。
```

- [ ] **Step 3: Commit**

```bash
git add README.md backend/plugins/base.py
git commit -m "Document mechanism module plugin architecture"
```

---

### Task 7: Full Verification and End-to-End Parse

**Files:**
- No source files expected.

- [ ] **Step 1: Run full test suite**

Run:

```bash
python -m pytest tests/ -q
```

Expected:

```text
... passed
```

The exact count will be greater than the current 171 because this plan adds tests.

- [ ] **Step 2: Run config validation**

Run:

```bash
python cli.py check-config -c config.yaml
```

Expected:

```text
✓ 配置加载成功
✓ 配置检查通过
```

- [ ] **Step 3: Run default product mock parse**

Run:

```bash
python cli.py parse tests/mock_data/diagnostic_information_20260103.zip -o ./output-verify-module1 --product default
```

Expected:

- Exit code 0.
- Output lists `机制模块 [EXAMPLE] 日志`.
- `output-verify-module1/diagnostic_information_20260103/result.json` exists.

- [ ] **Step 4: Run compact product mock parse**

Run:

```bash
python cli.py parse tests/mock_data_compact/compact_package_20260103.zip -o ./output-verify-module1-compact --product compact
```

Expected:

- Exit code 0.
- Output lists `机制模块 [COMPACT] 日志`.
- `output-verify-module1-compact/compact_package_20260103/result.json` exists.

- [ ] **Step 5: Inspect ownership boundary**

Run:

```bash
rg -n "_parse_one_mech|CycleDetector|MechDiagScanner|MechJournalScanner|ProcessNameResolver" backend/plugins/default/parser.py backend/plugins/mechanisms/module1.py
```

Expected:

- No `_parse_one_mech` occurrence.
- `CycleDetector`, `MechDiagScanner`, `MechJournalScanner`, and `ProcessNameResolver` occur in `backend/plugins/mechanisms/module1.py`, not in `backend/plugins/default/parser.py`.

- [ ] **Step 6: Commit verification cleanup if any files changed**

If no files changed, do not commit. If docs or expected outputs were intentionally updated:

```bash
git add <changed-files>
git commit -m "Verify mechanism plugin migration"
```

---

## Resume Checklist

If context or usage runs low, resume from the first unchecked task above.

Before starting any task:

```bash
git status --short --branch
```

Expected safe states:

- Clean working tree after a task commit.
- Or only the current task's files modified.

Use this Python command in the Codex environment if plain `python` is unavailable:

```powershell
& 'C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/ -q
```

---

## Self-Review Notes

- Spec coverage: The plan covers the requested architecture boundary: `module1` becomes a plugin and owns special parsing, cycle splitting, and role application.
- Placeholder scan: No placeholder markers are present.
- Type consistency: The plan consistently uses `MechanismModulePlugin`, `Module1Plugin`, `parse(result)`, `apply_roles(result, mech_result)`, and nested `mechanism_modules.<key>.config`.
