"""Base interface for mechanism module plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from backend.models import MechResult, ParseResult
from backend.parsing.output_writer import MechOutputWriter


class MechanismModulePlugin(ABC):
    """Base class for mechanism-specific parse plugins."""

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
    def validate_config(cls, module_key, config) -> list[str]:
        return []

    @abstractmethod
    def parse(self, result: ParseResult) -> MechResult | None:
        ...

    def build_diagnostic_line_scanner(self):
        return None

    def set_precomputed_diagnostic_entries(
        self,
        entries,
        file_count: int = 0,
        line_count: int = 0,
    ) -> None:
        self._precomputed_diagnostic_entries = list(entries)
        self._precomputed_diagnostic_file_count = file_count
        self._precomputed_diagnostic_line_count = line_count

    def apply_roles(self, result, mech_result) -> None:
        pass

    def write_output(self, mech_result: MechResult, output_dir: Path) -> Path:
        return MechOutputWriter().write(mech_result, output_dir)
