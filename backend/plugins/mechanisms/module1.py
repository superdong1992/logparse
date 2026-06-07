"""Module 1 mechanism plugin."""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from typing import Any

from backend.config_validation import validate_mechanism_module_config
from backend.models import MechLogEntry, MechResult, MechSlotOutput, ParseResult
from backend.parsing.lifecycle_common import LifecycleSplitConfig
from backend.parsing.lifecycle_splitter_v3 import LifecycleSplitterV3
from backend.parsing.mech_diag_scanner import MechDiagScanner
from backend.parsing.mech_journal_scanner import MechJournalScanner
from backend.parsing.process_name_resolver import ProcessNameResolver
from backend.parsing.role_identifier import RoleIdentifier
from backend.plugins.mechanisms.base import MechanismModulePlugin

logger = logging.getLogger(__name__)


class Module1Plugin(MechanismModulePlugin):
    """Module 1 mechanism plugin."""

    @classmethod
    def validate_config(cls, module_key: str, config: dict[str, Any]) -> list[str]:
        return validate_mechanism_module_config(module_key, config)

    def build_diagnostic_line_scanner(self):
        cfg = self.config
        errors = self.validate_config(self.module_key, cfg)
        if errors or not cfg.get("diag_pattern"):
            return None

        module_name: str = cfg["module_name"]
        diag_re = re.compile(cfg["diag_pattern"])
        seq_re = re.compile(cfg.get("sequence_pattern", r"No\[(\d+)\]"))
        master_keyword = (
            re.compile(cfg["active_master_keyword"])
            if cfg.get("active_master_keyword")
            else None
        )
        resolver = ProcessNameResolver()
        scanner = MechDiagScanner(
            diag_re,
            seq_re,
            master_keyword,
            resolver,
            module_name.upper(),
            self.ts_extractor,
        )
        return scanner.scan_line

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
        line_pattern2_required_substrings = (
            jnl_cfg.get("line_pattern2_required_substrings") or []
        )
        seq_re = re.compile(cfg.get("sequence_pattern", r"No\[(\d+)\]"))
        master_keyword = (
            re.compile(cfg["active_master_keyword"])
            if cfg.get("active_master_keyword")
            else None
        )
        try:
            split_config = LifecycleSplitConfig.from_mapping(cfg.get("lifecycle_split", {}))
        except ValueError as exc:
            result.errors.append(f"{self.module_key}: {exc}")
            return None

        all_entries: list[MechLogEntry] = []
        resolver = ProcessNameResolver()

        diag_t0 = time.perf_counter()
        diag_file_count = 0
        diag_entry_count = 0
        precomputed_diag_entries = getattr(self, "_precomputed_diagnostic_entries", None)
        if precomputed_diag_entries is not None:
            diag_file_count = int(getattr(self, "_precomputed_diagnostic_file_count", 0))
            all_entries.extend(list(precomputed_diag_entries))
            diag_entry_count = len(precomputed_diag_entries)
        elif diag_re:
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
                    diag_file_count += 1
                    scanned = diag_scanner.scan(log_entry, slot.slot_id)
                    diag_entry_count += len(scanned)
                    all_entries.extend(scanned)
        logger.info(
            "LOGPARSE_PERF module1.diag_scan module=%s elapsed=%.3fs files=%d entries=%d",
            self.module_key,
            time.perf_counter() - diag_t0,
            diag_file_count,
            diag_entry_count,
        )

        diag_tz = next(
            (e.timestamp.tzinfo for e in all_entries if e.timestamp and e.timestamp.tzinfo),
            None,
        )

        journal_t0 = time.perf_counter()
        journal_file_count = 0
        journal_entry_count = 0
        if (journal_re or journal_re2) and journal_keyword:
            journal_scanner = MechJournalScanner(
                journal_re,
                journal_re2,
                journal_keyword,
                seq_re,
                line_pattern2_required_substrings,
                master_keyword,
                resolver,
                mod_upper,
                self.ts_extractor,
            )
            for private_slot in result.private_slots:
                journal_file_count += len(private_slot.journal_logs)
                scanned = journal_scanner.scan(private_slot, diag_tz)
                journal_entry_count += len(scanned)
                all_entries.extend(scanned)
        logger.info(
            "LOGPARSE_PERF module1.journal_scan module=%s elapsed=%.3fs files=%d entries=%d",
            self.module_key,
            time.perf_counter() - journal_t0,
            journal_file_count,
            journal_entry_count,
        )

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
            slot_t0 = time.perf_counter()
            slot_output = MechSlotOutput(slot_id=slot_id)
            splitter = LifecycleSplitterV3(
                split_config,
                module_key=self.module_key,
                module_name=module_name,
            )
            split_result = splitter.split(entries)
            slot_output.board_cycles = splitter.build_board_cycles(split_result)
            slot_output.lifecycle_reliable = split_result.lifecycle_reliable
            slot_output.lifecycle_split_result = split_result
            mech_result.slots.append(slot_output)
            logger.info(
                "LOGPARSE_PERF module1.slot_cycle module=%s slot=%s elapsed=%.3fs "
                "entries=%d cycles=%d lifecycle_split=%s",
                self.module_key,
                slot_id,
                time.perf_counter() - slot_t0,
                len(entries),
                len(slot_output.board_cycles),
                "interval_v3",
            )

        active_slots = {entry.slot for entry in all_entries if entry.is_active_signal}
        mech_result.active_master_slots = sorted(active_slots)
        mech_result.diag_entry_count = sum(1 for entry in all_entries if entry.source == "diagnostic")
        mech_result.journal_entry_count = sum(1 for entry in all_entries if entry.source == "journal")

        return mech_result

    def apply_roles(self, result: ParseResult, mech_result: MechResult) -> None:
        RoleIdentifier.apply_mech_roles(mech_result, result)
