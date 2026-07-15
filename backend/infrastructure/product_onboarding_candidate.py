"""Bounded JSON document reader for onboarding candidates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.contracts.product_onboarding import (
    CandidateDocument,
    OnboardingInputError,
)


_MAX_DOCUMENT_BYTES = 64 * 1024
_MAX_DEPTH = 24
_MAX_NODES = 4096
_MAX_STRING_CHARACTERS = 16 * 1024


class SafeCandidateReader:
    def read(self, path: str | Path) -> CandidateDocument:
        try:
            candidate_path = Path(path)
        except (TypeError, ValueError):
            raise _document_error() from None
        try:
            if candidate_path.is_symlink() or not candidate_path.is_file():
                raise _document_error()
            if candidate_path.stat().st_size > _MAX_DOCUMENT_BYTES:
                raise _too_large_error()
            with candidate_path.open("rb") as stream:
                raw_document = stream.read(_MAX_DOCUMENT_BYTES + 1)
            if len(raw_document) > _MAX_DOCUMENT_BYTES:
                raise _too_large_error()
            serialized = raw_document.decode("utf-8")
        except OnboardingInputError:
            raise
        except (OSError, UnicodeError):
            raise _document_error() from None

        try:
            payload = json.loads(
                serialized,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
            _validate_shape(payload)
        except (ValueError, RecursionError, MemoryError):
            raise _document_error() from None
        if not isinstance(payload, dict):
            raise _document_error()

        schema_version = payload.get("schema_version")
        adapter = payload.get("adapter")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or not isinstance(adapter, str)
            or not adapter.strip()
            or adapter != adapter.strip()
            or len(adapter) > 128
            or any(value in adapter for value in "\r\n\x00")
        ):
            raise _document_error()
        candidate_payload = {
            key: value for key, value in payload.items() if key not in {"schema_version", "adapter"}
        }
        return CandidateDocument(
            schema_version=schema_version,
            adapter=adapter,
            payload=candidate_payload,
        )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> Any:
    raise ValueError("non-finite JSON number")


def _validate_shape(value: Any) -> None:
    remaining = _MAX_NODES
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        remaining -= 1
        if remaining < 0 or depth > _MAX_DEPTH:
            raise ValueError("candidate shape limit")
        if isinstance(item, str):
            if len(item) > _MAX_STRING_CHARACTERS:
                raise ValueError("candidate string limit")
            continue
        if isinstance(item, dict):
            stack.extend((entry, depth + 1) for entry in item.values())
            continue
        if isinstance(item, list):
            stack.extend((entry, depth + 1) for entry in item)


def _document_error() -> OnboardingInputError:
    return OnboardingInputError(
        "LP_ONBOARD_CANDIDATE_DOCUMENT_INVALID",
        "candidate document must be one bounded JSON object",
    )


def _too_large_error() -> OnboardingInputError:
    return OnboardingInputError(
        "LP_ONBOARD_CANDIDATE_DOCUMENT_TOO_LARGE",
        "candidate document exceeds the size limit",
    )
