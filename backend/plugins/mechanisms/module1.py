"""Module 1 mechanism plugin."""

from __future__ import annotations

from backend.models import MechResult, ParseResult
from backend.plugins.mechanisms.base import MechanismModulePlugin


class Module1Plugin(MechanismModulePlugin):
    """Minimal Module 1 mechanism plugin."""

    def parse(self, result: ParseResult) -> MechResult | None:
        return None
