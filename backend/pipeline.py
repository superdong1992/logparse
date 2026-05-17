""通用日志解析管道：编排产品无关的步骤，产品特定逻辑由插件处理。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.decompressor import Decompressor
from backend.metadata import MetadataGenerator
from backend.models import LogEntry, ParseResult, PrivateSlotInfo, SlotInfo
from backend.plugins.base import (
    DirectoryDiscoveryPlugin,
    LogParserPlugin,
)
from backend.plugins.loader import instantiate_plugin

logger = logging.getLogger(__name__)


class Pipeline:
    """产品无关日志解析编排器。

    步骤:
      1. Decompress        — 解压外层压缩包（通用）
      2. Discovery         — 发现 slot 和文件（产品插件）
      3. Inner Extraction  — 解压 LogEntry 内层压缩包（通用）
      4. Parse             — 解析日志内容（产品插件）
      5. Write Output      — 落盘（产品插件）
      6. Metadata          — 生成 metadata.json（通用）
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.pipeline_config = config.get("pipeline", {})
        self.decompressor = Decompressor(
            compressed_extensions=config.get("compressed_extensions"),
        )
        self.metadata_gen = MetadataGenerator()
        self._plugin_cache: dict[str, tuple[DirectoryDiscoveryPlugin, LogParserPlugin]] = {}

    def run(
        self,
        source: Path,
        output_dir: Path,
        product: str = "default",
        task_id: str | None = None,
    ) -> ParseResult:
        """运行完整解析管道。"""
        task_id = task_id or source.stem
        extract_dir = output_dir / task_id / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)

        errors: list[str] = []

        def _safe(message: str, fn):
            try:
                return fn()
            except Exception as e:
                errors.append(f"{message}: {e}")
                logger.warning("%s: %s", message, e)
                return None

        # Step 1: 解压
        _safe("解压外层包",
              lambda: self.decompressor.extract_all(
                  source, extract_dir, recursive=self.pipeline_config.get("recursive", True),
              ))

        # Step 2: 目录发现
        discovery, log_parser = self._load_plugins(product)
        diag_slots, private_slots = _safe("目录发现",
                                           lambda: discovery.discover(extract_dir)) or ([], [])

        result = ParseResult(
            task_id=task_id,
            package_name=source.name,
            extracted_root=str(extract_dir),
            diagnostic_slots=diag_slots,
            private_slots=private_slots,
            errors=errors,
        )

        # Step 3: 内层解压
        if self.pipeline_config.get("inner_extraction", True):
            _safe("内层解压",
                  lambda: self._extract_inner_contents(result, output_dir / task_id))

        # Step 4: 日志解析
        _safe("日志解析", lambda: log_parser.parse(result))

        # Step 5: 落盘
        for mech_result in result.mech_results:
            _safe(f"落盘 {mech_result.module_name}",
                  lambda mr=mech_result: log_parser.write_output(mr, output_dir / task_id))

        # Step 6: 元数据
        if self.pipeline_config.get("generate_metadata", True):
            _safe("元数据生成",
                  lambda: self.metadata_gen.generate(result, output_dir / task_id))

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

    def _extract_inner_contents(
        self, result: ParseResult, task_output_dir: Path,
    ) -> None:
        """解压所有诊断日志的内层压缩包，设置 LogEntry.extracted_path。"""
        contents_dir = task_output_dir / "contents"
        for slot in result.diagnostic_slots:
            for entry in slot.diagnostic_logs:
                if not entry.compressed:
                    continue
                src = Path(entry.path)
                if not src.exists():
                    continue
                dest = contents_dir / slot.name / src.stem
                try:
                    self.decompressor.extract_all(src, dest, recursive=False)
                    entry.extracted_path = str(dest)
                except Exception as e:
                    logger.warning("内层解压失败 %s: %s", src, e)
