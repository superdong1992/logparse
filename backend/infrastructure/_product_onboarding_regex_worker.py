"""Pure-stdlib isolated regex evaluator returning aggregate counters only."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from typing import Any, Mapping


MAX_SERIALIZED_INPUT_BYTES = 8 * 1024 * 1024
MAX_LINE_CHARACTERS = 4096
MAX_TOTAL_LINE_CHARACTERS = 1024 * 1024
MAX_LINES = 32_768
MAX_ITEMS = 64
PROBE_KINDS = {
    "non_empty",
    "equals",
    "suffix_decimal",
    "base_non_empty_after_decimal_suffix",
}


def evaluate_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate a prevalidated declarative plan without returning text."""

    normalized = _normalize_payload(payload)
    timestamp_re = re.compile(normalized["timestamp_pattern"])
    record_re = re.compile(normalized["record_pattern"])
    ordinal_re = (
        re.compile(normalized["ordinal_pattern"]) if normalized["ordinal_pattern"] else None
    )
    required_groups = normalized["required_groups"]
    probes = normalized["probes"]

    counters = {
        "record_matches": 0,
        "timestamp_parseable": 0,
        "timestamp_runtime_errors": 0,
        "ordinal_matches": 0,
        "ordinal_integers": 0,
    }
    group_counts = {name: 0 for name in required_groups}
    probe_counts = {probe["probe_id"]: 0 for probe in probes}

    for line in normalized["lines"]:
        match = record_re.search(line)
        if match is None:
            continue
        counters["record_matches"] += 1
        parseable, runtime_errors = _timestamp_counts(timestamp_re, line)
        if parseable:
            counters["timestamp_parseable"] += 1
        counters["timestamp_runtime_errors"] += runtime_errors

        values = match.groupdict()
        for name in required_groups:
            value = values.get(name)
            if isinstance(value, str) and value.strip():
                group_counts[name] += 1
        for probe in probes:
            value = values.get(probe["group"])
            if _probe_matches(probe, value):
                probe_counts[probe["probe_id"]] += 1

        if ordinal_re is not None:
            ordinal = ordinal_re.search(line)
            if ordinal is not None:
                counters["ordinal_matches"] += 1
                try:
                    int(ordinal.group(1))
                except (IndexError, TypeError, ValueError):
                    pass
                else:
                    counters["ordinal_integers"] += 1

    return {
        "status": "ok",
        "counters": counters,
        "group_counts": group_counts,
        "probe_counts": probe_counts,
    }


def _normalize_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise ValueError("invalid payload")
    lines = payload.get("lines")
    if (
        not isinstance(lines, list)
        or any(not isinstance(line, str) for line in lines)
        or len(lines) > MAX_LINES
        or any(len(line) > MAX_LINE_CHARACTERS for line in lines)
        or sum(len(line) for line in lines) > MAX_TOTAL_LINE_CHARACTERS
    ):
        raise ValueError("invalid lines")
    patterns: dict[str, str] = {}
    for name in ("timestamp_pattern", "record_pattern"):
        value = payload.get(name)
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise ValueError("invalid pattern")
        patterns[name] = value
    ordinal = payload.get("ordinal_pattern", "")
    if not isinstance(ordinal, str) or len(ordinal) > 4096:
        raise ValueError("invalid ordinal pattern")

    required_groups = payload.get("required_groups")
    if (
        not isinstance(required_groups, list)
        or len(required_groups) > MAX_ITEMS
        or any(not _safe_identifier(item) for item in required_groups)
        or len(set(required_groups)) != len(required_groups)
    ):
        raise ValueError("invalid required groups")
    probes = payload.get("probes")
    if not isinstance(probes, list) or len(probes) > MAX_ITEMS:
        raise ValueError("invalid probes")
    normalized_probes: list[dict[str, str]] = []
    probe_ids: set[str] = set()
    for probe in probes:
        if not isinstance(probe, dict) or set(probe) != {
            "probe_id",
            "kind",
            "group",
            "value",
        }:
            raise ValueError("invalid probe")
        probe_id = probe.get("probe_id")
        kind = probe.get("kind")
        group = probe.get("group")
        value = probe.get("value")
        if (
            not _safe_identifier(probe_id)
            or probe_id in probe_ids
            or kind not in PROBE_KINDS
            or not _safe_identifier(group)
            or group not in required_groups
            or not isinstance(value, str)
            or len(value) > 256
        ):
            raise ValueError("invalid probe")
        probe_ids.add(probe_id)
        normalized_probes.append(dict(probe))
    return {
        "lines": lines,
        **patterns,
        "ordinal_pattern": ordinal,
        "required_groups": list(required_groups),
        "probes": normalized_probes,
    }


def _safe_identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 128
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", value) is not None
    )


def _timestamp_counts(pattern: re.Pattern[str], line: str) -> tuple[int, int]:
    parseable = 0
    runtime_errors = 0
    for match in pattern.finditer(line):
        try:
            timestamp = match.group(1)
            timezone = match.group(2)
        except IndexError:
            runtime_errors += 1
            continue
        if timestamp is None:
            runtime_errors += 1
            continue
        try:
            datetime.fromisoformat(timestamp + (timezone or ""))
        except TypeError:
            runtime_errors += 1
        except ValueError:
            pass
        else:
            parseable += 1
    return parseable, runtime_errors


def _probe_matches(probe: Mapping[str, str], value: Any) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if probe["kind"] == "non_empty":
        return bool(stripped)
    if probe["kind"] == "equals":
        return stripped == probe["value"]
    if probe["kind"] == "suffix_decimal":
        return re.fullmatch(r".+-\d+", stripped) is not None
    if probe["kind"] == "base_non_empty_after_decimal_suffix":
        return bool(re.sub(r"-\d+$", "", stripped))
    return False


def _load_payload() -> Mapping[str, Any]:
    serialized_bytes = sys.stdin.buffer.read(MAX_SERIALIZED_INPUT_BYTES + 1)
    if len(serialized_bytes) > MAX_SERIALIZED_INPUT_BYTES:
        raise ValueError("payload too large")
    serialized = serialized_bytes.decode("utf-8")
    payload = json.loads(serialized)
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    return payload


def main() -> int:
    try:
        result = evaluate_payload(_load_payload())
    except (ValueError, TypeError, re.error, RecursionError, MemoryError):
        result = {"status": "error"}
        code = 2
    else:
        code = 0
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
