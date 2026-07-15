"""Current-product format knowledge for log onboarding.

This adapter receives an already bounded in-memory sample batch.  It owns no
filesystem access, subprocess execution, CLI behavior, or persistence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from backend.contracts.product_onboarding import (
    AdapterAnalysis,
    AdapterDraft,
    AdapterValidation,
    CandidateDocument,
    CandidateReplay,
    CaptureProbe,
    OnboardingDiagnostic,
    RegexEvaluation,
    RegexEvaluationPlan,
    SampleBatch,
    SampleFile,
)


_ADAPTER_ID = "current_module1"
_CURRENT_DISCOVERY_PLUGIN = "backend.extensions.products.current.scanner.ScannerPlugin"
_CURRENT_PARSER_PLUGIN = "backend.extensions.products.current.parser.ParserPlugin"
_MODULE_PLUGIN = "backend.extensions.mechanisms.module1.Module1Plugin"
_REQUIRED_GROUPS = ("Slot", "CPU_Id", "ProcessName", "Context")
_NEVER_MATCH_REGEX = r"(?!)"
_CANDIDATE_FIELDS = {
    "file_patterns",
    "timestamp_regex",
    "module_name",
    "diag_pattern",
    "sequence_pattern",
}
_REQUIRED_CANDIDATE_FIELDS = _CANDIDATE_FIELDS - {"sequence_pattern"}


@dataclass(frozen=True, slots=True)
class _TimestampTemplate:
    key: str
    regex: str
    runtime_compatible: bool


_TIMESTAMP_TEMPLATES = (
    _TimestampTemplate(
        "iso8601",
        (
            r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
            r"(?:[.,]\d+)?)(Z|[+-]\d{2}:?\d{2})?"
        ),
        True,
    ),
    _TimestampTemplate(
        "compact_iso8601",
        r"(\d{8}T\d{6}(?:[.,]\d+)?)(Z|[+-]\d{2}:?\d{2})?",
        True,
    ),
    _TimestampTemplate(
        "slash_datetime",
        r"(\d{4}/\d{2}/\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)()",
        False,
    ),
    _TimestampTemplate(
        "syslog_without_year",
        (
            r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
            r"\d{1,2}\s+\d{2}:\d{2}:\d{2})()"
        ),
        False,
    ),
)


_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "Slot": ("Slot", "slot", "slotId", "slot_id"),
    "CPU_Id": ("CPU-Id", "CPU_Id", "cpuId", "cpu_id", "cpu"),
    "ProcessName": ("ProcessName", "process_name", "process", "proc"),
    "Context": ("Context", "context", "message", "msg", "ctx"),
}


_SEQUENCE_TEMPLATES = (
    ("no_brackets", r"No\[(\d+)\]"),
    ("sequence_label", r"(?i)(?:sequence|seq)\s*[=:]\s*(\d+)"),
)


@dataclass(frozen=True, slots=True)
class _Candidate:
    file_patterns: tuple[str, ...]
    timestamp_regex: str
    module_name: str
    diag_pattern: str
    sequence_pattern: str = ""


@dataclass(frozen=True, slots=True)
class _FileEvidence:
    sample: SampleFile
    timestamp_counts: Mapping[str, tuple[int, int]]
    field_counts: Mapping[tuple[str, str], int]
    sequence_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class _AnalysisState:
    data: Mapping[str, Any]
    unresolved: tuple[str, ...]
    extension_requirements: tuple[str, ...]
    suggested_timestamp_regex: str | None


@dataclass(frozen=True, slots=True)
class _ReplayState:
    candidate: _Candidate | None
    analysis: _AnalysisState


class CurrentModule1OnboardingAdapter:
    @property
    def adapter_id(self) -> str:
        return _ADAPTER_ID

    def analyze(self, batch: SampleBatch) -> AdapterAnalysis:
        state = _analyze_batch(batch)
        diagnostics = tuple(
            OnboardingDiagnostic(
                code=f"adapter_required.{requirement}",
                message="The observed format requires an explicit adapter decision.",
                severity="warning",
            )
            for requirement in state.extension_requirements
        )
        return AdapterAnalysis(
            status=("needs_adapter" if state.extension_requirements else "needs_candidate"),
            data=state.data,
            diagnostics=diagnostics,
        )

    def prepare_candidate(
        self,
        batch: SampleBatch,
        document: CandidateDocument,
    ) -> CandidateReplay:
        analysis = _analyze_batch(batch)
        candidate, errors = _candidate_from_payload(document.payload)
        metrics: dict[str, int] = {
            "prepared_files": len(batch.files),
            "matched_files": 0,
            "sampled_lines": 0,
            "nonempty_lines": 0,
            "module_lines": 0,
        }
        warnings: list[str] = []
        state = _ReplayState(candidate=candidate, analysis=analysis)
        if candidate is None:
            return CandidateReplay(
                lines=(),
                metrics=metrics,
                errors=tuple(errors),
                state=state,
            )

        glob_patterns, pattern_errors = _compile_file_patterns(candidate.file_patterns)
        errors.extend(pattern_errors)
        matched = tuple(
            sample
            for sample in batch.files
            if any(pattern.match(sample.name) for pattern in glob_patterns)
        )
        metrics["matched_files"] = len(matched)
        metrics["sampled_lines"] = sum(sample.sampled_lines for sample in matched)
        metrics["nonempty_lines"] = sum(sample.nonempty_lines for sample in matched)
        marker = candidate.module_name.upper()
        lines = tuple(
            line for sample in matched for line in sample.lines if line.strip() and marker in line
        )
        metrics["module_lines"] = len(lines)

        if len(matched) != len(batch.files):
            warnings.append("file_patterns.unmatched_files")
        if metrics["nonempty_lines"] == 0:
            warnings.append("samples.no_nonempty_lines")
        if not lines:
            warnings.append("module_name.no_matches")
        if any(sample.truncated for sample in matched):
            warnings.append("samples.truncated")
        if any(sample.replacement_characters for sample in matched):
            warnings.append("samples.decode_replacements")
        if any(sample.binary_likely for sample in matched):
            warnings.append("samples.binary_likely")
        if batch.encoding != "utf-8":
            warnings.append("samples.non_runtime_encoding")
        if any(sample.compressed and not sample.name.endswith(".gz") for sample in matched):
            warnings.append("samples.gzip_suffix_requires_normalization")
        warnings.extend(
            f"adapter_required.{requirement}" for requirement in analysis.extension_requirements
        )
        if errors:
            return CandidateReplay(
                lines=lines,
                metrics=metrics,
                errors=tuple(dict.fromkeys(errors)),
                warnings=tuple(dict.fromkeys(warnings)),
                state=state,
            )

        return CandidateReplay(
            lines=lines,
            plan=RegexEvaluationPlan(
                timestamp_pattern=candidate.timestamp_regex,
                record_pattern=candidate.diag_pattern,
                ordinal_pattern=candidate.sequence_pattern,
                required_groups=_REQUIRED_GROUPS,
                probes=(
                    CaptureProbe(
                        probe_id="name_non_empty",
                        kind="non_empty",
                        group="ProcessName",
                    ),
                    CaptureProbe(
                        probe_id="numeric_suffix",
                        kind="suffix_decimal",
                        group="ProcessName",
                    ),
                    CaptureProbe(
                        probe_id="resolved_name_non_empty",
                        kind="base_non_empty_after_decimal_suffix",
                        group="ProcessName",
                    ),
                    CaptureProbe(
                        probe_id="zero_value",
                        kind="equals",
                        group="CPU_Id",
                        value="0",
                    ),
                ),
            ),
            metrics=metrics,
            warnings=tuple(dict.fromkeys(warnings)),
            state=state,
        )

    def finalize_validation(
        self,
        replay: CandidateReplay,
        evaluation: RegexEvaluation,
    ) -> AdapterValidation:
        errors = list(replay.errors)
        warnings = list(replay.warnings)
        metrics = dict(replay.metrics)
        metrics.update(
            {
                "diag_matches": 0,
                "diag_timestamps_parseable": 0,
                "timestamp_runtime_errors": 0,
                "sequence_matches": 0,
                "sequence_parseable": 0,
                "process_pid_splits": 0,
                "process_names_resolved": 0,
                "cpu_zero_projections": 0,
            }
        )
        group_counts = {name: 0 for name in _REQUIRED_GROUPS}
        if evaluation.status != "ok":
            errors.extend(evaluation.errors)
        else:
            counters = evaluation.counters
            group_counts.update(evaluation.group_counts)
            metrics.update(
                {
                    "diag_matches": counters.get("record_matches", 0),
                    "diag_timestamps_parseable": counters.get("timestamp_parseable", 0),
                    "timestamp_runtime_errors": counters.get("timestamp_runtime_errors", 0),
                    "sequence_matches": counters.get("ordinal_matches", 0),
                    "sequence_parseable": counters.get("ordinal_integers", 0),
                    "process_pid_splits": evaluation.probe_counts.get("numeric_suffix", 0),
                    "process_names_resolved": evaluation.probe_counts.get(
                        "resolved_name_non_empty", 0
                    ),
                    "cpu_zero_projections": evaluation.probe_counts.get("zero_value", 0),
                }
            )

        diag_matches = metrics["diag_matches"]
        if evaluation.status == "ok":
            if diag_matches != metrics["module_lines"]:
                warnings.append("diag_pattern.partial_coverage")
            if diag_matches and metrics["diag_timestamps_parseable"] != diag_matches:
                warnings.append("timestamp_regex.missing_on_diag_matches")
            if metrics["timestamp_runtime_errors"]:
                errors.append("timestamp_regex.runtime_type_error")
            for group_name in ("Slot", "ProcessName", "Context"):
                if diag_matches and group_counts[group_name] != diag_matches:
                    warnings.append(f"diag_pattern.empty_group.{group_name}")
            state = replay.state
            candidate = state.candidate if isinstance(state, _ReplayState) else None
            if candidate is not None and candidate.sequence_pattern and diag_matches:
                if metrics["sequence_matches"] == 0:
                    warnings.append("sequence_pattern.no_matches")
                elif metrics["sequence_parseable"] != metrics["sequence_matches"]:
                    warnings.append("sequence_pattern.non_integer_matches")
            if metrics["process_pid_splits"]:
                warnings.append("policy.process_pid_suffix_requires_confirmation")
            if diag_matches and metrics["process_names_resolved"] != diag_matches:
                warnings.append("diag_pattern.empty_resolved_process_name")
            if metrics["cpu_zero_projections"]:
                warnings.append("policy.cpu_zero_requires_topology_confirmation")
            if diag_matches and group_counts["CPU_Id"] != diag_matches:
                warnings.append("policy.cpu_empty_requires_topology_confirmation")

        errors = list(dict.fromkeys(errors))
        warnings = list(dict.fromkeys(warnings))
        technical_warnings = [warning for warning in warnings if not warning.startswith("policy.")]
        policy_warnings = [warning for warning in warnings if warning.startswith("policy.")]
        if errors:
            status = "invalid"
        elif technical_warnings:
            status = "needs_review"
        elif policy_warnings:
            status = "syntax_ready_needs_policy_confirmation"
        else:
            status = "syntax_ready"

        state = replay.state
        unresolved: tuple[str, ...] = ()
        if isinstance(state, _ReplayState):
            unresolved_set = set(state.analysis.unresolved)
            unresolved_set -= {
                "module_name",
                "diag_pattern",
                "timestamp_regex",
                *(f"field_mapping.{name}" for name in _REQUIRED_GROUPS),
            }
            if state.candidate is not None and not state.candidate.sequence_pattern:
                unresolved_set.add("sequence_pattern")
            unresolved = tuple(sorted(unresolved_set))
        return AdapterValidation(
            status=status,
            metrics=metrics,
            group_counts=group_counts,
            errors=tuple(errors),
            warnings=tuple(warnings),
            unresolved=unresolved,
        )

    def build_draft(
        self,
        batch: SampleBatch,
        replay: CandidateReplay,
        validation: AdapterValidation,
    ) -> AdapterDraft:
        state = replay.state
        if not isinstance(state, _ReplayState) or state.candidate is None:
            raise ValueError("candidate replay state is unavailable")
        if not validation.technically_ready:
            raise ValueError("candidate is not technically ready")
        candidate = state.candidate
        compressed_extensions = [".zip"]
        if any(sample.compressed for sample in batch.files):
            compressed_extensions.append(".gz")
        fragment = {
            "archive": {
                "recursive_extraction": False,
                "compressed_extensions": compressed_extensions,
            },
            "discovery": {
                "plugin": _CURRENT_DISCOVERY_PLUGIN,
                "config": {
                    "loose_diagnostics": {
                        "enabled": True,
                        "file_patterns": list(candidate.file_patterns),
                    },
                    "filename_timestamp_regex": _NEVER_MATCH_REGEX,
                },
            },
            "parser": {
                "plugin": _CURRENT_PARSER_PLUGIN,
                "config": {"timestamp_regex": candidate.timestamp_regex},
            },
            "mechanisms": {
                "module1": {
                    "plugin": _MODULE_PLUGIN,
                    "enabled": True,
                    "depends_on": [],
                    "config": {
                        "module_name": candidate.module_name,
                        "diag_pattern": candidate.diag_pattern,
                        "sequence_pattern": (candidate.sequence_pattern or _NEVER_MATCH_REGEX),
                    },
                }
            },
        }
        return AdapterDraft(
            fragment=fragment,
            unresolved=validation.unresolved,
            runtime_caveats=(
                "current_parser_uses_300_second_active_period_default_when_unset",
                "current_module1_runs_lifecycle_v3_with_empty_policy_when_unset",
                "current_module1_projects_cpu_id_zero_to_board_level",
                "current_module1_projects_empty_cpu_id_to_board_level",
                "current_module1_splits_numeric_process_suffix_as_pid",
                "current_parser_role_fallback_runs_on_synthetic_loose_slot",
                "schema_v2_runtime_uses_first_product_archive_recursion_setting",
                "current_pipeline_requires_utf8_log_text",
                "all_staged_files_are_treated_as_loose_diagnostics",
                "candidate_regex_requires_lan_corpus_performance_validation",
            ),
        )


def _candidate_from_payload(
    payload: Mapping[str, Any],
) -> tuple[_Candidate | None, list[str]]:
    errors: list[str] = []
    if set(payload) - _CANDIDATE_FIELDS:
        errors.append("candidate.unsupported_fields")
    if _REQUIRED_CANDIDATE_FIELDS - set(payload):
        errors.append("candidate.missing_fields")
    file_patterns = payload.get("file_patterns")
    if not isinstance(file_patterns, list):
        errors.append("file_patterns.invalid_type")
        normalized_patterns: tuple[str, ...] = ()
    else:
        normalized_patterns = tuple(file_patterns)
    values: dict[str, str] = {}
    for name in ("timestamp_regex", "module_name", "diag_pattern"):
        value = payload.get(name)
        if not isinstance(value, str):
            errors.append(f"{name}.invalid_type")
            value = ""
        values[name] = value
    sequence = payload.get("sequence_pattern", "")
    if not isinstance(sequence, str):
        errors.append("sequence_pattern.invalid_type")
        sequence = ""
    module_name = values["module_name"]
    if (
        not module_name.strip()
        or module_name != module_name.strip()
        or len(module_name) > 128
        or any(character in module_name for character in "\r\n\x00")
    ):
        errors.append("module_name.invalid")
    if errors:
        return None, list(dict.fromkeys(errors))
    return (
        _Candidate(
            file_patterns=normalized_patterns,
            timestamp_regex=values["timestamp_regex"],
            module_name=module_name,
            diag_pattern=values["diag_pattern"],
            sequence_pattern=sequence,
        ),
        [],
    )


def _compile_file_patterns(
    patterns: Sequence[Any],
) -> tuple[tuple[re.Pattern[str], ...], list[str]]:
    errors: list[str] = []
    if not patterns:
        return (), ["file_patterns.empty"]
    if len(patterns) > 128:
        errors.append("file_patterns.too_many")
    compiled: list[re.Pattern[str]] = []
    seen: set[str] = set()
    for pattern in patterns:
        if not isinstance(pattern, str):
            errors.append("file_patterns.invalid_type")
            continue
        normalized = pattern.casefold()
        stable_literal = re.sub(r"[*?]", "", pattern).strip("._- ")
        if normalized in seen:
            errors.append("file_patterns.duplicate")
        elif (
            not pattern
            or len(pattern) > 255
            or "/" in pattern
            or "\\" in pattern
            or not stable_literal
        ):
            errors.append("file_patterns.invalid")
        else:
            seen.add(normalized)
            regex = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
            compiled.append(re.compile(f"^{regex}$", re.IGNORECASE))
    return tuple(compiled), list(dict.fromkeys(errors))


def _analyze_batch(batch: SampleBatch) -> _AnalysisState:
    evidence = tuple(_file_evidence(sample) for sample in batch.files)
    timestamp_candidates = _aggregate_timestamp_candidates(evidence)
    field_hints = _aggregate_field_hints(evidence)
    sequence_candidates = _aggregate_sequence_candidates(evidence)
    families = _build_format_families(evidence)
    suggested_patterns = _infer_file_patterns(sample.name for sample in batch.files)
    suggested_timestamp = next(
        (
            item["regex"]
            for item in timestamp_candidates
            if item["runtime_compatible"] and item["parseable_lines"] > 0
        ),
        None,
    )

    unresolved = {
        "module_name",
        "diag_pattern",
        "active_period_gap_seconds",
        "active_master_keyword",
        "lifecycle_split",
        "cpu_zero_semantics",
        "cpu_empty_semantics",
        "process_pid_suffix_semantics",
        "file_role_mapping",
        "role_topology_mapping",
        "full_corpus_regex_performance",
    }
    fields_with_hints = {item["field"] for item in field_hints}
    for field in _REQUIRED_GROUPS:
        if field not in fields_with_hints:
            unresolved.add(f"field_mapping.{field}")
    if suggested_timestamp is None:
        unresolved.add("timestamp_regex")
    if len(families) > 1:
        unresolved.add("multiple_source_formats")
    if any(sample.truncated for sample in batch.files):
        unresolved.add("sample_coverage")

    extension_requirements: set[str] = set()
    if any(sample.binary_likely for sample in batch.files):
        extension_requirements.add("text_or_binary_adapter")
    if any(sample.replacement_characters for sample in batch.files):
        extension_requirements.add("encoding_hint")
    if batch.encoding != "utf-8":
        extension_requirements.add("text_encoding_adapter")
    if any(sample.compressed and not sample.name.endswith(".gz") for sample in batch.files):
        extension_requirements.add("gzip_suffix_normalization")
    template_by_key = {item.key: item for item in _TIMESTAMP_TEMPLATES}
    if any(
        counts[0] > 0 and not template_by_key[key].runtime_compatible
        for item in evidence
        for key, counts in item.timestamp_counts.items()
    ):
        extension_requirements.add("timestamp_normalization")

    files = []
    for item in evidence:
        profile = item.sample.public_profile()
        profile["dominant_timestamp"] = _dominant_timestamp(item)
        files.append(profile)
    data = {
        "sample_encoding": batch.encoding,
        "files": files,
        "format_families": families,
        "timestamp_candidates": timestamp_candidates,
        "candidate_hints": {
            "archive": {
                "observed_extensions": sorted(
                    {".gz" for sample in batch.files if sample.compressed}
                )
            },
            "discovery": {
                "plugin": _CURRENT_DISCOVERY_PLUGIN,
                "mode": "loose_diagnostics",
                "file_patterns": list(suggested_patterns),
            },
            "parser": {
                "plugin": _CURRENT_PARSER_PLUGIN,
                "timestamp_regex": suggested_timestamp,
            },
            "mechanism": {
                "required_groups": list(_REQUIRED_GROUPS),
                "field_hints": field_hints,
                "sequence_candidates": sequence_candidates,
            },
        },
        "unresolved": sorted(unresolved),
        "extension_requirements": sorted(extension_requirements),
    }
    return _AnalysisState(
        data=data,
        unresolved=tuple(sorted(unresolved)),
        extension_requirements=tuple(sorted(extension_requirements)),
        suggested_timestamp_regex=suggested_timestamp,
    )


def _file_evidence(sample: SampleFile) -> _FileEvidence:
    nonempty = tuple(line for line in sample.lines if line.strip())
    timestamp_counts: dict[str, tuple[int, int]] = {}
    for template in _TIMESTAMP_TEMPLATES:
        compiled = re.compile(template.regex)
        matched = sum(1 for line in nonempty if compiled.search(line))
        parseable = sum(1 for line in nonempty if _parseable_timestamp_count(compiled, line))
        timestamp_counts[template.key] = (matched, parseable)
    field_counts: dict[tuple[str, str], int] = {}
    for field_name, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            detector = _field_detector(alias)
            count = sum(1 for line in nonempty if detector.search(line))
            if count:
                field_counts[(field_name, alias)] = count
    sequence_counts = {
        key: sum(1 for line in nonempty if re.search(regex, line))
        for key, regex in _SEQUENCE_TEMPLATES
    }
    return _FileEvidence(
        sample=sample,
        timestamp_counts=timestamp_counts,
        field_counts=field_counts,
        sequence_counts=sequence_counts,
    )


def _field_detector(alias: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?i)(?<![A-Za-z0-9_]){re.escape(alias)}(?![A-Za-z0-9_])"
        rf"(?:\s*[:=]\s*|\s+)"
    )


def _parseable_timestamp_count(pattern: re.Pattern[str], line: str) -> int:
    count = 0
    for match in pattern.finditer(line):
        try:
            timestamp = match.group(1)
            timezone = match.group(2)
            datetime.fromisoformat(timestamp + (timezone or ""))
        except (IndexError, TypeError, ValueError):
            continue
        count += 1
    return count


def _dominant_timestamp(item: _FileEvidence) -> str | None:
    choices = [
        (counts[0], -index, key)
        for index, (key, counts) in enumerate(item.timestamp_counts.items())
        if counts[0]
    ]
    return max(choices)[2] if choices else None


def _aggregate_timestamp_candidates(
    evidence: Sequence[_FileEvidence],
) -> list[dict[str, Any]]:
    eligible = sum(item.sample.nonempty_lines for item in evidence)
    result: list[dict[str, Any]] = []
    for template in _TIMESTAMP_TEMPLATES:
        matched = sum(item.timestamp_counts[template.key][0] for item in evidence)
        parseable = sum(item.timestamp_counts[template.key][1] for item in evidence)
        if not matched:
            continue
        result.append(
            {
                "key": template.key,
                "regex": template.regex,
                "matched_lines": matched,
                "parseable_lines": parseable,
                "eligible_lines": eligible,
                "coverage": round(matched / eligible, 6) if eligible else 0.0,
                "file_ids": [
                    item.sample.file_id
                    for item in evidence
                    if item.timestamp_counts[template.key][0]
                ],
                "runtime_compatible": template.runtime_compatible,
            }
        )
    result.sort(
        key=lambda item: (
            -int(item["runtime_compatible"]),
            -item["parseable_lines"],
            -item["matched_lines"],
            item["key"],
        )
    )
    return result


def _aggregate_field_hints(
    evidence: Sequence[_FileEvidence],
) -> list[dict[str, Any]]:
    eligible = sum(item.sample.nonempty_lines for item in evidence)
    result: list[dict[str, Any]] = []
    for field, aliases in _FIELD_ALIASES.items():
        candidates = []
        for index, alias in enumerate(aliases):
            matched = sum(item.field_counts.get((field, alias), 0) for item in evidence)
            if matched:
                candidates.append((matched, -index, alias))
        if not candidates:
            continue
        matched, _order, alias = max(candidates)
        result.append(
            {
                "field": field,
                "alias": alias,
                "matched_lines": matched,
                "eligible_lines": eligible,
                "coverage": round(matched / eligible, 6) if eligible else 0.0,
                "file_ids": [
                    item.sample.file_id
                    for item in evidence
                    if item.field_counts.get((field, alias), 0)
                ],
            }
        )
    return result


def _aggregate_sequence_candidates(
    evidence: Sequence[_FileEvidence],
) -> list[dict[str, Any]]:
    eligible = sum(item.sample.nonempty_lines for item in evidence)
    result: list[dict[str, Any]] = []
    for key, regex in _SEQUENCE_TEMPLATES:
        matched = sum(item.sequence_counts.get(key, 0) for item in evidence)
        if matched:
            result.append(
                {
                    "key": key,
                    "regex": regex,
                    "matched_lines": matched,
                    "parseable_lines": matched,
                    "eligible_lines": eligible,
                    "coverage": round(matched / eligible, 6) if eligible else 0.0,
                    "file_ids": [
                        item.sample.file_id for item in evidence if item.sequence_counts.get(key, 0)
                    ],
                    "runtime_compatible": True,
                }
            )
    return result


def _best_aliases(item: _FileEvidence) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for field, aliases in _FIELD_ALIASES.items():
        present = [
            (item.field_counts.get((field, alias), 0), -index, alias)
            for index, alias in enumerate(aliases)
            if item.field_counts.get((field, alias), 0)
        ]
        if present:
            result.append((field, max(present)[2]))
    return tuple(result)


def _build_format_families(
    evidence: Sequence[_FileEvidence],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str | None, tuple[tuple[str, str], ...]], list[_FileEvidence]] = {}
    for item in evidence:
        grouped.setdefault((_dominant_timestamp(item), _best_aliases(item)), []).append(item)
    return [
        {
            "family_id": f"family_{index:03d}",
            "file_ids": [item.sample.file_id for item in members],
            "file_patterns": list(_infer_file_patterns(item.sample.name for item in members)),
            "timestamp_key": key[0],
            "field_aliases": dict(key[1]),
        }
        for index, (key, members) in enumerate(grouped.items(), 1)
    ]


def _infer_file_patterns(names: Iterable[str]) -> tuple[str, ...]:
    ordered = list(dict.fromkeys(names))
    grouped: dict[str, tuple[str, list[str]]] = {}
    for name in ordered:
        shape = _filename_shape(name)
        grouped.setdefault(shape.casefold(), (shape, []))[1].append(name)
    patterns: list[str] = []
    for shape, members in grouped.values():
        literal = re.sub(r"[*?]", "", shape)
        if len(members) >= 2 and "*" in shape and literal.strip("._- "):
            patterns.append(shape)
        else:
            patterns.extend(members)
    return tuple(dict.fromkeys(patterns))


def _filename_shape(name: str) -> str:
    shaped = re.sub(r"\d{2,}", "*", name)
    shaped = re.sub(r"(?<=\.)\d+(?=(?:\.gz)?$)", "*", shaped)
    return re.sub(r"\*+", "*", shaped)
