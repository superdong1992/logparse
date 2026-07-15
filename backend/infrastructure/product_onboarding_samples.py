"""Safe bounded readers for explicitly supplied onboarding samples."""

from __future__ import annotations

import codecs
import gzip
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence, TextIO

from backend.contracts.product_onboarding import (
    OnboardingInputError,
    SampleBatch,
    SampleFile,
    SamplingLimits,
)


_ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".zip", ".tar", ".7z", ".rar")


@dataclass(frozen=True, slots=True)
class _PreparedFile:
    file_id: str
    path: Path = field(repr=False)
    name: str
    size_bytes: int
    compressed: bool


class SecureSampleReader:
    """Read only named regular files under deterministic sampling budgets."""

    def read(
        self,
        input_files: Sequence[str | Path],
        *,
        encoding: str,
        limits: SamplingLimits,
    ) -> SampleBatch:
        codec_name = _text_codec_name(encoding)
        prepared = _prepare_inputs(input_files, limits)
        remaining = limits.max_total_characters
        samples: list[SampleFile] = []

        for index, item in enumerate(prepared):
            files_remaining = len(prepared) - index
            file_budget = remaining // files_remaining
            if file_budget <= 0:
                samples.append(_sample_file(item, (), 0, 0, truncated=True, binary=False))
                continue
            try:
                with _open_text(item, codec_name) as stream:
                    values = _read_bounded_lines(stream, limits, file_budget)
            except (OSError, EOFError, UnicodeError):
                raise OnboardingInputError(
                    "LP_ONBOARD_INPUT_UNREADABLE",
                    "an input file could not be read as bounded text",
                ) from None
            lines, character_count, replacement_count, truncated, binary = values
            remaining -= character_count
            samples.append(
                _sample_file(
                    item,
                    lines,
                    character_count,
                    replacement_count,
                    truncated=truncated,
                    binary=binary,
                )
            )

        return SampleBatch(encoding=codec_name, files=tuple(samples))


def _text_codec_name(encoding: str) -> str:
    try:
        codec = codecs.lookup(encoding)
    except (LookupError, TypeError):
        raise OnboardingInputError(
            "LP_ONBOARD_INPUT_ENCODING_INVALID",
            "encoding is not recognized",
        ) from None
    if not bool(getattr(codec, "_is_text_encoding", False)):
        raise OnboardingInputError(
            "LP_ONBOARD_INPUT_ENCODING_INVALID",
            "encoding is not a text codec",
        )
    return codec.name


def _prepare_inputs(
    input_files: Sequence[str | Path],
    limits: SamplingLimits,
) -> tuple[_PreparedFile, ...]:
    if isinstance(input_files, (str, bytes, Path)):
        raise OnboardingInputError(
            "LP_ONBOARD_INPUT_SEQUENCE_REQUIRED",
            "inputs must be an explicit file sequence",
        )
    if not input_files:
        raise OnboardingInputError(
            "LP_ONBOARD_INPUT_REQUIRED",
            "at least one explicit regular file is required",
        )
    if len(input_files) > limits.max_files:
        raise OnboardingInputError(
            "LP_ONBOARD_INPUT_FILE_LIMIT",
            "input file count exceeds the sampling limit",
        )

    pending: list[tuple[Path, str, int, bool]] = []
    seen_paths: set[Path] = set()
    seen_names: set[str] = set()
    for raw_path in input_files:
        try:
            path = Path(raw_path)
        except (TypeError, ValueError):
            raise OnboardingInputError(
                "LP_ONBOARD_INPUT_PATH_INVALID",
                "an input path is invalid",
            ) from None
        try:
            if path.is_symlink():
                raise OnboardingInputError(
                    "LP_ONBOARD_INPUT_SYMLINK_REJECTED",
                    "symbolic-link inputs are not accepted",
                )
            if not path.is_file():
                raise OnboardingInputError(
                    "LP_ONBOARD_INPUT_NOT_REGULAR",
                    "every input must be a regular file",
                )
            lower_name = path.name.casefold()
            if any(lower_name.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES):
                raise OnboardingInputError(
                    "LP_ONBOARD_INPUT_ARCHIVE_REJECTED",
                    "archive containers are not accepted as samples",
                )
            resolved = path.resolve(strict=True)
            size_bytes = resolved.stat().st_size
        except OnboardingInputError:
            raise
        except OSError:
            raise OnboardingInputError(
                "LP_ONBOARD_INPUT_METADATA_UNAVAILABLE",
                "input metadata is unavailable",
            ) from None

        if resolved in seen_paths:
            raise OnboardingInputError(
                "LP_ONBOARD_INPUT_DUPLICATE",
                "duplicate input paths are not accepted",
            )
        if lower_name in seen_names:
            raise OnboardingInputError(
                "LP_ONBOARD_INPUT_BASENAME_CONFLICT",
                "case-insensitive duplicate basenames are not accepted",
            )
        seen_paths.add(resolved)
        seen_names.add(lower_name)
        pending.append((resolved, path.name, size_bytes, lower_name.endswith(".gz")))

    pending.sort(key=lambda item: (item[1].casefold(), item[1], str(item[0])))
    return tuple(
        _PreparedFile(
            file_id=f"file_{index:03d}",
            path=path,
            name=name,
            size_bytes=size_bytes,
            compressed=compressed,
        )
        for index, (path, name, size_bytes, compressed) in enumerate(pending, 1)
    )


def _open_text(item: _PreparedFile, encoding: str) -> TextIO:
    if item.compressed:
        return gzip.open(item.path, "rt", encoding=encoding, errors="replace")
    return item.path.open("r", encoding=encoding, errors="replace")


def _read_bounded_lines(
    stream: TextIO,
    limits: SamplingLimits,
    remaining_characters: int,
) -> tuple[tuple[str, ...], int, int, bool, bool]:
    lines: list[str] = []
    character_count = 0
    replacement_count = 0
    truncated = False
    binary_likely = False

    while len(lines) < limits.max_lines_per_file and remaining_characters > 0:
        raw_line = stream.readline(limits.max_characters_per_line + 1)
        if raw_line == "":
            break
        line_too_long = len(raw_line) > limits.max_characters_per_line and not raw_line.endswith(
            ("\n", "\r")
        )
        line = raw_line[: limits.max_characters_per_line].rstrip("\r\n")
        if len(line) > remaining_characters:
            line = line[:remaining_characters]
            truncated = True
        lines.append(line)
        character_count += len(line)
        remaining_characters -= len(line)
        replacement_count += line.count("\ufffd")
        binary_likely = binary_likely or "\x00" in line
        if line_too_long or remaining_characters <= 0:
            truncated = True
            break

    if len(lines) >= limits.max_lines_per_file and stream.read(1):
        truncated = True
    return (
        tuple(lines),
        character_count,
        replacement_count,
        truncated,
        binary_likely,
    )


def _sample_file(
    item: _PreparedFile,
    lines: tuple[str, ...],
    character_count: int,
    replacement_count: int,
    *,
    truncated: bool,
    binary: bool,
) -> SampleFile:
    return SampleFile(
        file_id=item.file_id,
        name=item.name,
        size_bytes=item.size_bytes,
        compressed=item.compressed,
        sampled_lines=len(lines),
        nonempty_lines=sum(1 for line in lines if line.strip()),
        sampled_characters=character_count,
        replacement_characters=replacement_count,
        truncated=truncated,
        binary_likely=binary,
        lines=lines,
    )
