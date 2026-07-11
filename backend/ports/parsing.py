"""Port implemented by a product parse engine."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.contracts.runtime import ParseEngineOutcome, ParseRequest


@runtime_checkable
class ParseEnginePort(Protocol):
    def execute(self, request: ParseRequest) -> ParseEngineOutcome:
        ...
