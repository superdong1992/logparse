"""Mechanism extension port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.contracts.plugins import (
    MechanismContext,
    MechanismDescriptor,
    MechanismOutcome,
)


@runtime_checkable
class MechanismPort(Protocol):
    @property
    def descriptor(self) -> MechanismDescriptor:
        ...

    def execute(self, context: MechanismContext) -> MechanismOutcome:
        ...

