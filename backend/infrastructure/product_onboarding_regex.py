"""Static regex checks and isolated bounded evaluation for onboarding."""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from re import _constants as sre_constants
from re import _parser as sre_parser
from typing import Any, Callable, Sequence

from backend.contracts.product_onboarding import (
    CaptureProbe,
    RegexEvaluation,
    RegexEvaluationPlan,
)
from backend.infrastructure._product_onboarding_regex_worker import (
    MAX_LINE_CHARACTERS,
    MAX_LINES,
    MAX_ITEMS,
    MAX_SERIALIZED_INPUT_BYTES,
    MAX_TOTAL_LINE_CHARACTERS,
    PROBE_KINDS,
)


_WORKER_TIMEOUT_SECONDS = 5.0
_MAX_OUTPUT_CHARACTERS = 64 * 1024
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_WORKER_PATH = Path(__file__).with_name("_product_onboarding_regex_worker.py")
_COUNTER_NAMES = {
    "record_matches",
    "timestamp_parseable",
    "timestamp_runtime_errors",
    "ordinal_matches",
    "ordinal_integers",
}


Runner = Callable[..., subprocess.CompletedProcess[str]]


class IsolatedRegexSandbox:
    def __init__(
        self,
        *,
        timeout_seconds: float = _WORKER_TIMEOUT_SECONDS,
        runner: Runner = subprocess.run,
        worker_path: Path = _WORKER_PATH,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > _WORKER_TIMEOUT_SECONDS
        ):
            raise ValueError("timeout_seconds must be within the five-second hard limit")
        self._timeout_seconds = timeout_seconds
        self._runner = runner
        self._worker_path = worker_path

    def evaluate(
        self,
        lines: Sequence[str],
        plan: RegexEvaluationPlan,
    ) -> RegexEvaluation:
        errors = _validate_plan(plan)
        if errors:
            return RegexEvaluation.rejected(*errors)
        line_error = _validate_lines(lines)
        if line_error:
            return RegexEvaluation.rejected(line_error)
        if not lines:
            return RegexEvaluation(
                status="ok",
                counters={name: 0 for name in sorted(_COUNTER_NAMES)},
                group_counts={name: 0 for name in plan.required_groups},
                probe_counts={probe.probe_id: 0 for probe in plan.probes},
            )

        payload = json.dumps(
            {
                "schema_version": 1,
                "lines": list(lines),
                "timestamp_pattern": plan.timestamp_pattern,
                "record_pattern": plan.record_pattern,
                "ordinal_pattern": plan.ordinal_pattern,
                "required_groups": list(plan.required_groups),
                "probes": [
                    {
                        "probe_id": probe.probe_id,
                        "kind": probe.kind,
                        "group": probe.group,
                        "value": probe.value,
                    }
                    for probe in plan.probes
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(payload.encode("utf-8")) > MAX_SERIALIZED_INPUT_BYTES:
            return RegexEvaluation.rejected("regex_execution.input_limit")
        try:
            completed = self._runner(
                [sys.executable, "-I", str(self._worker_path)],
                cwd=_REPOSITORY_ROOT,
                input=payload,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return RegexEvaluation.rejected("regex_execution.timeout")
        except (OSError, subprocess.SubprocessError):
            return RegexEvaluation.rejected("regex_execution.failed")
        if (
            completed.returncode != 0
            or completed.stderr
            or len(completed.stdout) > _MAX_OUTPUT_CHARACTERS
        ):
            return RegexEvaluation.rejected("regex_execution.failed")
        try:
            result = json.loads(completed.stdout)
        except (ValueError, TypeError, RecursionError, MemoryError):
            return RegexEvaluation.rejected("regex_execution.failed")
        return _normalize_result(result, plan)


def _validate_plan(plan: RegexEvaluationPlan) -> tuple[str, ...]:
    errors: list[str] = []
    timestamp_capture_count = _bounded_capture_count(
        plan.timestamp_capture_count,
        "timestamp_pattern",
        errors,
    )
    ordinal_capture_count = _bounded_capture_count(
        plan.ordinal_capture_count,
        "ordinal_pattern",
        errors,
    )
    required_groups = plan.required_groups
    if (
        not isinstance(required_groups, tuple)
        or len(required_groups) > MAX_ITEMS
        or any(not _safe_identifier(name) for name in required_groups)
        or len(set(required_groups)) != len(required_groups)
    ):
        errors.append("record_pattern.invalid_required_groups")
        required_groups = ()
    probes = plan.probes
    if not isinstance(probes, tuple) or len(probes) > MAX_ITEMS:
        errors.append("record_pattern.invalid_probe")
        probes = ()
    _safe_compile(
        plan.timestamp_pattern,
        "timestamp_pattern",
        errors,
        minimum_groups=timestamp_capture_count,
    )
    _safe_compile(
        plan.record_pattern,
        "record_pattern",
        errors,
        required_groups=required_groups,
    )
    if plan.ordinal_pattern:
        _safe_compile(
            plan.ordinal_pattern,
            "ordinal_pattern",
            errors,
            minimum_groups=ordinal_capture_count,
        )
    probe_ids: set[str] = set()
    for probe in probes:
        if (
            not isinstance(probe, CaptureProbe)
            or not _safe_identifier(probe.probe_id)
            or probe.probe_id in probe_ids
            or probe.kind not in PROBE_KINDS
            or probe.group not in required_groups
            or not isinstance(probe.value, str)
            or len(probe.value) > 256
        ):
            errors.append("record_pattern.invalid_probe")
            continue
        probe_ids.add(probe.probe_id)
    return tuple(dict.fromkeys(errors))


def _bounded_capture_count(value: Any, label: str, errors: list[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_ITEMS:
        errors.append(f"{label}.invalid_capture_count")
        return 0
    return value


def _validate_lines(lines: Sequence[str]) -> str | None:
    if isinstance(lines, (str, bytes)) or len(lines) > MAX_LINES:
        return "regex_execution.input_limit"
    total_characters = 0
    for line in lines:
        if not isinstance(line, str) or len(line) > MAX_LINE_CHARACTERS:
            return "regex_execution.input_limit"
        total_characters += len(line)
        if total_characters > MAX_TOTAL_LINE_CHARACTERS:
            return "regex_execution.input_limit"
    return None


def _normalize_result(
    result: Any,
    plan: RegexEvaluationPlan,
) -> RegexEvaluation:
    if not isinstance(result, dict) or result.get("status") != "ok":
        return RegexEvaluation.rejected("regex_execution.failed")
    counters = result.get("counters")
    group_counts = result.get("group_counts")
    probe_counts = result.get("probe_counts")
    if (
        not _valid_counter_map(counters, _COUNTER_NAMES)
        or not _valid_counter_map(group_counts, set(plan.required_groups))
        or not _valid_counter_map(
            probe_counts,
            {probe.probe_id for probe in plan.probes},
        )
    ):
        return RegexEvaluation.rejected("regex_execution.failed")
    return RegexEvaluation(
        status="ok",
        counters=dict(counters),
        group_counts=dict(group_counts),
        probe_counts=dict(probe_counts),
    )


def _valid_counter_map(value: Any, names: set[str]) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == names
        and all(
            not isinstance(item, bool) and isinstance(item, int) and item >= 0
            for item in value.values()
        )
    )


def _safe_identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 128
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", value) is not None
    )


def _safe_compile(
    pattern: Any,
    label: str,
    errors: list[str],
    *,
    minimum_groups: int = 0,
    required_groups: Sequence[str] = (),
) -> re.Pattern[str] | None:
    if not isinstance(pattern, str):
        errors.append(f"{label}.invalid_type")
        return None
    if not pattern:
        errors.append(f"{label}.empty")
        return None
    if len(pattern) > 4096:
        errors.append(f"{label}.unsafe_too_long")
        return None
    if _regex_nesting_too_deep(pattern):
        errors.append(f"{label}.unsafe_too_deep")
        return None
    try:
        parsed = sre_parser.parse(pattern, 0)
    except (re.error, RecursionError, OverflowError, MemoryError):
        errors.append(f"{label}.invalid")
        return None
    reason = _unsafe_regex_reason(pattern, parsed)
    if reason:
        errors.append(f"{label}.unsafe_{reason}")
        return None
    groups = parsed.state.groups - 1
    if groups > 64:
        errors.append(f"{label}.too_many_capture_groups")
        return None
    if groups < minimum_groups:
        errors.append(f"{label}.missing_capture_groups")
    elif minimum_groups and parsed.state.groupwidths[1][0] <= 0:
        errors.append(f"{label}.empty_first_capture_group")
    missing = [name for name in required_groups if name not in parsed.state.groupdict]
    if missing:
        errors.append(f"{label}.missing_named_groups")
    try:
        return re.compile(pattern)
    except (re.error, RecursionError, OverflowError, MemoryError):
        errors.append(f"{label}.invalid")
        return None


def _unsafe_regex_reason(pattern: str, parsed: Any) -> str | None:
    if re.search(r"\\[1-9]|\(\?P=", pattern):
        return "backreference"
    if _contains_unsupported_regex_operation(parsed):
        return "unsupported_operation"
    if parsed.getwidth()[0] <= 0:
        return "zero_width"
    if _contains_regex_assertion(parsed):
        return "assertion"
    if _has_risky_repetition(parsed):
        return "risky_repetition"
    if _has_unsafe_repeat_layout(parsed):
        return "ambiguous_repeat_layout"
    return None


def _contains_unsupported_regex_operation(subpattern: Any) -> bool:
    leaf_operations = {
        sre_constants.LITERAL,
        sre_constants.NOT_LITERAL,
        sre_constants.ANY,
        sre_constants.IN,
        sre_constants.CATEGORY,
        sre_constants.AT,
    }
    repeat_operations = {
        sre_constants.MAX_REPEAT,
        sre_constants.MIN_REPEAT,
        sre_constants.POSSESSIVE_REPEAT,
    }
    for operation, argument in subpattern:
        if operation in leaf_operations:
            continue
        if operation is sre_constants.SUBPATTERN:
            if _contains_unsupported_regex_operation(argument[-1]):
                return True
            continue
        if operation is sre_constants.BRANCH:
            if any(_contains_unsupported_regex_operation(branch) for branch in argument[1]):
                return True
            continue
        if operation in repeat_operations:
            if _contains_unsupported_regex_operation(argument[2]):
                return True
            continue
        if operation in {sre_constants.ASSERT, sre_constants.ASSERT_NOT}:
            if _contains_unsupported_regex_operation(argument[1]):
                return True
            continue
        return True
    return False


def _regex_nesting_too_deep(pattern: str, *, maximum: int = 64) -> bool:
    depth = 0
    escaped = False
    in_character_class = False
    for character in pattern:
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == "[" and not in_character_class:
            in_character_class = True
            continue
        if character == "]" and in_character_class:
            in_character_class = False
            continue
        if in_character_class:
            continue
        if character == "(":
            depth += 1
            if depth > maximum:
                return True
        elif character == ")" and depth:
            depth -= 1
    return False


def _contains_regex_assertion(subpattern: Any) -> bool:
    for operation, argument in subpattern:
        if operation in {sre_constants.ASSERT, sre_constants.ASSERT_NOT}:
            return True
        if operation is sre_constants.SUBPATTERN and _contains_regex_assertion(argument[-1]):
            return True
        if operation is sre_constants.BRANCH and any(
            _contains_regex_assertion(branch) for branch in argument[1]
        ):
            return True
        if operation in {
            sre_constants.MAX_REPEAT,
            sre_constants.MIN_REPEAT,
            sre_constants.POSSESSIVE_REPEAT,
        } and _contains_regex_assertion(argument[2]):
            return True
    return False


@dataclass(slots=True)
class _RegexLayoutState:
    anchored: bool = False
    saw_consuming: bool = False
    saw_expansive_repeat: bool = False
    fixed_prefix_width: int = 0
    previous_expansive_repeat: bool = False
    expansive_repeats: int = 0
    expansive_any_repeats: int = 0

    def clone(self) -> "_RegexLayoutState":
        return _RegexLayoutState(
            anchored=self.anchored,
            saw_consuming=self.saw_consuming,
            saw_expansive_repeat=self.saw_expansive_repeat,
            fixed_prefix_width=self.fixed_prefix_width,
            previous_expansive_repeat=self.previous_expansive_repeat,
            expansive_repeats=self.expansive_repeats,
            expansive_any_repeats=self.expansive_any_repeats,
        )


def _has_unsafe_repeat_layout(subpattern: Any) -> bool:
    unsafe, _state = _scan_regex_layout(subpattern, _RegexLayoutState())
    return unsafe


def _scan_regex_layout(
    subpattern: Any,
    state: _RegexLayoutState,
) -> tuple[bool, _RegexLayoutState]:
    repeat_ops = {
        sre_constants.MAX_REPEAT,
        sre_constants.MIN_REPEAT,
        sre_constants.POSSESSIVE_REPEAT,
    }
    consuming_ops = {
        sre_constants.LITERAL,
        sre_constants.NOT_LITERAL,
        sre_constants.ANY,
        sre_constants.IN,
        sre_constants.CATEGORY,
    }
    beginning_ops = {
        sre_constants.AT_BEGINNING,
        sre_constants.AT_BEGINNING_LINE,
        sre_constants.AT_BEGINNING_STRING,
    }

    for operation, argument in subpattern:
        if operation is sre_constants.AT:
            if argument in beginning_ops and not state.saw_consuming:
                state.anchored = True
            continue
        if operation in consuming_ops:
            _mark_mandatory_width(state, 1)
            continue
        if operation is sre_constants.SUBPATTERN:
            unsafe, state = _scan_regex_layout(argument[-1], state)
            if unsafe:
                return True, state
            continue
        if operation is sre_constants.BRANCH:
            branch_states: list[_RegexLayoutState] = []
            for branch in argument[1]:
                unsafe, branch_state = _scan_regex_layout(branch, state.clone())
                if unsafe:
                    return True, state
                branch_states.append(branch_state)
            state = _merge_regex_layout_states(branch_states)
            continue
        if operation in repeat_ops:
            minimum, maximum, child = argument
            expansive = maximum == sre_constants.MAXREPEAT or maximum > 16
            child_has_expansive = _contains_expansive_repeat(child)
            if expansive:
                if state.previous_expansive_repeat:
                    return True, state
                if (
                    not state.anchored
                    and not state.saw_expansive_repeat
                    and state.fixed_prefix_width < 3
                ):
                    return True, state
                state.saw_expansive_repeat = True
                state.previous_expansive_repeat = True
                state.expansive_repeats += 1
                if state.expansive_repeats > 16:
                    return True, state
                if _contains_any_token(child):
                    state.expansive_any_repeats += 1
                    if state.expansive_any_repeats > 1:
                        return True, state
                continue
            if child_has_expansive:
                if maximum > 1:
                    return True, state
                original = state.clone()
                unsafe, included = _scan_regex_layout(child, state.clone())
                if unsafe:
                    return True, state
                state = _merge_regex_layout_states([original, included])
                continue
            child_width = child.getwidth()[0]
            mandatory_width = minimum * child_width
            if mandatory_width:
                _mark_mandatory_width(state, mandatory_width)
            continue
        if operation in {sre_constants.GROUPREF, sre_constants.GROUPREF_EXISTS}:
            return True, state
    return False, state


def _mark_mandatory_width(state: _RegexLayoutState, width: int) -> None:
    state.saw_consuming = True
    if not state.saw_expansive_repeat:
        state.fixed_prefix_width += width
    state.previous_expansive_repeat = False


def _merge_regex_layout_states(
    states: Sequence[_RegexLayoutState],
) -> _RegexLayoutState:
    if not states:
        return _RegexLayoutState()
    return _RegexLayoutState(
        anchored=all(state.anchored for state in states),
        saw_consuming=any(state.saw_consuming for state in states),
        saw_expansive_repeat=any(state.saw_expansive_repeat for state in states),
        fixed_prefix_width=min(state.fixed_prefix_width for state in states),
        previous_expansive_repeat=any(state.previous_expansive_repeat for state in states),
        expansive_repeats=max(state.expansive_repeats for state in states),
        expansive_any_repeats=max(state.expansive_any_repeats for state in states),
    )


def _contains_expansive_repeat(subpattern: Any) -> bool:
    repeat_ops = {
        sre_constants.MAX_REPEAT,
        sre_constants.MIN_REPEAT,
        sre_constants.POSSESSIVE_REPEAT,
    }
    for operation, argument in subpattern:
        if operation in repeat_ops:
            _minimum, maximum, child = argument
            if maximum == sre_constants.MAXREPEAT or maximum > 16:
                return True
            if _contains_expansive_repeat(child):
                return True
        elif operation is sre_constants.SUBPATTERN:
            if _contains_expansive_repeat(argument[-1]):
                return True
        elif operation is sre_constants.BRANCH and any(
            _contains_expansive_repeat(branch) for branch in argument[1]
        ):
            return True
    return False


def _contains_any_token(subpattern: Any) -> bool:
    for operation, argument in subpattern:
        if operation is sre_constants.ANY:
            return True
        if operation is sre_constants.SUBPATTERN and _contains_any_token(argument[-1]):
            return True
        if operation is sre_constants.BRANCH and any(
            _contains_any_token(branch) for branch in argument[1]
        ):
            return True
        if operation in {
            sre_constants.MAX_REPEAT,
            sre_constants.MIN_REPEAT,
            sre_constants.POSSESSIVE_REPEAT,
        } and _contains_any_token(argument[2]):
            return True
    return False


def _has_risky_repetition(
    subpattern: Any,
    *,
    inside_expansive_repeat: bool = False,
) -> bool:
    repeat_ops = {
        sre_constants.MAX_REPEAT,
        sre_constants.MIN_REPEAT,
        sre_constants.POSSESSIVE_REPEAT,
    }
    previous_expansive_repeat = False
    for operation, argument in subpattern:
        if operation in repeat_ops:
            _minimum, maximum, child = argument
            expansive = maximum == sre_constants.MAXREPEAT or maximum > 16
            if previous_expansive_repeat or inside_expansive_repeat:
                return True
            if expansive and _contains_regex_complexity(child):
                return True
            if _has_risky_repetition(
                child,
                inside_expansive_repeat=inside_expansive_repeat or expansive,
            ):
                return True
            previous_expansive_repeat = expansive
            continue

        previous_expansive_repeat = False
        if operation is sre_constants.SUBPATTERN:
            if _has_risky_repetition(
                argument[-1],
                inside_expansive_repeat=inside_expansive_repeat,
            ):
                return True
        elif operation is sre_constants.BRANCH:
            if inside_expansive_repeat:
                return True
            if any(
                _has_risky_repetition(
                    branch,
                    inside_expansive_repeat=inside_expansive_repeat,
                )
                for branch in argument[1]
            ):
                return True
        elif operation in {sre_constants.ASSERT, sre_constants.ASSERT_NOT}:
            if inside_expansive_repeat:
                return True
            if _has_risky_repetition(
                argument[1],
                inside_expansive_repeat=inside_expansive_repeat,
            ):
                return True
    return False


def _contains_regex_complexity(subpattern: Any) -> bool:
    repeat_ops = {
        sre_constants.MAX_REPEAT,
        sre_constants.MIN_REPEAT,
        sre_constants.POSSESSIVE_REPEAT,
    }
    for operation, argument in subpattern:
        if operation in repeat_ops:
            return True
        if operation in {
            sre_constants.BRANCH,
            sre_constants.ASSERT,
            sre_constants.ASSERT_NOT,
            sre_constants.GROUPREF_EXISTS,
        }:
            return True
        if operation is sre_constants.SUBPATTERN and _contains_regex_complexity(argument[-1]):
            return True
    return False
