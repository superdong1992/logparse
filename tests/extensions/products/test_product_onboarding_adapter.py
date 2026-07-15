from __future__ import annotations

import gzip
import json
import zipfile
from pathlib import Path

import pytest

from backend.config_migration import normalize_config_for_runtime
from backend.contracts.product_onboarding import (
    CandidateDocument,
    RegexEvaluation,
    SampleBatch,
    SampleFile,
)
from backend.decompressor import Decompressor
from backend.extensions.products.current.parser import ParserPlugin
from backend.extensions.products.current.scanner import ScannerPlugin
from backend.extensions.products.onboarding.current_module1 import (
    CurrentModule1OnboardingAdapter,
)
from backend.models import ParseResult
from backend.presentation.cli.composition import build_product_onboarding_application


def _line(timestamp: str, *, context: str = "ready") -> str:
    return (
        f"{timestamp} MODULE Service=control; Slot=1; CPU-Id=0; "
        f"ProcessName=worker-42; Context={context}; No[7]"
    )


def _sample(
    name: str,
    *lines: str,
    compressed: bool = False,
) -> SampleFile:
    characters = sum(len(line) for line in lines)
    return SampleFile(
        file_id="file_001",
        name=name,
        size_bytes=characters,
        compressed=compressed,
        sampled_lines=len(lines),
        nonempty_lines=sum(1 for line in lines if line.strip()),
        sampled_characters=characters,
        replacement_characters=0,
        truncated=False,
        binary_likely=False,
        lines=tuple(lines),
    )


def _batch(*samples: SampleFile, encoding: str = "utf-8") -> SampleBatch:
    normalized = tuple(
        SampleFile(
            file_id=f"file_{index:03d}",
            name=sample.name,
            size_bytes=sample.size_bytes,
            compressed=sample.compressed,
            sampled_lines=sample.sampled_lines,
            nonempty_lines=sample.nonempty_lines,
            sampled_characters=sample.sampled_characters,
            replacement_characters=sample.replacement_characters,
            truncated=sample.truncated,
            binary_likely=sample.binary_likely,
            lines=sample.lines,
        )
        for index, sample in enumerate(samples, 1)
    )
    return SampleBatch(encoding=encoding, files=normalized)


def _candidate(**overrides) -> CandidateDocument:
    payload = {
        "file_patterns": ["device_*.log"],
        "timestamp_regex": r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})()",
        "module_name": "MODULE",
        "diag_pattern": (
            r"Slot=(?P<Slot>[^;]+);\s*CPU-Id=(?P<CPU_Id>[^;]*);\s*"
            r"ProcessName=(?P<ProcessName>[^;]+);\s*Context=(?P<Context>[^;]+)"
        ),
        "sequence_pattern": r"No\[(\d+)\]",
    }
    payload.update(overrides)
    return CandidateDocument(
        schema_version=1,
        adapter="current_module1",
        payload=payload,
    )


def test_analysis_infers_rotated_family_timestamp_and_field_aliases() -> None:
    secret = "PRIVATE_CONTEXT"
    batch = _batch(
        _sample("device_20260101.log", _line("2026-01-03 00:00:00", context=secret)),
        _sample("device_20260102.log", _line("2026-01-03 00:01:00", context=secret)),
    )

    analysis = CurrentModule1OnboardingAdapter().analyze(batch)
    serialized = json.dumps(analysis.data, ensure_ascii=False)

    assert analysis.status == "needs_candidate"
    assert analysis.data["candidate_hints"]["discovery"]["file_patterns"] == ["device_*.log"]
    assert len(analysis.data["format_families"]) == 1
    assert {
        item["field"] for item in analysis.data["candidate_hints"]["mechanism"]["field_hints"]
    } == {"Slot", "CPU_Id", "ProcessName", "Context"}
    assert secret not in serialized


def test_mixed_timestamp_families_require_normalization() -> None:
    analysis = CurrentModule1OnboardingAdapter().analyze(
        _batch(
            _sample("iso.log", _line("2026-01-03 00:00:00")),
            _sample("slash.log", _line("2026/01/03 00:00:00")),
        )
    )

    assert len(analysis.data["format_families"]) == 2
    assert "multiple_source_formats" in analysis.data["unresolved"]
    assert "timestamp_normalization" in analysis.data["extension_requirements"]


