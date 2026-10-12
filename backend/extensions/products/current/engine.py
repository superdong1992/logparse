"""直接消费 schema v2 的产品解析引擎。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Mapping

from backend.contracts.runtime import (
    Diagnostic,
    DiagnosticSeverity,
    ParseEngineError,
    ParseEngineOutcome,
    ParseRequest,
    ParseRuntimeOptions,
    StageResult,
    StageStatus,
)
from backend.decompressor import Decompressor
from backend.extensions.products.current.metadata import MetadataGenerator
from backend.extensions.products.current.models import ParseResult
from backend.infrastructure.artifact_layout import ArtifactLayout
from backend.infrastructure.artifact_reader import read_json_artifact
from backend.performance import PerformanceRecorder, resolve_worker_count
from backend.plugins.base import DirectoryDiscoveryPlugin, LogParserPlugin
from backend.plugins.loader import instantiate_plugin


class CurrentProductParseEngine:
    def __init__(self, config: Mapping[str, Any]):
        self.config = deepcopy(dict(config))
        self.performance = PerformanceRecorder(enabled=False)

    def execute(self, request: ParseRequest) -> ParseEngineOutcome:
        task_id = request.task_id or request.source.stem
        layout = ArtifactLayout(request.output_root, task_id)
        products = self.config.get("products", {})
        if request.product not in products:
            raise ParseEngineError(
                f"未找到产品配置: {request.product}",
                diagnostics=(
                    Diagnostic(
                        "LP_CONFIG_INVALID", f"未找到产品配置: {request.product}", stage="config"
                    ),
                ),
                preserve_existing_output=True,
            )
        product = products[request.product]
        configured = self.config.get("pipeline", {})
        options = replace(
            request.options,
            **{
                name: getattr(request.options, name)
                if getattr(request.options, name) is not None
                else configured.get(name, default)
                for name, default in (
                    ("extraction_workers", "auto"),
                    ("diagnostic_scan_workers", "auto"),
                    ("debug_expand_gz", False),
                    ("keep_workspace", False),
                )
            },
        )
        try:
            previous_artifacts = self._prepare_task(request.source, layout)
        except (OSError, ValueError) as exc:
            diagnostic = Diagnostic("LP_UNSAFE_TASK", str(exc), stage="pipeline.prepare")
            raise ParseEngineError(
                str(exc),
                stages=(StageResult("pipeline.prepare", StageStatus.FAILED, diagnostics=(diagnostic,)),),
                diagnostics=(diagnostic,),
                preserve_existing_output=True,
            ) from exc
        try:
            for path in previous_artifacts:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        except OSError as exc:
            diagnostic = Diagnostic("LP_TASK_RESET_FAILED", str(exc), stage="pipeline.prepare")
            old_workspace = layout.task_dir / "extracted"
            raise ParseEngineError(
                str(exc),
                stages=(StageResult("pipeline.prepare", StageStatus.FAILED, diagnostics=(diagnostic,)),),
                diagnostics=(diagnostic,),
                workspace=old_workspace if old_workspace.is_dir() else None,
            ) from exc
        if options.keep_workspace:
            workspace = layout.task_dir / "extracted"
            return self._execute_in_workspace(request, options, product, layout, workspace)
        with tempfile.TemporaryDirectory(prefix=f"logparse-{task_id}-") as temp_root:
            return self._execute_in_workspace(
                request,
                options,
                product,
                layout,
                Path(temp_root) / "extracted",
            )

    def _execute_in_workspace(
        self,
        request: ParseRequest,
        options: ParseRuntimeOptions,
        product: Mapping[str, Any],
        layout: ArtifactLayout,
        workspace: Path,
    ) -> ParseEngineOutcome:
        stages: list[StageResult] = []
        diagnostics: list[Diagnostic] = []
        extraction_workers = resolve_worker_count(options.extraction_workers, default_cap=4)
        diagnostic_workers = resolve_worker_count(options.diagnostic_scan_workers, default_cap=4)
        performance = PerformanceRecorder(
            enabled=options.profile,
            config={
                "debug_expand_gz": options.debug_expand_gz,
                "extraction_workers": options.extraction_workers,
                "extraction_workers_resolved": extraction_workers,
                "diagnostic_scan_workers": options.diagnostic_scan_workers,
                "diagnostic_scan_workers_resolved": diagnostic_workers,
            },
        )
        self.performance = performance

        def stage(name, code, action, metrics=None):
            started = time.perf_counter()
            try:
                value = action()
                counters = metrics(value) if metrics else {}
            except Exception as exc:
                diagnostic = Diagnostic(code, str(exc), stage=name)
                diagnostics.append(diagnostic)
                stages.append(StageResult(name, StageStatus.FAILED, diagnostics=(diagnostic,)))
                performance.record_stage(
                    name, elapsed_seconds=time.perf_counter() - started, error=True
                )
                raise ParseEngineError(
                    str(exc), stages=tuple(stages), diagnostics=tuple(diagnostics),
                    workspace=workspace if options.keep_workspace and workspace.is_dir() else None,
                ) from exc
            elapsed = time.perf_counter() - started
            stages.append(StageResult(name, StageStatus.SUCCEEDED, metrics=counters))
            performance.record_stage(name, elapsed_seconds=elapsed, **counters)
            if options.verbose:
                print(f"  {name} [OK] ({elapsed:.1f}s)")
            return value

        archive = product["archive"]
        decompressor = Decompressor(compressed_extensions=archive["compressed_extensions"])
        stage(
            "pipeline.extract",
            "LP_EXTRACTION_FAILED",
            lambda: self._extract(
                request.source,
                workspace,
                decompressor,
                recursive=archive["recursive_extraction"],
                expand_gz=options.debug_expand_gz,
                workers=extraction_workers,
            ),
            lambda files: {"files": len(files)},
        )

        def discover():
            discovery, parser = self._load_plugins(product, options, performance)
            scopes, private_sources = discovery.discover(workspace)
            diagnostics.extend(getattr(discovery, "last_diagnostics", ()))
            return scopes, private_sources, parser

        scopes, private_sources, parser = stage(
            "pipeline.discovery",
            "LP_DISCOVERY_FAILED",
            discover,
            lambda value: {"diagnostic_slots": len(value[0]), "private_slots": len(value[1])},
        )
        result = ParseResult(
            task_id=layout.task_id,
            package_name=request.source.name,
            extracted_root=str(workspace),
            diagnostic_slots=scopes,
            private_slots=private_sources,
            errors=[item.message for item in diagnostics],
        )
        stage(
            "pipeline.parse",
            "LP_PARSE_FAILED",
            lambda: parser.parse(result),
            lambda _value: {
                "diagnostic_files": sum(len(scope.diagnostic_logs) for scope in scopes),
                "mech_results": len(result.mech_results),
                "errors": len(result.errors),
            },
        )
        diagnostics.extend(getattr(parser, "last_mechanism_diagnostics", ()))
        reported_errors = {item.message for item in diagnostics}
        for message in result.errors:
            if message not in reported_errors:
                diagnostics.append(
                    Diagnostic("LP_PARSE_PARTIAL", message, stage="pipeline.parse")
                )
                reported_errors.add(message)
        batch = getattr(parser, "last_scan_batch", None)
        for mechanism in result.mech_results:
            stage(
                "pipeline.write_output",
                "LP_EVIDENCE_WRITE_FAILED",
                lambda item=mechanism: parser.write_output(item, layout.task_dir),
                lambda _value, item=mechanism: {
                    "module": item.module_key or item.module_name,
                    "slots": len(item.slots),
                },
            )
        stage(
            "pipeline.metadata",
            "LP_METADATA_WRITE_FAILED",
            lambda: MetadataGenerator().generate(result, layout.task_dir, product=request.product),
        )
        layout.mech_modules.mkdir(parents=True, exist_ok=True)
        if options.keep_workspace:
            stages.append(StageResult("pipeline.cleanup", StageStatus.SKIPPED))
        else:
            stage(
                "pipeline.cleanup",
                "LP_WORKSPACE_CLEANUP_FAILED",
                lambda: shutil.rmtree(workspace),
            )
        if options.profile:
            stage(
                "pipeline.profile",
                "LP_ARTIFACT_WRITE_FAILED",
                lambda: performance.write(layout.task_dir),
            )
        return ParseEngineOutcome(
            result=result,
            status="partial"
            if any(d.severity == DiagnosticSeverity.ERROR for d in diagnostics)
            else "success",
            stages=tuple(stages),
            diagnostics=tuple(diagnostics),
            counters=_counters(result, batch),
            workspace=workspace if options.keep_workspace else None,
            created_at=result.created_at.isoformat(),
        )

    def _load_plugins(self, product, options, performance):
        discovery = product["discovery"]
        parser = product["parser"]
        return (
            instantiate_plugin(
                discovery["plugin"], DirectoryDiscoveryPlugin, deepcopy(discovery.get("config", {}))
            ),
            instantiate_plugin(
                parser["plugin"],
                LogParserPlugin,
                deepcopy(parser.get("config", {})),
                mechanisms=deepcopy(product["mechanisms"]),
                runtime_options={"diagnostic_scan_workers": options.diagnostic_scan_workers},
                performance_recorder=performance,
            ),
        )

    @staticmethod
    def _prepare_task(source: Path, layout: ArtifactLayout) -> list[Path]:
        """Validate ownership and all paths before returning the fixed deletion set."""
        _reject_link(source)
        if not source.is_file() and not source.is_dir():
            raise ValueError("输入必须是已有文件或目录。")
        source_root = source.resolve()
        task_root = layout.task_dir.resolve()
        if source_root.is_relative_to(task_root) or (
            source.is_dir() and task_root.is_relative_to(source_root)
        ):
            raise ValueError("输入与任务输出目录不能互相包含。")
        if source.is_dir():
            _safe_tree_files(source, source_root)
        _reject_link(layout.task_dir)
        if layout.task_dir.exists() and not layout.task_dir.is_dir():
            raise ValueError("任务输出路径已被文件占用。")
        reserved = (
            layout.metadata, layout.result, layout.mech_modules, layout.performance,
            layout.dfx_report, layout.dfx_summary, layout.dfx_context,
            layout.task_dir / "extracted", layout.parse_manifest,
        )
        present = [path for path in reserved if path.exists() or path.is_symlink()]
        # Validate every deletion target before deleting any. Arbitrary task-local
        # files are not part of this reserved set and remain untouched.
        for path in present:
            _safe_tree_files(path, task_root)
        if not present:
            return []
        manifest = read_json_artifact(layout.parse_manifest, expected_schema=1)
        if (
            manifest.get("task_id") != layout.task_id
            or type(manifest.get("artifact_contract_version")) is not int
            or manifest.get("artifact_contract_version") != 1
            or manifest.get("status") not in ("success", "partial", "failed")
            or not isinstance(manifest.get("artifacts"), dict)
            or not isinstance(manifest.get("workspace"), dict)
        ):
            raise ValueError("既有任务缺少有效的归属记录，拒绝覆盖。")
        return present

    @staticmethod
    def _extract(source, workspace, decompressor, *, recursive, expand_gz, workers):
        workspace.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            files = []
            for path in _safe_tree_files(source, source.resolve()):
                target = workspace / path.relative_to(source)
                if path.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                if path.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
                    files.append(str(target))
            if recursive:
                for filename in tuple(files):
                    path = Path(filename)
                    if not decompressor.is_compressed(path.name):
                        continue
                    plain_gz = path.name.lower().endswith(".gz") and not path.name.lower().endswith(".tar.gz")
                    if plain_gz:
                        if not expand_gz or path.with_suffix("").exists():
                            continue
                        target = path.parent
                    else:
                        target = path.parent / f"{path.name}_extracted"
                        if target.exists():
                            continue
                    files.extend(decompressor.extract_all(
                        path, target, recursive=True, expand_gz=expand_gz, workers=workers
                    ))
            return files
        if source.is_file() and not decompressor.is_compressed(source.name):
            target = workspace / source.name
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
            return [str(target)]
        return decompressor.extract_all(
            source, workspace, recursive=recursive, expand_gz=expand_gz, workers=workers
        )


def _reject_link(path: Path) -> None:
    if path.is_symlink() or path.is_junction():
        raise ValueError(f"不支持符号链接或目录联接: {path}")


def _safe_tree_files(path: Path, root: Path) -> list[Path]:
    """Inspect without following links, including Windows junctions."""
    _reject_link(path)
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"路径超出允许的目录: {path}")
    paths = [path]
    if path.is_dir():
        def fail(error):
            raise error
        for directory, dirs, files in path.walk(follow_symlinks=False, on_error=fail):
            for name in (*dirs, *files):
                child = directory / name
                _reject_link(child)
                if not child.resolve().is_relative_to(root):
                    raise ValueError(f"路径超出允许的目录: {child}")
                if not child.is_dir() and not child.is_file():
                    raise ValueError(f"不支持特殊文件: {child}")
                paths.append(child)
    return paths


def _counters(result: Any, scan_batch: Any) -> dict[str, int]:
    return {
        "discovered_scope_count": len(result.diagnostic_slots),
        "private_source_count": len(result.private_slots),
        "discovered_file_count": sum(
            len(scope.diagnostic_logs) for scope in result.diagnostic_slots
        )
        + sum(len(source.journal_logs) for source in result.private_slots),
        "log_line_count": int(getattr(scan_batch, "line_count", 0)),
        "mechanism_result_count": len(result.mech_results),
        "mechanism_entry_count": sum(
            int(item.diag_entry_count) + int(item.journal_entry_count)
            for item in result.mech_results
        ),
    }
