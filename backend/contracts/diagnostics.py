"""Product-neutral diagnostics DTOs.

Diagnostics contain codes, bounded structured details, and human-readable
messages.  They are not a carrier for raw log lines or source contexts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from backend.contracts.runtime import DiagnosticSeverity


@dataclass(frozen=True)
class DiagnosticRecord:
    """A stable diagnostic item shared by manifests and deterministic DFX."""

    code: str
    message: str
    severity: DiagnosticSeverity | str = DiagnosticSeverity.ERROR
    stage: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.code).strip():
            raise ValueError("diagnostic code must not be empty")
        try:
            DiagnosticSeverity(self.severity)
        except ValueError as exc:
            raise ValueError(
                f"unsupported diagnostic severity: {self.severity}"
            ) from exc

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": str(self.code),
            "message": str(self.message),
            "severity": DiagnosticSeverity(self.severity).value,
        }
        if self.stage:
            payload["stage"] = str(self.stage)
        if self.detail:
            payload["detail"] = dict(self.detail)
        return payload

    @classmethod
    def from_value(
        cls,
        value: "DiagnosticRecord | Mapping[str, Any] | str",
        *,
        default_code: str = "LP_PARSE_ERROR",
        stage: str = "",
    ) -> "DiagnosticRecord":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls(
                code=str(value.get("code") or default_code),
                message=str(value.get("message") or value.get("detail") or ""),
                severity=str(value.get("severity") or DiagnosticSeverity.ERROR.value),
                stage=str(value.get("stage") or stage),
                detail=(
                    dict(value.get("detail") or {})
                    if isinstance(value.get("detail"), Mapping)
                    else {}
                ),
            )
        return cls(code=default_code, message=str(value), stage=stage)
