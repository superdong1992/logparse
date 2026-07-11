from __future__ import annotations

from pathlib import Path

import pytest

from backend.application.parse_service import ParseService, ParseServiceError
from backend.contracts.runtime import (
    ArtifactRecord,
    Diagnostic,
    ParseEngineError,
    ParseEngineOutcome,
    ParseRequest,
    StageResult,
    StageStatus,
)
from backend.extensions.products.current.engine import CurrentProductParseEngine
from backend.extensions.products.current.models import ParseResult
from backend.extensions.products.current.pipeline import PipelineStageError


class _Session:
    def __init__(self):
        self.result = None
        self.finalized = None

    def write_result(self, payload):
        self.result = payload
        return ArtifactRecord("result", "result.json")

    def finalize(self, **kwargs):
        self.finalized = kwargs
        return (ArtifactRecord("parse_manifest", "parse_manifest.json"),)


def test_parse_service_commits_result_and_manifest(tmp_path: Path) -> None:
    class Engine:
        def execute(self, request):
            return ParseEngineOutcome(
                result={"value": 1},
                stages=(StageResult("parse", StageStatus.SUCCEEDED),),
                counters={"files": 2},
            )

    session = _Session()
    service = ParseService(
        Engine(),
        artifact_session_factory=lambda _root, _task: session,
        result_serializer=lambda state: {"schema_version": 2, **state},
    )

    run = service.run(ParseRequest(tmp_path / "input.zip", tmp_path / "output"))

    assert session.result == {"schema_version": 2, "value": 1}
    assert session.finalized["status"] == "success"
    assert run.artifacts[0].name == "parse_manifest"


def test_parse_service_writes_failed_manifest_when_engine_raises(tmp_path: Path) -> None:
    class Engine:
        def execute(self, request):
            raise RuntimeError("boom")

    session = _Session()
    service = ParseService(
        Engine(),
        artifact_session_factory=lambda _root, _task: session,
        result_serializer=lambda state: state,
    )

    with pytest.raises(ParseServiceError, match="boom"):
        service.run(ParseRequest(tmp_path / "input.zip", tmp_path / "output"))

    assert session.result is None
    assert session.finalized["status"] == "failed"
    assert session.finalized["diagnostics"][0].code == "LP_PARSE_ENGINE_FAILED"


def test_parse_service_preserves_structured_fatal_engine_stage(tmp_path: Path) -> None:
    diagnostic = Diagnostic(
        code="LP_DISCOVERY_FAILED",
        message="discovery failed",
        stage="pipeline.discovery",
    )

    class Engine:
        def execute(self, request):
            raise ParseEngineError(
                "discovery failed",
                stages=(
                    StageResult(
                        "pipeline.discovery",
                        StageStatus.FAILED,
                        diagnostics=(diagnostic,),
                    ),
                ),
                diagnostics=(diagnostic,),
            )

    session = _Session()
    service = ParseService(
        Engine(),
        artifact_session_factory=lambda _root, _task: session,
        result_serializer=lambda state: state,
    )

    with pytest.raises(ParseServiceError, match="discovery failed"):
        service.run(ParseRequest(tmp_path / "input.zip", tmp_path / "output"))

    assert session.finalized["status"] == "failed"
    assert session.finalized["stages"][0].name == "pipeline.discovery"
    assert session.finalized["diagnostics"][0].code == "LP_DISCOVERY_FAILED"


def test_parse_service_records_artifact_failure(tmp_path: Path) -> None:
    class Engine:
        def execute(self, request):
            return ParseEngineOutcome(result={"value": 1})

    class BrokenSession(_Session):
        def write_result(self, payload):
            raise OSError("disk full")

    session = BrokenSession()
    service = ParseService(
        Engine(),
        artifact_session_factory=lambda _root, _task: session,
        result_serializer=lambda state: state,
    )

    with pytest.raises(ParseServiceError, match="disk full"):
        service.run(ParseRequest(tmp_path / "input.zip", tmp_path / "output"))

    assert session.finalized["status"] == "failed"
    assert session.finalized["diagnostics"][-1].code == "LP_ARTIFACT_WRITE_FAILED"


def test_current_product_engine_maps_fatal_stage_to_structured_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = CurrentProductParseEngine({})
    engine.pipeline.stage_records = [
        {"name": "pipeline.discovery", "success": False, "metrics": {}}
    ]

    def fail(*args, **kwargs):
        raise PipelineStageError("pipeline.discovery", "unknown layout")

    monkeypatch.setattr(engine.pipeline, "run", fail)

    with pytest.raises(ParseEngineError) as caught:
        engine.execute(ParseRequest(tmp_path / "input.zip", tmp_path / "output"))

    assert caught.value.diagnostics[0].code == "LP_DISCOVERY_FAILED"
    assert caught.value.stages[0].status == StageStatus.FAILED


def test_current_product_engine_keeps_isolated_mechanism_error_partial(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = CurrentProductParseEngine({})
    result = ParseResult(errors=["[module1] parse 异常: broken rule"])
    engine.pipeline.stage_records = [
        {"name": "pipeline.parse", "success": True, "metrics": {}}
    ]
    monkeypatch.setattr(engine.pipeline, "run", lambda *args, **kwargs: result)

    outcome = engine.execute(
        ParseRequest(tmp_path / "input.zip", tmp_path / "output")
    )

    assert outcome.status == "partial"
    assert outcome.diagnostics[0].code == "LP_MECHANISM_FAILED"
