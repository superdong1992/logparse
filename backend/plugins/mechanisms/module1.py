"""Module 1 mechanism plugin."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from backend.config_validation import validate_mechanism_module_config
from backend.models import MechLogEntry, MechResult, MechSlotOutput, ParseResult
from backend.parsing.cycle_detector import CycleDetector
from backend.parsing.lifecycle_splitter import LifecycleSplitConfig, LifecycleSplitter
from backend.parsing.mech_diag_scanner import MechDiagScanner
from backend.parsing.mech_journal_scanner import MechJournalScanner
from backend.parsing.process_name_resolver import ProcessNameResolver
from backend.parsing.role_identifier import RoleIdentifier
from backend.plugins.mechanisms.base import MechanismModulePlugin


class Module1Plugin(MechanismModulePlugin):
    """Module 1 mechanism plugin."""

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
        lifecycle_split_cfg = cfg.get("lifecycle_split")
        use_lifecycle_split_v2 = (
            isinstance(lifecycle_split_cfg, dict)
            and lifecycle_split_cfg.get("enabled", False) is True
        )
        split_config: LifecycleSplitConfig | None = None
        if use_lifecycle_split_v2:
            try:
                split_config = LifecycleSplitConfig.from_mapping(lifecycle_split_cfg)
            except ValueError as exc:
                result.errors.append(f"{self.module_key}: {exc}")
                return None

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

        mech_result = MechResult(module_name=module_name, module_key=self.module_key)
        for slot_id, entries in sorted(by_slot.items()):
            slot_output = MechSlotOutput(slot_id=slot_id)
            if use_lifecycle_split_v2 and split_config is not None:
                splitter = LifecycleSplitter(
                    split_config,
                    module_key=self.module_key,
                    module_name=module_name,
                )
                split_result = splitter.split(entries)
                slot_output.board_cycles = splitter.build_board_cycles(split_result)
                slot_output.lifecycle_reliable = split_result.lifecycle_reliable
                slot_output.lifecycle_split_result = split_result
            else:
                detector = CycleDetector(
                    indicator=indicator,
                    whitelist=whitelist,
                    module_key=self.module_key,
                    module_name=module_name,
                )
                slot_output.board_cycles = detector.detect(entries)
                slot_output.lifecycle_reliable = detector.lifecycle_reliable
                slot_output.boundary_issues = detector.boundary_issues
                result.errors.extend(detector.errors)
            mech_result.slots.append(slot_output)

        active_slots = {entry.slot for entry in all_entries if entry.is_active_signal}
        mech_result.active_master_slots = sorted(active_slots)
        mech_result.diag_entry_count = sum(1 for entry in all_entries if entry.source == "diagnostic")
        mech_result.journal_entry_count = sum(1 for entry in all_entries if entry.source == "journal")

        return mech_result

    def apply_roles(self, result: ParseResult, mech_result: MechResult) -> None:
        RoleIdentifier.apply_mech_roles(mech_result, result)
