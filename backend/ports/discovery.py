"""Discovery extension port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.contracts.plugins import DiscoveryContext, DiscoveryResult


@runtime_checkable
class DiscoveryPort(Protocol):
    def discover_context(self, context: DiscoveryContext) -> DiscoveryResult:
        ...

