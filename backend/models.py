"""旧模型导入路径的兼容 façade。

产品模型已经迁入绿色 LAN 扩展区。保留本模块是为了兼容既有插件、测试和
局域网配置中的类路径；新的通用架构代码不得在这里新增产品概念。
"""

from backend.extensions.products.current.models import (
    ActivePeriod,
    BoardRole,
    BoardType,
    JournalLogFile,
    LogEntry,
    MechBoardCycle,
    MechCpuCycle,
    MechLogEntry,
    MechProcessLifecycle,
    MechResult,
    MechSlotOutput,
    ParseResult,
    PrivateSlotInfo,
    SlotInfo,
)

__all__ = [
    "ActivePeriod",
    "BoardRole",
    "BoardType",
    "JournalLogFile",
    "LogEntry",
    "MechBoardCycle",
    "MechCpuCycle",
    "MechLogEntry",
    "MechProcessLifecycle",
    "MechResult",
    "MechSlotOutput",
    "ParseResult",
    "PrivateSlotInfo",
    "SlotInfo",
]
