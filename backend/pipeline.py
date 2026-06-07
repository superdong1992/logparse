"""通用日志解析管道：编排产品无关的步骤，产品特定逻辑由插件处理。"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any

from backend.decompressor import Decompressor
from backend.metadata import MetadataGenerator
from backend.models import ParseResult
from backend.performance import PerformanceRecorder, resolve_worker_count
from backend.plugins.base import (
    DirectoryDiscoveryPlugin,
    LogParserPlugin,
)
from backend.plugins.loader import instantiate_plugin

logger = logging.getLogger(__name__)


class Pipeline:
    """产品无关日志解析编排器。

    步骤:
      1. Decompress        — 统一解压 archive（通用）
      2. Discovery         — 发现 slot 和文件（产品插件）
      3. Parse             — 解析日志内容（产品插件）
      4. Write Output      — 落盘（产品插件）
      5. Metadata          — 生成 metadata.json（通用）
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        raw_pipeline_config = config.get("pipeline", {})
        self.pipeline_config = raw_pipeline_config if isinstance(raw_pipeline_config, dict) else {}
        self.decompressor = Decompressor(
            compressed_extensions=config.get("compressed_extensions"),
        )
        self.metadata_gen = MetadataGenerator()
        self.performance = PerformanceRecorder(enabled=False)
        self._plugin_cache: dict[str, tuple[DirectoryDiscoveryPlugin, LogParserPlugin]] = {}

    def run(
        self,
        source: Path,
        output_dir: Path,
        product: str = "default",
        task_id: str | None = None,
        verbose: bool = False,
        profile: bool = False,
    ) -> ParseResult:
        """运行完整解析管道。"""
        task_id = task_id or source.stem
        extract_dir = output_dir / task_id / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)

        errors: list[str] = []
        debug_expand_gz = bool(self.pipeline_config.get("debug_expand_gz", False))
        extraction_workers_raw = self.pipeline_config.get("extraction_workers", "auto")
        diagnostic_workers_raw = self.pipeline_config.get("diagnostic_scan_workers", "auto")
        extraction_workers = resolve_worker_count(extraction_workers_raw, default_cap=4)
        diagnostic_workers = resolve_worker_count(diagnostic_workers_raw, default_cap=4)
        self.performance = PerformanceRecorder(
            enabled=profile,
            config={
                "debug_expand_gz": debug_expand_gz,
                "extraction_workers": extraction_workers_raw,
                "extraction_workers_resolved": extraction_workers,
                "diagnostic_scan_workers": diagnostic_workers_raw,
                "diagnostic_scan_workers_resolved": diagnostic_workers,
            },
        )

        def _safe(message: str, fn, stage_name: str | None = None, metrics_fn=None):
            t0 = time.time()
            try:
                r = fn()
            except Exception as e:
                errors.append(f"{message}: {e}")
                logger.warning("%s: %s", message, e)
                if stage_name:
                    self.performance.record_stage(
                        stage_name,
                        elapsed_seconds=time.time() - t0,
                        error=True,
                    )
                return None
            elapsed = time.time() - t0
            if stage_name:
                metrics = metrics_fn(r) if metrics_fn else {}
                self.performance.record_stage(
                    stage_name,
                    elapsed_seconds=elapsed,
                    **metrics,
                )
            if verbose:
                extra = ""
                if isinstance(r, list):
                    extra = f" ({len(r)} 项, {elapsed:.1f}s)"
                elif isinstance(r, dict):
                    extra = f" ({len(r)} 模块, {elapsed:.1f}s)"
                elif isinstance(r, int):
                    extra = f" ({r} 文件, {elapsed:.1f}s)"
                else:
                    extra = f" ({elapsed:.1f}s)"
                print(f"  {message} [OK]{extra}")
            return r

        # Step 1: unified archive extraction. Plain .gz log files stay
        # compressed by default; --debug-expand-gz keeps the searchable
        # extracted/ workflow available for manual debugging.
        extracted = _safe(f"[1/6] 解压 {source.name}",
              lambda: self._extract_all(
                  source,
                  extract_dir,
                  recursive=self.pipeline_config.get("recursive_extraction", False),
                  expand_gz=debug_expand_gz,
                  workers=extraction_workers,
              ),
              "pipeline.extract",
              lambda r: {"files": len(r) if isinstance(r, list) else 0})
        if verbose and extracted is not None:
            print(f"    解压文件数: {extracted}")

        # Step 2: 目录发现
        discovery, log_parser = self._load_plugins(product)
        if hasattr(log_parser, "config") and isinstance(log_parser.config, dict):
            log_parser.config["_pipeline"] = {
                "diagnostic_scan_workers": diagnostic_workers_raw,
                "diagnostic_scan_workers_resolved": diagnostic_workers,
            }
        setattr(log_parser, "performance_recorder", self.performance)
        diag_slots, private_slots = _safe("[2/6] 扫描 diag/",
                                           lambda: discovery.discover(extract_dir),
                                           "pipeline.discovery",
                                           lambda r: {
                                               "diagnostic_slots": len(r[0]) if isinstance(r, tuple) else 0,
                                               "private_slots": len(r[1]) if isinstance(r, tuple) else 0,
                                           }) or ([], [])
        if verbose:
            diag_file_count = sum(len(s.diagnostic_logs) for s in diag_slots)
            journal_file_count = sum(len(ps.journal_logs) for ps in private_slots)
            print(f"    诊断日志槽位: {len(diag_slots)} ({diag_file_count} 文件)")
            print(f"    私有日志槽位: {len(private_slots)} ({journal_file_count} 文件)")

        result = ParseResult(
            task_id=task_id,
            package_name=source.name,
            extracted_root=str(extract_dir),
            diagnostic_slots=diag_slots,
            private_slots=private_slots,
            errors=errors,
        )

        # Step 3 is intentionally absent: all archive extraction happens in
        # Step 1. Scanners consume the unified extracted workspace.

        # Step 4: 日志解析
        _safe("[4/6] 日志解析 (时间戳+周期+机制模块+角色)",
              lambda: log_parser.parse(result),
              "pipeline.parse",
              lambda _r: {
                  "diagnostic_files": sum(len(s.diagnostic_logs) for s in result.diagnostic_slots),
                  "mech_results": len(result.mech_results),
                  "errors": len(result.errors),
              })
        if verbose:
            ts_total = sum(len(e.content_timestamps) for s in result.diagnostic_slots for e in s.diagnostic_logs)
            print(f"    提取时间戳: {ts_total} 条")
            for slot in result.diagnostic_slots:
                periods = len(slot.active_periods)
                if periods:
                    print(f"    {slot.name}: {periods} 个 ActivePeriod, 角色={slot.role.value}")

        # Step 5: 落盘
        for mech_result in result.mech_results:
            _safe(f"[5/6] 落盘 {mech_result.module_name}",
                  lambda mr=mech_result: log_parser.write_output(mr, output_dir / task_id),
                  "pipeline.write_output",
                  lambda _r, mr=mech_result: {
                      "module": mr.module_key or mr.module_name,
                      "slots": len(mr.slots),
                  })
            if verbose:
                total = sum(
                    cp.total_count
                    for s in mech_result.slots
                    for c in s.board_cycles
                    for cp in (
                        list(c.processes)
                        + [
                            cpu_proc
                            for cpu_cycle in c.cpu_cycles
                            for cpu_proc in cpu_cycle.processes
                        ]
                    )
                )
                diag = mech_result.diag_entry_count
                journal_count = mech_result.journal_entry_count
                match_mark = "" if diag + journal_count == total else " [!条数不一致]"
                print(f"    [{mech_result.module_name}] 诊断:{diag} + journal:{journal_count} = {diag + journal_count} -> 输出:{total}{match_mark}")

        # Step 6: 元数据
        if self.pipeline_config.get("generate_metadata", True):
            meta_path = _safe("[6/6] 元数据生成",
                  lambda: self.metadata_gen.generate(result, output_dir / task_id),
                  "pipeline.metadata",
                  lambda r: {"path": r})
            if verbose and meta_path:
                print(f"    元数据: {meta_path}")

        cleanup_t0 = time.time()
        cleanup_count = self._cleanup_intermediate_files(extract_dir)
        self.performance.record_stage(
            "pipeline.cleanup",
            elapsed_seconds=time.time() - cleanup_t0,
            files=cleanup_count,
        )
        if verbose and cleanup_count:
            print(f"    清理中间文件: {cleanup_count} 项")

        if profile:
            self.performance.write(output_dir / task_id)

        return result

    def _load_plugins(
        self, product: str,
    ) -> tuple[DirectoryDiscoveryPlugin, LogParserPlugin]:
        """加载指定产品的插件对（缓存）。"""
        if product in self._plugin_cache:
            return self._plugin_cache[product]

        products = self.config.get("products", {})
        prod_cfg = products.get(product, {})
        if not prod_cfg:
            raise ValueError(f"未找到产品配置: {product}")

        discovery_cfg = prod_cfg.get("discovery", {})
        parser_cfg = prod_cfg.get("log_parser", {})

        discovery = instantiate_plugin(
            discovery_cfg["plugin"],
            DirectoryDiscoveryPlugin,
            discovery_cfg.get("config", {}),
            decompressor=self.decompressor,
        )
        log_parser = instantiate_plugin(
            parser_cfg["plugin"],
            LogParserPlugin,
            parser_cfg.get("config", {}),
        )

        self._plugin_cache[product] = (discovery, log_parser)
        return discovery, log_parser

    def _extract_all(
        self,
        source: Path,
        extract_dir: Path,
        *,
        recursive: bool,
        expand_gz: bool,
        workers: int,
    ) -> list[str]:
        try:
            return self.decompressor.extract_all(
                source,
                extract_dir,
                recursive=recursive,
                expand_gz=expand_gz,
                workers=workers,
            )
        except TypeError as exc:
            if "workers" not in str(exc):
                raise
            return self.decompressor.extract_all(
                source,
                extract_dir,
                recursive=recursive,
                expand_gz=expand_gz,
            )

    def _cleanup_intermediate_files(self, extract_dir: Path) -> int:
        if self.pipeline_config.get("cleanup_extracted", False):
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
                return 1
            return 0

        if self.pipeline_config.get("cleanup_inner_archives", False):
            return self._cleanup_inner_archives(extract_dir)

        return 0

    def _cleanup_inner_archives(self, extract_dir: Path) -> int:
        if not extract_dir.exists():
            return 0

        removed = 0
        for path in sorted(extract_dir.rglob("*")):
            if not path.is_file():
                continue
            if not self._is_cleanup_archive(path.name):
                continue
            if not (path.parent / f"{path.name}_extracted").is_dir():
                continue
            path.unlink()
            removed += 1
        return removed

    def _is_cleanup_archive(self, name: str) -> bool:
        lower = name.lower()
        if lower.endswith(".gz") and not (
            lower.endswith(".tar.gz") or lower.endswith(".tgz")
        ):
            return False
        return self.decompressor.is_compressed(name)
