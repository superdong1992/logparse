"""Frozen parse use case and formal artifact commit boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from backend.contracts.runtime import (
    Diagnostic,
    DiagnosticSeverity,
    ParseEngineError,
    ParseRequest,
    ParseRun,
    StageResult,
    StageStatus,
)
from backend.ports.artifacts import ParseArtifactSessionPort
from backend.ports.parsing import ParseEnginePort


ArtifactSessionFactory = Callable[[Path, str], ParseArtifactSessionPort]
ResultSerializer = Callable[[Any], Mapping[str, Any]]


class ParseServiceError(RuntimeError):
    def __init__(self, message: str, *, task_id: str):
        self.task_id = task_id
        super().__init__(message)


class ParseService:
    """Execute an injected product engine and atomically publish its contract."""

    def __init__(
        self,
        engine: ParseEnginePort,
        *,
        artifact_session_factory: ArtifactSessionFactory,
        result_serializer: ResultSerializer,
    ) -> None:
        self._engine = engine
        self._artifact_session_factory = artifact_session_factory
        self._result_serializer = result_serializer

    def run(self, request: ParseRequest) -> ParseRun:
        task_id = request.task_id or request.source.stem
        session = self._artifact_session_factory(request.output_root, task_id)
        try:
            outcome = self._engine.execute(request)
        except Exception as exc:
            if isinstance(exc, ParseEngineError):
                diagnostics = exc.diagnostics
                stages = exc.stages
            else:
                diagnostics = ()
                stages = ()
            if not diagnostics:
                diagnostics = (
                    Diagnostic(
                        code="LP_PARSE_ENGINE_FAILED",
                        message=str(exc),
                        severity=DiagnosticSeverity.ERROR,
                        stage="parse",
                    ),
                )
            if not stages:
                stages = (
                    StageResult(
                        name="parse",
                        status=StageStatus.FAILED,
                        diagnostics=diagnostics,
                    ),
                )
            session.finalize(
                product=request.product,
                status="failed",
                stages=stages,
                counters={},
                diagnostics=diagnostics,
                workspace=None,
            )
            raise ParseServiceError(str(exc), task_id=task_id) from exc

        diagnostics = tuple(outcome.diagnostics)
        status = _normalize_status(outcome.status, diagnostics)
        base_stages = outcome.stages or (
            StageResult(
                name="parse",
                status=(
                    StageStatus.FAILED
                    if status == "failed"
                    else StageStatus.SUCCEEDED
                ),
                diagnostics=diagnostics,
            ),
        )
        try:
            payload = self._result_serializer(outcome.result)
            session.write_result(payload)
        except Exception as exc:
            artifact_diagnostic = Diagnostic(
                code="LP_ARTIFACT_WRITE_FAILED",
                message=str(exc),
                severity=DiagnosticSeverity.ERROR,
                stage="artifacts",
            )
            failed_diagnostics = (*diagnostics, artifact_diagnostic)
            session.finalize(
                product=request.product,
                status="failed",
                stages=(
                    *base_stages,
                    StageResult(
                        name="artifacts",
                        status=StageStatus.FAILED,
                        diagnostics=(artifact_diagnostic,),
                    ),
                ),
                counters=outcome.counters,
                diagnostics=failed_diagnostics,
                workspace=(
                    str(outcome.workspace)
                    if outcome.workspace is not None
                    else None
                ),
                created_at=outcome.created_at,
            )
            raise ParseServiceError(str(exc), task_id=task_id) from exc

        stages = (
            *base_stages,
            StageResult(name="artifacts", status=StageStatus.SUCCEEDED),
        )
        artifacts = session.finalize(
            product=request.product,
            status=status,
            stages=stages,
            counters=outcome.counters,
            diagnostics=diagnostics,
            workspace=str(outcome.workspace) if outcome.workspace is not None else None,
            created_at=outcome.created_at,
        )
        return ParseRun(
            result=outcome.result,
            status=status,
            stages=tuple(stages),
            artifacts=tuple(artifacts),
            diagnostics=diagnostics,
            workspace=outcome.workspace,
        )


def _normalize_status(status: str, diagnostics: tuple[Diagnostic, ...]) -> str:
    normalized = str(status or "success").lower()
    if normalized not in {"success", "partial", "failed"}:
        normalized = "failed"
    if normalized == "success" and any(
        item.severity == DiagnosticSeverity.ERROR for item in diagnostics
    ):
        return "partial"
    return normalized
