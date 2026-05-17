from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class PackageConfig(BaseModel):
    outer_name_prefix: str = ""
    diagnostic_dir: str = "diag"
    private_dir: str = "varlog"


class BoardConfig(BaseModel):
    dir_pattern: str = "slot_*"
    role_detection: str = "by_log_content"


class DiagnosticFilesConfig(BaseModel):
    patterns: list[str] = Field(default_factory=lambda: ["diag.zip", "diaglog_*.log.zip", "diaglog_*.zip"])
    filename_timestamp_regex: str = r".*_(\d{14})\..*"


class LogContentConfig(BaseModel):
    timestamp_regex: str = r"(\d{4}-\d{1,2}-\d{1,2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2})?"
    active_period_gap_threshold: int = 300


class JournalFilesConfig(BaseModel):
    patterns: list[str] = Field(default_factory=lambda: ["journal.log", "journal.log.*.gz"])
    sequence_regex: str = r"journal\.log(?:\.(\d+))?(?:\.gz)?"


class PrivateLogsConfig(BaseModel):
    dir_patterns: list[str] = Field(default_factory=lambda: ["slot_*", "slot_*_cpu_*"])
    archive_name: str = "varlog.zip"
    journal_files: JournalFilesConfig = Field(default_factory=JournalFilesConfig)


class MechModuleJournalConfig(BaseModel):
    line_pattern: str = r'^\S+\s+\S+\s+\S+?:\s+\[slotId\s*=\s*\d+,\s*cpuId\s*=\s*\d+,\s*processName\s*=\s*(\S+?)-(\d+)\]:\s+No\[(\d+)\](.+)$'
    line_pattern2: str = ""
    identifying_keyword: str = ""


class MechanismModuleConfig(BaseModel):
    module_name: str = ""  # 日志中实际出现的模块名（全大写），用于 Stage 1 预过滤
    enabled: bool = True
    diag_pattern: str = ""
    active_master_keyword: str = ""
    board_restart_indicator: str = ""
    process_name_mapping: dict[str, str] = Field(default_factory=dict)
    journal: MechModuleJournalConfig = Field(default_factory=MechModuleJournalConfig)
    sequence_pattern: str = r'No\[(\d+)\]'


class OutputConfig(BaseModel):
    base_dir: str = "./output"
    keep_original: bool = True
    generate_metadata: bool = True


class AppConfig(BaseModel):
    output: OutputConfig = Field(default_factory=OutputConfig)
    package: PackageConfig = Field(default_factory=PackageConfig)
    boards: dict[str, BoardConfig] = Field(default_factory=dict)
    diagnostic_files: DiagnosticFilesConfig = Field(default_factory=DiagnosticFilesConfig)
    log_content: LogContentConfig = Field(default_factory=LogContentConfig)
    private_logs: PrivateLogsConfig = Field(default_factory=PrivateLogsConfig)
    mechanism_modules: dict[str, MechanismModuleConfig] = Field(default_factory=dict)
    compressed_extensions: list[str] = Field(
        default_factory=lambda: [".gz", ".zip", ".tar.gz", ".tgz", ".tar"]
    )


def glob_to_regex(pattern: str) -> re.Pattern:
    regex = re.escape(pattern)
    regex = regex.replace(r"\*", ".*")
    regex = regex.replace(r"\?", ".")
    return re.compile(f"^{regex}$", re.IGNORECASE)


