from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class BoardType(str, Enum):
    MAIN_CONTROL = "main_control"
    INTERFACE = "interface"


class BoardRole(str, Enum):
    ACTIVE = "active"
    STANDBY = "standby"
    UNKNOWN = "unknown"


class LogType(str, Enum):
    DIAGNOSTIC = "diagnostic"
    PRIVATE = "private"


class ActivePeriod(BaseModel):
    """一个连续的主控时段段。"""
    start: datetime
    end: datetime

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


class LogEntry(BaseModel):
    """单个日志文件的描述。"""
    path: str
    name: str
    size_bytes: int = 0
    compressed: bool = False
    original_format: str = ""
    extracted_path: str = ""
    dump_time: Optional[datetime] = None
    content_timestamps: list[datetime] = Field(default_factory=list)


class SlotInfo(BaseModel):
    """槽位（板卡）信息。"""
    slot_id: str
    name: str
    board_type: BoardType = BoardType.MAIN_CONTROL
    role: BoardRole = BoardRole.UNKNOWN
    path: str
    diagnostic_logs: list[LogEntry] = Field(default_factory=list)
    active_periods: list[ActivePeriod] = Field(default_factory=list)

    def add_diagnostic_log(self, entry: LogEntry) -> None:
        self.diagnostic_logs.append(entry)

    def add_active_period(self, period: ActivePeriod) -> None:
        self.active_periods.append(period)
        self.active_periods.sort(key=lambda p: p.start)

    @property
    def all_content_timestamps(self) -> list[datetime]:
        stamps: list[datetime] = []
        for log in self.diagnostic_logs:
            stamps.extend(log.content_timestamps)
        return sorted(stamps)


class JournalLogFile(BaseModel):
    """单个 journal 日志文件。"""
    path: str
    name: str
    size_bytes: int = 0
    compressed: bool = False
    sequence: int = 0  # 0=当前日志, N=历史轮转


class PrivateSlotInfo(BaseModel):
    """varlog/ 下的私有日志槽位。"""
    dir_name: str         # "slot_1" 或 "slot_1_cpu_0"
    slot_id: str          # 所属板卡 slot_id, e.g. "1"
    cpu_id: str | None = None  # None/"0" means board-level; non-zero values are CPU subcards.
    path: str
    journal_logs: list[JournalLogFile] = Field(default_factory=list)


class MechLogEntry(BaseModel):
    """单条机制模块日志。"""
    timestamp: datetime | None = None
    source: str = ""                # "diagnostic" | "journal"
    source_file: str = ""           # 来源文件路径，如 "slot_1/diag.zip"
    slot: str = ""
    cpu_id: str = ""
    process_name: str = ""
    pid: str = ""
    context: str = ""
    sequence: int = 0
    is_active_signal: bool = False
    raw: str = ""


class MechProcessLifecycle(BaseModel):
    """同一进程同一 PID 的一次连续生命周期。"""
    process_name: str
    pid: str
    logs: list[MechLogEntry] = Field(default_factory=list)
    total_count: int = 0
    missing_sequences: list[int] = Field(default_factory=list)


class MechCycleSplitTrace(BaseModel):
    """重启周期切分原因追踪。"""
    timestamp: datetime
    reason: str = ""
    cpu_id: str = ""
    indicator: str = ""
    old_pid: str = ""
    new_pid: str = ""
    detail: str = ""


class MechBoundaryIssue(BaseModel):
    """Structured lifecycle boundary diagnostic."""
    kind: str
    severity: str = "warning"
    action: str = ""
    reason: str = ""
    module_key: str = ""
    event_id: str = ""
    scope: str = "board"
    slot: str = ""
    split_time: datetime | None = None
    adjusted_time: datetime | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    old_pid_end: datetime | None = None
    new_pid_start: datetime | None = None
    process_name: str = ""
    pid: str = ""
    direction: str = ""
    log_count: int = 0
    detail: str = ""
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    protected_boundaries: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    suggested_commands: list[str] = Field(default_factory=list)


class MechCpuCycle(BaseModel):
    """CPU-local lifecycle nested inside a board lifecycle."""
    cpu_id: str = ""
    dir_name: str = ""
    start_time: datetime | None = None
    end_time: datetime | None = None
    split_traces: list[MechCycleSplitTrace] = Field(default_factory=list)
    processes: list[MechProcessLifecycle] = Field(default_factory=list)


class MechBoardCycle(BaseModel):
    """一次整板重启周期。"""
    dir_name: str = ""              # "{启动时间}-{恢复时间}"
    start_time: datetime | None = None
    end_time: datetime | None = None
    split_traces: list[MechCycleSplitTrace] = Field(default_factory=list)
    processes: list[MechProcessLifecycle] = Field(default_factory=list)
    cpu_cycles: list[MechCpuCycle] = Field(default_factory=list)


class MechSlotOutput(BaseModel):
    """单个槽位的机制模块日志输出。"""
    slot_id: str
    board_cycles: list[MechBoardCycle] = Field(default_factory=list)
    lifecycle_reliable: bool = True
    boundary_issues: list[MechBoundaryIssue] = Field(default_factory=list)
    lifecycle_split_result: Any | None = None


class MechResult(BaseModel):
    """机制模块解析结果。"""
    module_name: str = ""
    module_key: str = ""
    slots: list[MechSlotOutput] = Field(default_factory=list)
    active_master_slots: list[str] = Field(default_factory=list)
    diag_entry_count: int = 0
    journal_entry_count: int = 0


class ParseResult(BaseModel):
    """完整解析结果"""
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    package_name: str = ""
    diagnostic_slots: list[SlotInfo] = Field(default_factory=list)
    private_slots: list[PrivateSlotInfo] = Field(default_factory=list)
    mech_results: list[MechResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    extracted_root: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskStatus(str, Enum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    SCANNING = "scanning"
    IDENTIFYING = "identifying"
    DONE = "done"
    ERROR = "error"


class TaskInfo(BaseModel):
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    message: str = ""
    result: Optional[ParseResult] = None
