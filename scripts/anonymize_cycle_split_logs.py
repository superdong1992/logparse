#!/usr/bin/env python3
"""Anonymize lifecycle split log snippets."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import sys


TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:[\.,]\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?"
)

PID_PROCESS_TOKEN_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_.:/-]*?)-(?P<pid>\d+)@(?P<cpu>[A-Za-z0-9_.:/-]+)"
)
AT_PROCESS_TOKEN_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_.:/-]*)@(?P<cpu>[A-Za-z0-9_.:/-]+)"
)
PROCESS_FIELD_RE = re.compile(
    r"(?P<prefix>\b(?:ProcessName|process|proc)=)(?P<value>[^\s;,]+)"
)
PID_SUFFIX_RE = re.compile(r"^(?P<name>.+)-(?P<pid>\d+)$")

FIELD_SHORT_NAMES = {
    "ProcessName": "pn",
    "process": "pr",
    "proc": "pc",
    "module": "m",
    "slot": "s",
    "split": "sp",
    "adjusted": "ad",
    "window": "w",
    "same_pid_conflicts": "sc",
    "protected_boundaries": "pb",
    "protected_gap": "pg",
    "role": "r",
    "old_pids": "op",
    "old_end": "oe",
    "new_pid": "np",
    "new_start": "ns",
    "before": "b",
    "after": "a",
    "last": "l",
    "reason": "rs",
}

VALUE_SHORT_NAMES = {
    "adjusted_backward": "ab",
    "kept": "k",
    "board": "b",
    "indicator": "i",
    "no_safe_gap_candidate": "nsg",
}

SHORTENABLE_FIELD_RE = re.compile(
    r"\b("
    + "|".join(re.escape(name) for name in sorted(FIELD_SHORT_NAMES, key=len, reverse=True))
    + r")="
)
MODULE_VALUE_RE = re.compile(r"(?<=\bm=)module(?P<number>\d+)\b")
WORD_VALUE_RE = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in sorted(VALUE_SHORT_NAMES, key=len, reverse=True)) + r")\b"
)


def _parse_timestamp(value: str) -> datetime | None:
    normalized = value.replace(" ", "T").replace(",", ".")
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    if re.search(r"[+-]\d{4}$", normalized):
        normalized = f"{normalized[:-2]}:{normalized[-2:]}"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def collect_timestamps(text: str) -> dict[str, str]:
    found: dict[str, datetime] = {}
    for match in TIMESTAMP_RE.finditer(text):
        raw = match.group(0)
        parsed = _parse_timestamp(raw)
        if parsed is not None:
            found[raw] = parsed

    ordered = sorted(found, key=lambda item: (found[item], item))
    return {raw: f"t{index}" for index, raw in enumerate(ordered, start=1)}


def replace_timestamps(text: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return text

    def replace(match: re.Match[str]) -> str:
        return mapping.get(match.group(0), match.group(0))

    return TIMESTAMP_RE.sub(replace, text)


def _add_process(mapping: dict[str, str], raw_name: str) -> None:
    if raw_name and raw_name not in mapping:
        mapping[raw_name] = f"p{len(mapping) + 1}"


def _field_process_name(value: str) -> str:
    match = PID_SUFFIX_RE.match(value)
    if match:
        return match.group("name")
    return value


def collect_process_names(text: str) -> dict[str, str]:
    candidates: list[tuple[int, str]] = []

    for match in PID_PROCESS_TOKEN_RE.finditer(text):
        candidates.append((match.start("name"), match.group("name")))

    for match in PROCESS_FIELD_RE.finditer(text):
        candidates.append((match.start("value"), _field_process_name(match.group("value"))))

    for match in AT_PROCESS_TOKEN_RE.finditer(text):
        previous = text[max(0, match.start() - 1):match.start()]
        if previous == "-" or PID_SUFFIX_RE.match(match.group("name")):
            continue
        candidates.append((match.start("name"), match.group("name")))

    mapping: dict[str, str] = {}
    for _position, name in sorted(candidates, key=lambda item: item[0]):
        _add_process(mapping, name)
    return mapping


def replace_process_names(text: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return text

    def replace_pid_token(match: re.Match[str]) -> str:
        name = mapping.get(match.group("name"), match.group("name"))
        return f"{name}-{match.group('pid')}@{match.group('cpu')}"

    def replace_at_token(match: re.Match[str]) -> str:
        previous = text[max(0, match.start() - 1):match.start()]
        if previous == "-" or PID_SUFFIX_RE.match(match.group("name")):
            return match.group(0)
        name = mapping.get(match.group("name"), match.group("name"))
        return f"{name}@{match.group('cpu')}"

    def replace_field(match: re.Match[str]) -> str:
        value = match.group("value")
        suffix_match = PID_SUFFIX_RE.match(value)
        if suffix_match:
            name = mapping.get(suffix_match.group("name"), suffix_match.group("name"))
            return f"{match.group('prefix')}{name}-{suffix_match.group('pid')}"
        return f"{match.group('prefix')}{mapping.get(value, value)}"

    text = PID_PROCESS_TOKEN_RE.sub(replace_pid_token, text)
    text = PROCESS_FIELD_RE.sub(replace_field, text)
    return AT_PROCESS_TOKEN_RE.sub(replace_at_token, text)


def shorten_diagnostic_tokens(text: str) -> str:
    text = text.replace("unsafe cycle split", "ucs")
    text = SHORTENABLE_FIELD_RE.sub(lambda match: f"{FIELD_SHORT_NAMES[match.group(1)]}=", text)
    text = MODULE_VALUE_RE.sub(lambda match: f"m{match.group('number')}", text)
    return WORD_VALUE_RE.sub(lambda match: VALUE_SHORT_NAMES[match.group(1)], text)


def sanitize_text(text: str) -> str:
    timestamp_mapping = collect_timestamps(text)
    text = replace_timestamps(text, timestamp_mapping)
    process_mapping = collect_process_names(text)
    text = replace_process_names(text, process_mapping)
    return shorten_diagnostic_tokens(text)


def _read_input(path_arg: str) -> str:
    if path_arg == "-":
        return sys.stdin.read()
    return Path(path_arg).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Anonymize unsafe cycle split lifecycle log snippets.",
    )
    parser.add_argument("input", help="Input log path, or '-' to read stdin.")
    parser.add_argument("-o", "--output", help="Write sanitized text to this path.")
    args = parser.parse_args(argv)

    sanitized = sanitize_text(_read_input(args.input))
    if args.output:
        Path(args.output).write_text(sanitized, encoding="utf-8")
    else:
        sys.stdout.write(sanitized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
