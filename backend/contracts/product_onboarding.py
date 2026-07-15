"""Versioned, product-neutral contracts for log-format onboarding.

Raw sample text is an in-process implementation detail.  None of the public
report serializers in this module expose sample lines, source paths, or the
candidate document body.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


REPORT_CONTRACT = "logparse.product_onboarding.report"
REPORT_SCHEMA_VERSION = 1
CANDIDATE_SCHEMA_VERSION = 1

EXIT_SUCCESS = 0
EXIT_INPUT_ERROR = 2
EXIT_TECHNICAL_FAILURE = 3
EXIT_POLICY_CONFIRMATION_REQUIRED = 4


class OnboardingError(RuntimeError):
    """Safe boundary error whose text contains no private input material."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class OnboardingInputError(OnboardingError):
    """Invalid CLI input, sample metadata, or candidate document."""


class OnboardingTechnicalError(OnboardingError):
    """Internal or isolated-execution failure safe to expose to callers."""


@dataclass(frozen=True, slots=True)
class SamplingLimits:
    max_files: int = 64
    max_lines_per_file: int = 512
    max_characters_per_line: int = 4096
    max_total_characters: int = 1024 * 1024

    def __post_init__(self) -> None:
        for name in (
            "max_files",
            "max_lines_per_file",
            "max_characters_per_line",
            "max_total_characters",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class SampleFile:
    file_id: str
    name: str
    size_bytes: int
    compressed: bool
    sampled_lines: int
    nonempty_lines: int
    sampled_characters: int
    replacement_characters: int
    truncated: bool
    binary_likely: bool
    lines: tuple[str, ...] = field(repr=False, compare=False)

    def public_profile(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "name": self.name,
            "size_bytes": self.size_bytes,
            "compressed": self.compressed,
            "sampled_lines": self.sampled_lines,
            "nonempty_lines": self.nonempty_lines,
            "sampled_characters": self.sampled_characters,
            "replacement_characters": self.replacement_characters,
            "truncated": self.truncated,
            "binary_likely": self.binary_likely,
        }


@dataclass(frozen=True, slots=True)
class SampleBatch:
    encoding: str
    files: tuple[SampleFile, ...]


@dataclass(frozen=True, slots=True)
class CandidateDocument:
    schema_version: int
    adapter: str
    payload: Mapping[str, Any] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class CaptureProbe:
    probe_id: str
    kind: str
    group: str
    value: str = ""


@dataclass(frozen=True, slots=True)
class RegexEvaluationPlan:
    timestamp_pattern: str
    record_pattern: str
    ordinal_pattern: str = ""
    required_groups: tuple[str, ...] = ()
    probes: tuple[CaptureProbe, ...] = ()
    timestamp_capture_count: int = 2
    ordinal_capture_count: int = 1


@dataclass(frozen=True, slots=True)
class RegexEvaluation:
    status: str
    counters: Mapping[str, int] = field(default_factory=dict)
    group_counts: Mapping[str, int] = field(default_factory=dict)
    probe_counts: Mapping[str, int] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    @classmethod
    def rejected(cls, *errors: str) -> "RegexEvaluation":
        return cls(status="rejected", errors=tuple(errors))


@dataclass(frozen=True, slots=True)
class AdapterAnalysis:
    status: str
    data: Mapping[str, Any]
    diagnostics: tuple["OnboardingDiagnostic", ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateReplay:
    lines: tuple[str, ...] = field(repr=False, compare=False)
    plan: RegexEvaluationPlan | None = None
    metrics: Mapping[str, int] = field(default_factory=dict)
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    state: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class AdapterValidation:
    status: str
    metrics: Mapping[str, int]
    group_counts: Mapping[str, int]
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()

    @property
    def technically_ready(self) -> bool:
        return self.status in {
            "syntax_ready",
            "syntax_ready_needs_policy_confirmation",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "readiness_scope": "candidate_syntax_only",
            "final_config_ready": False,
            "metrics": dict(self.metrics),
            "group_populated": dict(self.group_counts),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "unresolved": list(self.unresolved),
        }


@dataclass(frozen=True, slots=True)
class AdapterDraft:
    fragment: Mapping[str, Any]
    unresolved: tuple[str, ...]
    runtime_caveats: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OnboardingDiagnostic:
    code: str
    message: str
    severity: str = "warning"

    def __post_init__(self) -> None:
        if self.severity not in {"info", "warning", "error"}:
            raise ValueError("unsupported onboarding diagnostic severity")
        if not self.code.strip() or not self.message.strip():
            raise ValueError("onboarding diagnostics require code and message")

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True, slots=True)
class OnboardingReport:
    operation: str
    adapter: str
    status: str
    diagnostics: tuple[OnboardingDiagnostic, ...] = ()
    final_config_ready: bool = False
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contract": REPORT_CONTRACT,
            "schema_version": REPORT_SCHEMA_VERSION,
            "operation": self.operation,
            "adapter": self.adapter,
            "status": self.status,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "final_config_ready": self.final_config_ready,
        }
        for key, value in self.data.items():
            if key not in payload:
                payload[key] = value
        return payload
