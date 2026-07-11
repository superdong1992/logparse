"""Generic topology and cycle identities.

The core only understands an ordered hierarchy of typed segments.  Product
extensions decide the meaning of each segment.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True, slots=True)
class ScopeSegment:
    kind: str
    value: str

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("scope segment kind must be non-empty")
        if not self.value.strip():
            raise ValueError("scope segment value must be non-empty")


@dataclass(frozen=True, order=True, slots=True)
class ScopeRef:
    """Stable reference to a product-defined hierarchical scope."""

    segments: tuple[ScopeSegment, ...]

    def __post_init__(self) -> None:
        if not self.segments:
            raise ValueError("scope must contain at least one segment")

    def child(self, kind: str, value: str) -> "ScopeRef":
        return ScopeRef((*self.segments, ScopeSegment(kind, value)))

    @property
    def identity(self) -> str:
        return "/".join(f"{item.kind}:{item.value}" for item in self.segments)


@dataclass(frozen=True, order=True, slots=True)
class CycleRef:
    """Stable cycle identity that does not rely on object identity or paths."""

    scope: ScopeRef
    cycle_id: str
    ordinal: int = 0

    def __post_init__(self) -> None:
        if not self.cycle_id.strip():
            raise ValueError("cycle_id must be non-empty")
        if self.ordinal < 0:
            raise ValueError("cycle ordinal must be non-negative")
