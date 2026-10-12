"""Frozen native mechanism plugin API; policy implementations are yellow."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable

from backend.contracts.plugins import (
    MechanismContext,
    MechanismDescriptor,
    MechanismOutcome,
    PLUGIN_API_VERSION,
    PluginCapability,
)


class MechanismPlugin(ABC):
    """Native v1 plugin that receives bounded input and returns an outcome."""

    plugin_api_version = PLUGIN_API_VERSION
    plugin_capabilities: frozenset[PluginCapability] = frozenset()
    config_schema: str | None = None

    def __init__(
        self,
        config: dict[str, Any],
        module_key: str = "",
        ts_extractor: Any = None,
        dependencies: Iterable[str] | None = None,
    ) -> None:
        self.config = config
        self.module_key = module_key
        self.ts_extractor = ts_extractor
        self.dependencies = tuple(dict.fromkeys(str(item) for item in (dependencies or ())))

    @property
    def module_name(self) -> str:
        return str(self.config.get("module_name", ""))

    @property
    def descriptor(self) -> MechanismDescriptor:
        return MechanismDescriptor(
            key=self.module_key,
            name=self.module_name or self.module_key,
            dependencies=self.dependencies,
            capabilities=self.plugin_capabilities,
            api_version=self.plugin_api_version,
            config_schema=self.config_schema,
        )

    @classmethod
    def validate_config(cls, module_key, config) -> list[str]:
        return []

    @abstractmethod
    def execute(self, context: MechanismContext) -> MechanismOutcome:
        ...

    def build_diagnostic_line_scanner(self):
        return None

__all__ = ["MechanismPlugin"]
