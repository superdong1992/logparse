"""Application service for deterministic product log-format onboarding."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

from backend.contracts.product_onboarding import (
    AdapterValidation,
    CANDIDATE_SCHEMA_VERSION,
    CandidateDocument,
    OnboardingDiagnostic,
    OnboardingInputError,
    OnboardingReport,
    RegexEvaluation,
    SamplingLimits,
)
from backend.ports.product_onboarding import (
    CandidateReaderPort,
    ProductOnboardingAdapterPort,
    RegexSandboxPort,
    SampleReaderPort,
)


FragmentValidator = Callable[[dict[str, Any]], Sequence[str]]


class ProductOnboardingService:
    """Coordinate injected readers, adapter logic, and isolated evaluation."""

    def __init__(
        self,
        *,
        sample_reader: SampleReaderPort,
        candidate_reader: CandidateReaderPort,
        sandbox: RegexSandboxPort,
        adapter: ProductOnboardingAdapterPort,
        fragment_validator: FragmentValidator,
    ) -> None:
        self._sample_reader = sample_reader
        self._candidate_reader = candidate_reader
        self._sandbox = sandbox
        self._adapter = adapter
        self._fragment_validator = fragment_validator

    @property
    def adapter_id(self) -> str:
        return self._adapter.adapter_id

    def analyze(
        self,
        input_files: Sequence[str | Path],
        *,
        encoding: str = "utf-8",
        limits: SamplingLimits | None = None,
    ) -> OnboardingReport:
        batch = self._sample_reader.read(
            input_files,
            encoding=encoding,
            limits=limits or SamplingLimits(),
        )
        analysis = self._adapter.analyze(batch)
        return OnboardingReport(
            operation="analyze",
            adapter=self.adapter_id,
            status=analysis.status,
            diagnostics=analysis.diagnostics,
            final_config_ready=False,
            data={"analysis": dict(analysis.data)},
        )

    def validate(
        self,
        input_files: Sequence[str | Path],
        candidate_path: str | Path,
        *,
        encoding: str = "utf-8",
        limits: SamplingLimits | None = None,
    ) -> OnboardingReport:
        batch, document = self._load_inputs(
            input_files,
            candidate_path,
            encoding=encoding,
            limits=limits or SamplingLimits(),
        )
        replay, validation = self._evaluate(batch, document)
        return OnboardingReport(
            operation="validate",
            adapter=self.adapter_id,
            status=validation.status,
            diagnostics=_validation_diagnostics(validation),
            final_config_ready=False,
            data={"validation": validation.to_dict()},
        )

    def build_draft(
        self,
        input_files: Sequence[str | Path],
        candidate_path: str | Path,
        *,
        encoding: str = "utf-8",
        limits: SamplingLimits | None = None,
    ) -> OnboardingReport:
        batch, document = self._load_inputs(
            input_files,
            candidate_path,
            encoding=encoding,
            limits=limits or SamplingLimits(),
        )
        replay, validation = self._evaluate(batch, document)
        diagnostics = _validation_diagnostics(validation)
        if not validation.technically_ready:
            return OnboardingReport(
                operation="build-draft",
                adapter=self.adapter_id,
                status="draft_not_built",
                diagnostics=diagnostics,
                final_config_ready=False,
                data={"validation": validation.to_dict()},
            )

        draft = self._adapter.build_draft(batch, replay, validation)
        root = {
            "schema_version": 2,
            "pipeline": {},
            "products": {"draft": dict(draft.fragment)},
        }
        if self._fragment_validator(root):
            invalid = OnboardingDiagnostic(
                code="LP_ONBOARD_DRAFT_CONTRACT_INVALID",
                message="The generated draft failed the configuration contract.",
                severity="error",
            )
            return OnboardingReport(
                operation="build-draft",
                adapter=self.adapter_id,
                status="draft_not_built",
                diagnostics=(*diagnostics, invalid),
                final_config_ready=False,
                data={"validation": validation.to_dict()},
            )

        return OnboardingReport(
            operation="build-draft",
            adapter=self.adapter_id,
            status="needs_policy_confirmation",
            diagnostics=diagnostics,
            final_config_ready=False,
            data={
                "must_not_persist": True,
                "product_config_contract": "schema_v2_product_fragment",
                "product_config_schema_version": 2,
                "input_contract": {
                    "staging": "flat_zip",
                    "preserve_basenames": True,
                    "require_unique_casefolded_basenames": True,
                },
                "draft_product_config": dict(draft.fragment),
                "validation": validation.to_dict(),
                "unresolved": list(draft.unresolved),
                "runtime_caveats": list(draft.runtime_caveats),
            },
        )

    def _load_inputs(
        self,
        input_files: Sequence[str | Path],
        candidate_path: str | Path,
        *,
        encoding: str,
        limits: SamplingLimits,
    ) -> tuple[Any, CandidateDocument]:
        document = self._candidate_reader.read(candidate_path)
        if document.schema_version != CANDIDATE_SCHEMA_VERSION:
            raise OnboardingInputError(
                "LP_ONBOARD_CANDIDATE_VERSION_UNSUPPORTED",
                "candidate schema_version is unsupported",
            )
        if document.adapter != self.adapter_id:
            raise OnboardingInputError(
                "LP_ONBOARD_ADAPTER_UNSUPPORTED",
                "candidate adapter is not available",
            )
        batch = self._sample_reader.read(
            input_files,
            encoding=encoding,
            limits=limits,
        )
        return batch, document

    def _evaluate(
        self,
        batch: Any,
        document: CandidateDocument,
    ) -> tuple[Any, AdapterValidation]:
        replay = self._adapter.prepare_candidate(batch, document)
        if replay.plan is None:
            evaluation = RegexEvaluation.rejected(*replay.errors)
        else:
            evaluation = self._sandbox.evaluate(replay.lines, replay.plan)
        validation = self._adapter.finalize_validation(replay, evaluation)
        return replay, validation


def _validation_diagnostics(
    validation: AdapterValidation,
) -> tuple[OnboardingDiagnostic, ...]:
    diagnostics: list[OnboardingDiagnostic] = []
    diagnostics.extend(
        OnboardingDiagnostic(
            code=code,
            message="Candidate technical validation failed.",
            severity="error",
        )
        for code in validation.errors
    )
    diagnostics.extend(
        OnboardingDiagnostic(
            code=code,
            message="Candidate validation requires review.",
            severity="warning",
        )
        for code in validation.warnings
    )
    return tuple(diagnostics)
