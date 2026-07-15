from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.application.product_onboarding import ProductOnboardingService
from backend.contracts.product_onboarding import (
    AdapterAnalysis,
    AdapterDraft,
    AdapterValidation,
    CandidateDocument,
    CandidateReplay,
    OnboardingInputError,
    RegexEvaluation,
    RegexEvaluationPlan,
    SampleBatch,
)


class _Samples:
    def __init__(self) -> None:
        self.calls = 0

    def read(self, input_files, *, encoding, limits):
        self.calls += 1
        return SampleBatch(encoding=encoding, files=())


class _Candidates:
    def __init__(self, document: CandidateDocument) -> None:
        self.document = document

    def read(self, path):
        return self.document


class _Sandbox:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, lines, plan):
        self.calls += 1
        return RegexEvaluation(
            status="ok",
            counters={"record_matches": 1},
            group_counts={},
            probe_counts={},
        )


@dataclass
class _Adapter:
    validation: AdapterValidation
    draft_calls: int = 0

    @property
    def adapter_id(self) -> str:
        return "adapter-a"

    def analyze(self, batch):
        return AdapterAnalysis(status="needs_candidate", data={"files": []})

    def prepare_candidate(self, batch, document):
        return CandidateReplay(
            lines=("bounded",),
            plan=RegexEvaluationPlan(
                timestamp_pattern=r"(2026)()",
                record_pattern=r"value",
            ),
        )

    def finalize_validation(self, replay, evaluation):
        return self.validation

    def build_draft(self, batch, replay, validation):
        self.draft_calls += 1
        return AdapterDraft(
            fragment={"archive": {}, "discovery": {}, "parser": {}, "mechanisms": {}},
            unresolved=("policy",),
            runtime_caveats=("caveat",),
        )


def _service(
    validation: AdapterValidation,
    *,
    document: CandidateDocument | None = None,
    validator=None,
):
    samples = _Samples()
    sandbox = _Sandbox()
    adapter = _Adapter(validation)
    captured: list[dict] = []

    def validate_fragment(root):
        captured.append(root)
        return [] if validator is None else validator(root)

    service = ProductOnboardingService(
        sample_reader=samples,
        candidate_reader=_Candidates(
            document or CandidateDocument(schema_version=1, adapter="adapter-a", payload={})
        ),
        sandbox=sandbox,
        adapter=adapter,
        fragment_validator=validate_fragment,
    )
    return service, samples, sandbox, adapter, captured


def test_analyze_reads_the_batch_once_and_wraps_adapter_data() -> None:
    service, samples, sandbox, _adapter, _captured = _service(
        AdapterValidation("syntax_ready", {}, {})
    )

    report = service.analyze(["sample.log"]).to_dict()

    assert samples.calls == 1
    assert sandbox.calls == 0
    assert report["operation"] == "analyze"
    assert report["analysis"] == {"files": []}


def test_validate_orchestrates_one_sample_read_and_one_sandbox_call() -> None:
    validation = AdapterValidation(
        "syntax_ready_needs_policy_confirmation",
        {"matches": 1},
        {"field": 1},
        warnings=("policy.review",),
    )
    service, samples, sandbox, _adapter, _captured = _service(validation)

    report = service.validate(["sample.log"], "candidate.json").to_dict()

    assert samples.calls == 1
    assert sandbox.calls == 1
    assert report["status"] == "syntax_ready_needs_policy_confirmation"
    assert report["validation"]["final_config_ready"] is False


def test_candidate_version_and_adapter_are_checked_before_sample_read() -> None:
    for document in (
        CandidateDocument(schema_version=2, adapter="adapter-a", payload={}),
        CandidateDocument(schema_version=1, adapter="adapter-b", payload={}),
    ):
        service, samples, _sandbox, _adapter, _captured = _service(
            AdapterValidation("syntax_ready", {}, {}),
            document=document,
        )

        with pytest.raises(OnboardingInputError):
            service.validate(["sample.log"], "candidate.json")
        assert samples.calls == 0


def test_build_draft_wraps_and_validates_one_schema_v2_root() -> None:
    validation = AdapterValidation(
        "syntax_ready",
        {"matches": 1},
        {},
        unresolved=("policy",),
    )
    service, samples, sandbox, adapter, captured = _service(validation)

    report = service.build_draft(["sample.log"], "candidate.json").to_dict()

    assert samples.calls == 1
    assert sandbox.calls == 1
    assert adapter.draft_calls == 1
    assert captured[0]["schema_version"] == 2
    assert set(captured[0]["products"]) == {"draft"}
    assert report["status"] == "needs_policy_confirmation"
    assert report["must_not_persist"] is True
    assert report["final_config_ready"] is False


def test_build_draft_stops_before_projection_when_candidate_is_not_ready() -> None:
    validation = AdapterValidation(
        "needs_review",
        {},
        {},
        warnings=("candidate.review",),
    )
    service, _samples, _sandbox, adapter, captured = _service(validation)

    report = service.build_draft(["sample.log"], "candidate.json").to_dict()

    assert adapter.draft_calls == 0
    assert captured == []
    assert report["status"] == "draft_not_built"


def test_invalid_projected_fragment_is_not_returned() -> None:
    service, _samples, _sandbox, _adapter, _captured = _service(
        AdapterValidation("syntax_ready", {}, {}),
        validator=lambda _root: ["private validator detail"],
    )

    report = service.build_draft(["sample.log"], "candidate.json").to_dict()

    assert report["status"] == "draft_not_built"
    assert "draft_product_config" not in report
    assert report["diagnostics"][-1]["code"] == "LP_ONBOARD_DRAFT_CONTRACT_INVALID"
