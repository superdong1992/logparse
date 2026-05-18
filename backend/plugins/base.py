"""插件基类：定义目录发现和日志解析两套接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from backend.models import (
    MechResult,
    ParseResult,
    PrivateSlotInfo,
    SlotInfo,
)


class DirectoryDiscoveryPlugin(ABC):
    """产品特定的目录结构发现。

    约定：
      - 输入: extracted_root (Path) — 外层压缩包已完整解压后的根目录
      - 输出: (list[SlotInfo], list[PrivateSlotInfo])
      - 负责：找到 slot 目录、匹配诊断日志文件、提取私有/journal 日志文件
      - 对于嵌套压缩包（如 varlog.zip），可用 self.decompressor 解压后再扫描
      - 设置 LogEntry.compressed=True 和 LogEntry.path 供管道做内层解压
    """

    def __init__(self, config: dict[str, Any], decompressor: Any = None):
        self.config = config
        self.decompressor = decompressor

    @abstractmethod
    def discover(
        self, extracted_root: Path,
    ) -> tuple[list[SlotInfo], list[PrivateSlotInfo]]:
        """扫描 extracted_root，返回发现的 slot 信息。"""
        ...


class LogParserPlugin(ABC):
    """产品特定的日志内容解析。

    约定：
      - 输入: ParseResult（diagnostic_slots + private_slots 已填充，内层解压已完成）
      - 输出: ParseResult（原地修改：填充 content_timestamps、active_periods、
              mech_results、BoardRole）
      - 负责：读取日志文件内容、提取时间戳、构建 ActivePeriod、
              机制模块正则解析、构建重启周期、判定板卡角色
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config

    @abstractmethod
    def parse(self, result: ParseResult) -> ParseResult:
        """解析所有日志内容，填充 ParseResult。

        后置条件：
          - 所有 LogEntry.content_timestamps 已填充
          - 所有 SlotInfo.active_periods 已构建
          - result.mech_results 已填充
          - 所有 SlotInfo.role 已设置
          - result.errors 包含非致命错误
        """
        ...

    @abstractmethod
    def write_output(
        self, mech_result: MechResult, output_dir: Path,
    ) -> Path:
        """将一个模块的解析结果落盘。返回写入的目录路径。"""
        ...
