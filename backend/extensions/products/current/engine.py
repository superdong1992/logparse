"""Current product adapter for the frozen ParseEnginePort."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from backend.contracts.runtime import (
    Diagnostic,
    DiagnosticSeverity,
    ParseEngineError,
    ParseEngineOutcome,
    ParseRequest,
    StageResult,
    StageStatus,
)
from backend.extensions.products.current.pipeline import Pipeline, PipelineStageError


class CurrentProductParseEngine:
    def __init__(self, runtime_config: Mapping[str, Any]):
        self.pipeline = Pipeline(dict(runtime_config))

    def execute(self, request: ParseRequest) -> ParseEngineOutcome:
        options = request.options
        self.pipeline.pipeline_config.update(
            {
                "debug_expand_gz": options.debug_expand_gz,
                "extraction_workers": options.extraction_workers,
                "diagnostic_scan_workers": options.diagnostic_scan_workers,
            }
        )
        try:
            result = self.pipeline.run(
                request.source,
                request.output_root,
                product=request.product,
                task_id=request.task_id,
                verbose=options.verbose,
                profile=options.profile,
                keep_workspace=options.keep_workspace,
            )
        except PipelineStageError as exc:
            stages = _stage_results(self.pipeline.stage_records)
            diagnostic = Diagnostic(
                code=_fatal_stage_code(exc.stage_name),
                message=str(exc),
                severity=DiagnosticSeverity.ERROR,
                stage=exc.stage_name,
            )
            stages = tuple(
                StageResult(
                    name=stage.name,
                    status=stage.status,
                    diagnostics=(diagnostic,) if stage.name == exc.stage_name else (),
                    metrics=stage.metrics,
                )
                for stage in stages
            )
            raise ParseEngineError(
                str(exc),
                stages=stages,
                diagnostics=(diagnostic,),
            ) from exc
        structured_diagnostics = tuple(self.pipeline.last_diagnostics)
        structured_messages = {item.message for item in structured_diagnostics}
        legacy_diagnostics = tuple(
            Diagnostic(
                code=_diagnostic_code(message),
                message=str(message),
                severity=DiagnosticSeverity.ERROR,
                stage="parse",
            )
            for message in result.errors
            if str(message) not in structured_messages
        )
        diagnostics = (*structured_diagnostics, *legacy_diagnostics)
        stages = _stage_results(self.pipeline.stage_records)
        return ParseEngineOutcome(
            result=result,
            status="partial" if diagnostics else "success",
            stages=stages,
            diagnostics=diagnostics,
            counters=_counters(result, self.pipeline.last_scan_batch),
            workspace=self.pipeline.last_workspace,
            created_at=result.created_at.isoformat(),
        )


def _stage_results(records: list[dict[str, Any]]) -> tuple[StageResult, ...]:
    return tuple(
            StageResult(
                name=str(record["name"]),
                status=(
                    StageStatus.SUCCEEDED
                    if record.get("success")
                    else StageStatus.FAILED
                ),
                metrics=_safe_metrics(record.get("metrics", {})),
            )
            for record in records
        )


def _fatal_stage_code(stage_name: str) -> str:
    return {
        "pipeline.extract": "LP_EXTRACTION_FAILED",
        "pipeline.discovery": "LP_DISCOVERY_FAILED",
        "pipeline.parse": "LP_PARSE_FAILED",
        "pipeline.write_output": "LP_EVIDENCE_WRITE_FAILED",
        "pipeline.metadata": "LP_METADATA_WRITE_FAILED",
    }.get(stage_name, "LP_PARSE_ENGINE_FAILED")


def _diagnostic_code(message: str) -> str:
    if "shared diagnostic scan" in message:
        return "LP_DIAGNOSTIC_SCAN_FAILED"
    if "parse 异常" in message:
        return "LP_MECHANISM_FAILED"
    return "LP_PARSE_PARTIAL"


def _safe_metrics(raw: Any) -> dict[str, int | float | str | bool]:
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(key): value
        for key, value in raw.items()
        if isinstance(value, (int, float, str, bool))
        and not isinstance(value, Path)
    }


def _counters(result: Any, scan_batch: Any) -> dict[str, int]:
    discovered_files = sum(
        len(scope.diagnostic_logs) for scope in result.diagnostic_slots
    ) + sum(len(source.journal_logs) for source in result.private_slots)
    mechanism_entries = sum(
        int(item.diag_entry_count) + int(item.journal_entry_count)
        for item in result.mech_results
    )
    return {
        "discovered_scope_count": len(result.diagnostic_slots),
        "private_source_count": len(result.private_slots),
        "discovered_file_count": discovered_files,
        "log_line_count": int(getattr(scan_batch, "line_count", 0)),
        "mechanism_result_count": len(result.mech_results),
        "mechanism_entry_count": mechanism_entries,
    }
