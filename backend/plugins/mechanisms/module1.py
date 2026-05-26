"""Module 1 mechanism plugin."""

from __future__ import annotations

from typing import Any

from backend.config_validation import validate_mechanism_module_config
from backend.models import MechResult, ParseResult
from backend.plugins.mechanisms.base import MechanismModulePlugin


class Module1Plugin(MechanismModulePlugin):
    """Minimal Module 1 mechanism plugin."""

    @classmethod
    def validate_config(cls, module_key: str, config: dict[str, Any]) -> list[str]:
        return validate_mechanism_module_config(module_key, config)

    def parse(self, result: ParseResult) -> MechResult | None:
        return None
