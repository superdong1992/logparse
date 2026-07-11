"""插件基类：定义目录发现和日志解析两套接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

class DirectoryDiscoveryPlugin(ABC):
    """Compatibility interface for product-specific workspace discovery.

    约定：
      - 输入: extracted_root (Path) — 外层压缩包已完整解压后的根目录
      - 输出: two opaque product-owned discovery collections
      - 负责：识别产品范围、匹配诊断日志、提取私有日志来源
      - 不负责：解压归档包；归档解压由 Decompressor 的统一解压阶段完成
    """

    def __init__(self, config: dict[str, Any], decompressor: Any = None):
        self.config = config
        self.decompressor = decompressor

    @classmethod
    def validate_config(cls, product_name: str, config: dict[str, Any]) -> list[str]:
        return []

    @abstractmethod
    def discover(
        self, extracted_root: Path,
    ) -> tuple[list[Any], list[Any]]:
        """扫描 extracted_root，返回产品扩展拥有的发现结果。"""
        ...


class LogParserPlugin(ABC):
    """Compatibility interface for a product-owned parser adapter."""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    @abstractmethod
    def parse(self, state: Any) -> Any:
        """Parse and return the opaque product state."""
        ...

    @abstractmethod
    def write_output(
        self, mechanism_result: Any, output_dir: Path,
    ) -> Path:
        """将一个模块的解析结果落盘。返回写入的目录路径。"""
        ...