def test_suggested_timestamp_prefers_runtime_compatible_highest_coverage() -> None:
    analysis = CurrentModule1OnboardingAdapter().analyze(
        _batch(
            _sample(
                "mixed.log",
                _line("2026-01-03 00:00:00"),
                *(_line(f"20260103T0000{second:02d}") for second in range(10)),
            )
        )
    )

    candidates = analysis.data["timestamp_candidates"]
    assert candidates[0]["key"] == "compact_iso8601"
    assert analysis.data["candidate_hints"]["parser"]["timestamp_regex"] == (
        candidates[0]["regex"]
    )


def test_field_alias_detection_supports_whitespace_and_identifier_boundaries() -> None:
    positive = CurrentModule1OnboardingAdapter().analyze(
        _batch(
            _sample(
                "positive.log",
                "2026-01-03 00:00:00 MODULE "
                "Slot 1 CPU-Id 0 ProcessName worker Context ready",
            )
        )
    )
    negative = CurrentModule1OnboardingAdapter().analyze(
        _batch(
            _sample(
                "negative.log",
                "2026-01-03 00:00:00 MODULE "
                "NoSlot=1 xCPU-Id=0 xProcessName=worker NoContext=ready",
            )
        )
    )

    assert {
        item["field"]
        for item in positive.data["candidate_hints"]["mechanism"]["field_hints"]
    } == {"Slot", "CPU_Id", "ProcessName", "Context"}
    assert negative.data["candidate_hints"]["mechanism"]["field_hints"] == []


def test_adapter_builds_declarative_current_replay_spec() -> None:
    adapter = CurrentModule1OnboardingAdapter()
    replay = adapter.prepare_candidate(
        _batch(_sample("device_001.log", _line("2026-01-03 00:00:00"))),
        _candidate(),
    )

    assert replay.errors == ()
    assert replay.plan is not None
    assert replay.plan.required_groups == ("Slot", "CPU_Id", "ProcessName", "Context")
    assert {(probe.kind, probe.group) for probe in replay.plan.probes} == {
        ("non_empty", "ProcessName"),
        ("suffix_decimal", "ProcessName"),
        ("base_non_empty_after_decimal_suffix", "ProcessName"),
        ("equals", "CPU_Id"),
    }


def test_replay_interpretation_separates_policy_from_technical_readiness() -> None:
    adapter = CurrentModule1OnboardingAdapter()
    replay = adapter.prepare_candidate(
        _batch(_sample("device_001.log", _line("2026-01-03 00:00:00"))),
        _candidate(),
    )
    evaluation = RegexEvaluation(
        status="ok",
        counters={
            "record_matches": 1,
            "timestamp_parseable": 1,
            "timestamp_runtime_errors": 0,
            "ordinal_matches": 1,
            "ordinal_integers": 1,
        },
        group_counts={name: 1 for name in replay.plan.required_groups},
        probe_counts={
            "name_non_empty": 1,
            "numeric_suffix": 1,
            "resolved_name_non_empty": 1,
            "zero_value": 1,
        },
    )

    validation = adapter.finalize_validation(replay, evaluation)

    assert validation.status == "syntax_ready_needs_policy_confirmation"
    assert validation.errors == ()
    assert validation.warnings == (
        "policy.process_pid_suffix_requires_confirmation",
        "policy.cpu_zero_requires_topology_confirmation",
    )


def test_empty_process_name_after_decimal_suffix_requires_review() -> None:
    adapter = CurrentModule1OnboardingAdapter()
    replay = adapter.prepare_candidate(
        _batch(
            _sample(
                "device_001.log",
                "2026-01-03 00:00:00 MODULE Slot=1; CPU-Id=1; "
                "ProcessName=-123; Context=ready; No[7]",
            )
        ),
        _candidate(),
    )
    evaluation = RegexEvaluation(
        status="ok",
        counters={
            "record_matches": 1,
            "timestamp_parseable": 1,
            "timestamp_runtime_errors": 0,
            "ordinal_matches": 1,
            "ordinal_integers": 1,
        },
        group_counts={name: 1 for name in replay.plan.required_groups},
        probe_counts={
            "name_non_empty": 1,
            "numeric_suffix": 0,
            "resolved_name_non_empty": 0,
            "zero_value": 0,
        },
    )

    validation = adapter.finalize_validation(replay, evaluation)

    assert validation.status == "needs_review"
    assert "diag_pattern.empty_resolved_process_name" in validation.warnings