class ConfigLoader:
    """配置加载器，维护已编译的模式缓存。"""

    def __init__(self, config_path: str | Path = "config.yaml"):
        self.config_path = Path(config_path)
        self._config: Optional[AppConfig] = None
        self._slot_pattern: Optional[re.Pattern] = None
        self._diag_file_patterns: Optional[list[re.Pattern]] = None
        self._filename_ts_regex: Optional[re.Pattern] = None
        self._content_ts_regex: Optional[re.Pattern] = None
        self._private_dir_patterns: Optional[list[re.Pattern]] = None
        self._journal_file_patterns: Optional[list[re.Pattern]] = None
        self._journal_seq_regex: Optional[re.Pattern] = None

    def load(self) -> AppConfig:
        if self._config is not None:
            return self._config

        if self.config_path.exists():
            raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        else:
            raw = {}

        self._config = AppConfig(**raw)
        self._compile_patterns()
        return self._config

    def _compile_patterns(self) -> None:
        config = self._config
        if config is None:
            return

        main_ctrl = config.boards.get("main_control")
        if main_ctrl:
            self._slot_pattern = glob_to_regex(main_ctrl.dir_pattern)

        self._diag_file_patterns = [
            glob_to_regex(p) for p in config.diagnostic_files.patterns
        ]

        self._filename_ts_regex = re.compile(config.diagnostic_files.filename_timestamp_regex)

        self._content_ts_regex = re.compile(config.log_content.timestamp_regex)

        self._private_dir_patterns = [
            glob_to_regex(p) for p in config.private_logs.dir_patterns
        ]

        self._journal_file_patterns = [
            glob_to_regex(p) for p in config.private_logs.journal_files.patterns
        ]

        self._journal_seq_regex = re.compile(
            config.private_logs.journal_files.sequence_regex, re.IGNORECASE
        )

    def get_config(self) -> AppConfig:
        if self._config is None:
            self.load()
        assert self._config is not None, "ConfigLoader.load() failed"
        return self._config

    @property
    def slot_pattern(self) -> re.Pattern | None:
        if self._slot_pattern is None:
            self.load()
        return self._slot_pattern

    @property
    def diag_file_patterns(self) -> list[re.Pattern]:
        if self._diag_file_patterns is None:
            self.load()
        return self._diag_file_patterns  # type: ignore[return-value]

    @property
    def content_timestamp_regex(self) -> re.Pattern:
        if self._content_ts_regex is None:
            self.load()
        return self._content_ts_regex  # type: ignore[return-value]

    @property
    def gap_threshold_seconds(self) -> int:
        return self.get_config().log_content.active_period_gap_threshold

    @property
    def private_dir_patterns(self) -> list[re.Pattern]:
        if self._private_dir_patterns is None:
            self.load()
        return self._private_dir_patterns  # type: ignore[return-value]

    @property
    def journal_file_patterns(self) -> list[re.Pattern]:
        if self._journal_file_patterns is None:
            self.load()
        return self._journal_file_patterns  # type: ignore[return-value]

    @property
    def journal_seq_regex(self) -> re.Pattern:
        if self._journal_seq_regex is None:
            self.load()
        return self._journal_seq_regex  # type: ignore[return-value]

    def get_archive_name(self) -> str:
        return self.get_config().private_logs.archive_name

    def is_slot_dir(self, name: str) -> bool:
        if self.slot_pattern is None:
            return False
        return bool(self.slot_pattern.match(name))

    def extract_slot_id(self, name: str) -> str:
        match = re.match(r"slot_(.+)", name, re.IGNORECASE)
        return match.group(1) if match else name

    def match_diag_file(self, name: str) -> bool:
        for pat in self.diag_file_patterns:
            if pat.match(name):
                return True
        return False

    def is_private_slot_dir(self, name: str) -> bool:
        """判断目录名是否匹配 varlog/ 下的 slot 或 slot_cpu 模式。"""
        for pat in self.private_dir_patterns:
            if pat.match(name):
                return True
        return False

    def extract_private_slot_info(self, dir_name: str) -> tuple[str, str | None]:
        """从目录名提取 slot_id 和可选的 cpu_id。
        'slot_1' → ('1', None)
        'slot_1_cpu_0' → ('1', '0')
        """
        # 先尝试匹配 slot_X_cpu_Y
        match = re.match(r"slot_(.+?)_cpu_(.+)", dir_name, re.IGNORECASE)
        if match:
            return match.group(1), match.group(2)
        # 再匹配 slot_X
        match = re.match(r"slot_(.+)", dir_name, re.IGNORECASE)
        if match:
            return match.group(1), None
        return dir_name, None

    def match_journal_file(self, name: str) -> bool:
        """判断文件名是否匹配 journal 日志模式。"""
        for pat in self.journal_file_patterns:
            if pat.match(name):
                return True
        return False

    def extract_journal_sequence(self, filename: str) -> int:
        """从 journal 文件名提取序号。0=当前日志，N=历史轮转。"""
        match = self.journal_seq_regex.match(filename)
        if not match:
            return 0
        seq_str = match.group(1)
        try:
            return int(seq_str) if seq_str else 0
        except ValueError:
            return 0

    def extract_dump_time(self, filename: str) -> Optional[datetime]:
        """从文件名提取转储时间戳（仅供参考）。"""
        if self._filename_ts_regex is None:
            self.load()
        match = self._filename_ts_regex.match(filename)  # type: ignore[union-attr]
        if not match:
            return None
        ts_str = match.group(1)
        try:
            return datetime.strptime(ts_str, "%Y%m%d%H%M%S")
        except ValueError:
            return None

    def extract_content_timestamps(self, text: str) -> list[datetime]:
        """从日志内容文本中提取所有真实时间戳（含可选时区）。"""
        stamps: list[datetime] = []
        for m in self.content_timestamp_regex.finditer(text):
            ts_str = m.group(1)
            tz_str = m.group(2)
            if tz_str:
                ts_str = ts_str + tz_str
            try:
                stamps.append(datetime.fromisoformat(ts_str))
            except ValueError:
                continue
        return stamps

    def is_compressed(self, name: str) -> bool:
        config = self.get_config()
        name_lower = name.lower()
        for ext in config.compressed_extensions:
            if name_lower.endswith(ext):
                return True
        return False

    def is_diag_dir(self, dir_name: str) -> bool:
        config = self.get_config()
        return dir_name.lower() == config.package.diagnostic_dir.lower()

    def is_private_dir(self, dir_name: str) -> bool:
        config = self.get_config()
        return dir_name.lower() == config.package.private_dir.lower()

    def get_mech_module_config(self, name: str) -> MechanismModuleConfig | None:
        """获取指定机制模块的配置，不存在返回 None。"""
        modules = self.get_config().mechanism_modules
        return modules.get(name)
