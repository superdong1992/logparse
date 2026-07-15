from __future__ import annotations

import json
import subprocess

import pytest

from backend.contracts.product_onboarding import (
    CaptureProbe,
    RegexEvaluationPlan,
)
from backend.infrastructure._product_onboarding_regex_worker import evaluate_payload
from backend.infrastructure.product_onboarding_regex import IsolatedRegexSandbox


def _plan(**overrides) -> RegexEvaluationPlan:
    values = {
        "timestamp_pattern": r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})()",
        "record_pattern": (r"Alpha=(?P<first>[^;]+);\s*Beta=(?P<second>[^;]*)"),
        "ordinal_pattern": r"Count\[(\d+)\]",
        "required_groups": ("first", "second"),
        "probes": (
            CaptureProbe("present", "non_empty", "first"),
            CaptureProbe("suffix", "suffix_decimal", "first"),
            CaptureProbe("zero", "equals", "second", "0"),
        ),
    }
    values.update(overrides)
    return RegexEvaluationPlan(**values)


def _payload() -> dict:
    plan = _plan()
    return {
        "schema_version": 1,
        "lines": ["2026-01-03 00:00:00 Alpha=worker-42; Beta=0; Count[7]"],
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
    }


def test_worker_pure_function_returns_only_generic_aggregates() -> None:
    result = evaluate_payload(_payload())

    assert result["counters"] == {
        "record_matches": 1,
        "timestamp_parseable": 1,
        "timestamp_runtime_errors": 0,
        "ordinal_matches": 1,
        "ordinal_integers": 1,
    }
    assert result["group_counts"] == {"first": 1, "second": 1}
    assert result["probe_counts"] == {"present": 1, "suffix": 1, "zero": 1}
    assert "worker-42" not in repr(result)


def test_generic_probe_rejects_an_empty_base_after_decimal_suffix() -> None:
    payload = _payload()
    payload["lines"] = ["2026-01-03 00:00:00 Alpha=-42; Beta=0; Count[7]"]
    payload["probes"].append(
        {
            "probe_id": "base_present",
            "kind": "base_non_empty_after_decimal_suffix",
            "group": "first",
            "value": "",
        }
    )

    result = evaluate_payload(payload)

    assert result["probe_counts"]["base_present"] == 0


def test_real_isolated_subprocess_smoke_does_not_echo_unicode_content() -> None:
    secret = "私密内容_PRIVATE"
    line = f"2026-01-03 00:00:00 Alpha=worker-42; Beta=0; Count[7] {secret}"

    result = IsolatedRegexSandbox().evaluate([line], _plan())

    assert result.status == "ok"
    assert result.counters["record_matches"] == 1
    assert secret not in repr(result)


@pytest.mark.parametrize(
    ("pattern", "error_suffix"),
    [
        (
            r"Alpha=(?P<first>(a+)+); Beta=(?P<second>x)",
            "risky_repetition",
        ),
        (
            r"(?=Alpha)Alpha=(?P<first>x); Beta=(?P<second>y)",
            "assertion",
        ),
        (
            r"Alpha=(?P<first>.+)\1; Beta=(?P<second>y)",
            "backreference",
        ),
        (
            r"(?P<first>.*)X(?P<second>.*)",
            "ambiguous_repeat_layout",
        ),
        ("(" * 65 + "x" + ")" * 65, "too_deep"),
    ],
)
def test_static_checks_reject_unsafe_shapes(pattern: str, error_suffix: str) -> None:
    result = IsolatedRegexSandbox().evaluate(["bounded"], _plan(record_pattern=pattern))

    assert result.status == "rejected"
    assert any(error.endswith(error_suffix) for error in result.errors)


def test_missing_named_groups_and_timestamp_capture_contract_are_rejected() -> None:
    result = IsolatedRegexSandbox().evaluate(
        ["bounded"],
        _plan(
            timestamp_pattern=r"(2026)",
            record_pattern=r"Alpha=(?P<first>value)",
        ),
    )

    assert "timestamp_pattern.missing_capture_groups" in result.errors
    assert "record_pattern.missing_named_groups" in result.errors