def test_projection_is_a_schema_v2_product_fragment_with_unresolved_policy() -> None:
    adapter = CurrentModule1OnboardingAdapter()
    batch = _batch(
        _sample(
            "device_001.log.gz",
            _line("2026-01-03 00:00:00"),
            compressed=True,
        )
    )
    replay = adapter.prepare_candidate(
        batch,
        _candidate(file_patterns=["device_*.log.gz"]),
    )
    evaluation = RegexEvaluation(
        status="ok",
        counters={
            "record_matches": 1,
            "timestamp_parseable": 1,
            "timestamp_runtime_errors": 0,
            "ordinal_matches": 1,
            "ordinal_integers": 1,
        },
        group_counts={name: 1 for name in replay.plan.required_groups},
        probe_counts={
            "name_non_empty": 1,
            "numeric_suffix": 1,
            "resolved_name_non_empty": 1,
            "zero_value": 1,
        },
    )
    validation = adapter.finalize_validation(replay, evaluation)

    draft = adapter.build_draft(batch, replay, validation)

    assert draft.fragment["archive"]["compressed_extensions"] == [".zip", ".gz"]
    assert draft.fragment["mechanisms"]["module1"]["config"]["module_name"] == "MODULE"
    assert "lifecycle_split" in draft.unresolved
    assert "current_module1_runs_lifecycle_v3_with_empty_policy_when_unset" in (
        draft.runtime_caveats
    )


def test_uppercase_gzip_and_non_runtime_encoding_require_an_adapter() -> None:
    analysis = CurrentModule1OnboardingAdapter().analyze(
        _batch(
            _sample("device.log.GZ", _line("2026-01-03 00:00:00"), compressed=True),
            encoding="utf-16",
        )
    )

    assert analysis.status == "needs_adapter"
    assert {
        "gzip_suffix_normalization",
        "text_encoding_adapter",
    }.issubset(analysis.data["extension_requirements"])


@pytest.mark.parametrize(
    "override",
    [
        {"unsupported": True},
        {"file_patterns": ["*"]},
        {"module_name": ""},
    ],
)
def test_invalid_candidate_fields_fail_before_sandbox(override: dict) -> None:
    replay = CurrentModule1OnboardingAdapter().prepare_candidate(
        _batch(_sample("device_001.log", _line("2026-01-03 00:00:00"))),
        _candidate(**override),
    )

    assert replay.plan is None
    assert replay.errors


@pytest.mark.parametrize("compressed", [False, True])
def test_built_fragment_runs_through_plain_and_gzip_runtime(
    tmp_path: Path,
    compressed: bool,
) -> None:
    log_name = "device_001.log.gz" if compressed else "device_001.log"
    log_path = tmp_path / log_name
    if compressed:
        with gzip.open(log_path, "wt", encoding="utf-8") as stream:
            stream.write(_line("2026-01-03 00:00:00") + "\n")
    else:
        log_path.write_text(_line("2026-01-03 00:00:00") + "\n", encoding="utf-8")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "adapter": "current_module1",
                **dict(_candidate(file_patterns=[log_name]).payload),
            }
        ),
        encoding="utf-8",
    )

    report = (
        build_product_onboarding_application().build_draft([log_path], candidate_path).to_dict()
    )
    fragment = report["draft_product_config"]
    root = {"schema_version": 2, "pipeline": {}, "products": {"new": fragment}}
    runtime = normalize_config_for_runtime(root)
    product = runtime["products"]["new"]
    outer_zip = tmp_path / "samples.zip"
    with zipfile.ZipFile(outer_zip, "w") as archive:
        archive.write(log_path, arcname=log_path.name)
    extracted = tmp_path / "extracted"
    decompressor = Decompressor(compressed_extensions=fragment["archive"]["compressed_extensions"])
    decompressor.extract_all(
        outer_zip,
        extracted,
        recursive=runtime["pipeline"]["recursive_extraction"],
    )
    scanner = ScannerPlugin(product["discovery"]["config"], decompressor)
    diagnostic_slots, private_slots = scanner.discover(extracted)
    parser = ParserPlugin(product["log_parser"]["config"])
    result = ParseResult(
        task_id="onboarding-runtime",
        package_name=outer_zip.name,
        extracted_root=str(extracted),
        diagnostic_slots=diagnostic_slots,
        private_slots=private_slots,
    )

    parser.parse(result)

    assert report["status"] == "needs_policy_confirmation"
    assert [item.name for item in diagnostic_slots[0].diagnostic_logs] == [log_name]
    assert result.mech_results[0].diag_entry_count == 1
