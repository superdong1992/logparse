"""Bounded current-product input exposed to native mechanism extensions."""

from __future__ import annotations

from dataclasses import dataclass

from backend.extensions.products.current.models import PrivateSlotInfo, SlotInfo


@dataclass(frozen=True, slots=True)
class CurrentMechanismInput:
    diagnostic_slots: tuple[SlotInfo, ...]
    private_slots: tuple[PrivateSlotInfo, ...]

    @classmethod
    def from_collections(
        cls,
        diagnostic_slots,
        private_slots,
    ) -> "CurrentMechanismInput":
        return cls(tuple(diagnostic_slots), tuple(private_slots))
