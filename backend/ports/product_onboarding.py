"""Product-neutral ports for the log-format onboarding use case."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

from backend.contracts.product_onboarding import (
    AdapterAnalysis,
    AdapterDraft,
    AdapterValidation,
    CandidateDocument,
    CandidateReplay,
    RegexEvaluation,
    RegexEvaluationPlan,
    SampleBatch,
    SamplingLimits,
)


@runtime_checkable
class SampleReaderPort(Protocol):
    def read(
        self,
        input_files: Sequence[str | Path],
        *,
        encoding: str,
        limits: SamplingLimits,
    ) -> SampleBatch: ...


@runtime_checkable
class CandidateReaderPort(Protocol):
    def read(self, path: str | Path) -> CandidateDocument: ...


@runtime_checkable
class RegexSandboxPort(Protocol):
    def evaluate(
        self,
        lines: Sequence[str],
        plan: RegexEvaluationPlan,
    ) -> RegexEvaluation: ...


@runtime_checkable
class ProductOnboardingAdapterPort(Protocol):
    @property
    def adapter_id(self) -> str: ...

    def analyze(self, batch: SampleBatch) -> AdapterAnalysis: ...

    def prepare_candidate(
        self,
        batch: SampleBatch,
        document: CandidateDocument,
    ) -> CandidateReplay: ...

    def finalize_validation(
        self,
        replay: CandidateReplay,
        evaluation: RegexEvaluation,
    ) -> AdapterValidation: ...

    def build_draft(
        self,
        batch: SampleBatch,
        replay: CandidateReplay,
        validation: AdapterValidation,
    ) -> AdapterDraft: ...