def test_timeout_is_a_stable_technical_error() -> None:
    def timeout_runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="worker", timeout=5)

    result = IsolatedRegexSandbox(runner=timeout_runner).evaluate(
        ["2026-01-03 00:00:00 Alpha=x; Beta=0; Count[1]"], _plan()
    )

    assert result.errors == ("regex_execution.timeout",)


@pytest.mark.parametrize("timeout", [0, -1, 5.01, float("inf"), float("nan"), True])
def test_timeout_cannot_exceed_the_five_second_hard_limit(timeout) -> None:
    with pytest.raises(ValueError):
        IsolatedRegexSandbox(timeout_seconds=timeout)


@pytest.mark.parametrize(
    "lines",
    [
        "not-a-line-sequence",
        ["x" * 4097],
        ["x" * 4096] * 257,
        [""] * 32_769,
    ],
)
def test_parent_rejects_unbounded_lines_before_starting_worker(lines) -> None:
    def runner(*args, **kwargs):
        raise AssertionError("worker must not be started")

    result = IsolatedRegexSandbox(runner=runner).evaluate(lines, _plan())

    assert result.errors == ("regex_execution.input_limit",)


def test_parent_rejects_oversized_declarative_plan_before_worker() -> None:
    def runner(*args, **kwargs):
        raise AssertionError("worker must not be started")

    probes = tuple(
        CaptureProbe(f"probe{index}", "non_empty", "first") for index in range(65)
    )
    result = IsolatedRegexSandbox(runner=runner).evaluate(
        ["bounded"],
        _plan(probes=probes, timestamp_capture_count=65),
    )

    assert "record_pattern.invalid_probe" in result.errors
    assert "timestamp_pattern.invalid_capture_count" in result.errors


def test_real_worker_accepts_worst_case_json_escaping_within_sample_budget() -> None:
    lines = ["\x00" * 4096] * 256

    result = IsolatedRegexSandbox().evaluate(lines, _plan())

    assert result.status == "ok"
    assert result.counters["record_matches"] == 0


@pytest.mark.parametrize(
    "completed",
    [
        subprocess.CompletedProcess([], 2, stdout='{"status":"error"}\n', stderr=""),
        subprocess.CompletedProcess([], 0, stdout="not-json", stderr=""),
        subprocess.CompletedProcess([], 0, stdout='{"status":"ok"}', stderr=""),
        subprocess.CompletedProcess([], 0, stdout="x" * (64 * 1024 + 1), stderr=""),
        subprocess.CompletedProcess([], 0, stdout='{"status":"ok"}', stderr="private"),
    ],
)
def test_worker_failures_and_malformed_ipc_fail_closed(completed) -> None:
    def runner(*args, **kwargs):
        return completed

    result = IsolatedRegexSandbox(runner=runner).evaluate(
        ["2026-01-03 00:00:00 Alpha=x; Beta=0; Count[1]"], _plan()
    )

    assert result.errors == ("regex_execution.failed",)
    assert "private" not in repr(result)


def test_worker_rejects_unbounded_or_malformed_payloads() -> None:
    payload = _payload()
    payload["lines"] = ["x" * 4097]

    with pytest.raises(ValueError):
        evaluate_payload(payload)


def test_valid_fake_ipc_is_normalized_without_extra_fields() -> None:
    payload = {
        "status": "ok",
        "counters": {
            "record_matches": 1,
            "timestamp_parseable": 1,
            "timestamp_runtime_errors": 0,
            "ordinal_matches": 1,
            "ordinal_integers": 1,
        },
        "group_counts": {"first": 1, "second": 1},
        "probe_counts": {"present": 1, "suffix": 1, "zero": 1},
    }

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")

    result = IsolatedRegexSandbox(runner=runner).evaluate(
        ["2026-01-03 00:00:00 Alpha=x; Beta=0; Count[1]"], _plan()
    )

    assert result.status == "ok"
    assert dict(result.group_counts) == {"first": 1, "second": 1}
