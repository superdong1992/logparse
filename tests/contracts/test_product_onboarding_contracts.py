from __future__ import annotations

import pytest

from backend.contracts.product_onboarding import (
    AdapterValidation,
    CandidateDocument,
    EXIT_INPUT_ERROR,
    EXIT_POLICY_CONFIRMATION_REQUIRED,
    EXIT_SUCCESS,
    EXIT_TECHNICAL_FAILURE,
    OnboardingDiagnostic,
    OnboardingReport,
    SampleBatch,
    SampleFile,
    SamplingLimits,
)


def test_public_sample_contract_does_not_repr_raw_lines() -> None:
    secret = "PRIVATE_LOG_BODY"
    sample = SampleFile(
        file_id="file_001",
        name="sample.log",
        size_bytes=10,
        compressed=False,
        sampled_lines=1,
        nonempty_lines=1,
        sampled_characters=10,
        replacement_characters=0,
        truncated=False,
        binary_likely=False,
        lines=(secret,),
    )

    assert secret not in repr(sample)
    assert secret not in repr(SampleBatch(encoding="utf-8", files=(sample,)))
    assert "lines" not in sample.public_profile()


@pytest.mark.parametrize(
    "field",
    [
        "max_files",
        "max_lines_per_file",
        "max_characters_per_line",
        "max_total_characters",
    ],
)
def test_sampling_limits_require_positive_integers(field: str) -> None:
    values = {
        "max_files": 1,
        "max_lines_per_file": 1,
        "max_characters_per_line": 1,
        "max_total_characters": 1,
    }
    values[field] = 0

    with pytest.raises(ValueError, match=field):
        SamplingLimits(**values)


def test_candidate_document_payload_is_immutable_and_hidden_from_repr() -> None:
    document = CandidateDocument(
        schema_version=1,
        adapter="adapter-a",
        payload={"secret": "PRIVATE_CANDIDATE"},
    )

    assert "PRIVATE_CANDIDATE" not in repr(document)
    with pytest.raises(TypeError):
        document.payload["new"] = "value"  # type: ignore[index]


def test_report_envelope_is_versioned_and_cannot_be_overridden() -> None:
    report = OnboardingReport(
        operation="analyze",
        adapter="adapter-a",
        status="needs_candidate",
        diagnostics=(
            OnboardingDiagnostic(
                code="LP_ONBOARD_NOTE",
                message="review required",
                severity="info",
            ),
        ),
        data={"contract": "wrong", "analysis": {"files": 1}},
    ).to_dict()

    assert report["contract"] == "logparse.product_onboarding.report"
    assert report["schema_version"] == 1
    assert report["operation"] == "analyze"
    assert report["final_config_ready"] is False
    assert report["analysis"] == {"files": 1}


def test_validation_contract_never_claims_final_readiness() -> None:
    payload = AdapterValidation(
        status="syntax_ready",
        metrics={"matched": 1},
        group_counts={"field": 1},
    ).to_dict()

    assert payload["readiness_scope"] == "candidate_syntax_only"
    assert payload["final_config_ready"] is False


def test_exit_codes_are_stable() -> None:
    assert (
        EXIT_SUCCESS,
        EXIT_INPUT_ERROR,
        EXIT_TECHNICAL_FAILURE,
        EXIT_POLICY_CONFIRMATION_REQUIRED,
    ) == (0, 2, 3, 4)
