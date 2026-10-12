from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.application.mechanism_execution import MechanismExecutionService
from backend.contracts.plugins import (
    DiagnosticScanBatch,
    MechanismContext,
    MechanismOutcome,
)
from backend.contracts.runtime import Diagnostic, DiagnosticSeverity
from backend.extensions.mechanisms.base import MechanismPlugin


class NativePlugin(MechanismPlugin):
    def execute(self, context: MechanismContext) -> MechanismOutcome:
        assert not hasattr(context, "parse_state")
        assert context.extension_input == {"bounded": True}
        return MechanismOutcome(
            result={"result": "ok"},
            role_signals={"scope:1": "active"},
            diagnostics=(
                Diagnostic(
                    code="LP_NATIVE_NOTE",
                    message="structured note",
                    severity=DiagnosticSeverity.INFO,
                    stage="mechanism.native",
                ),
            ),
        )


def test_native_plugin_receives_no_mutable_parse_state_and_returns_full_outcome() -> None:
    runtime = MechanismExecutionService({}, timestamp_extractor=None)
    plugin = NativePlugin({"module_name": "native"}, module_key="native")
    accepted = []
    errors = []

    outcomes = runtime.execute(
        {"bounded": True},
        [plugin],
        DiagnosticScanBatch(),
        error_sink=errors.append,
        outcome_sink=lambda mechanism, outcome: accepted.append(
            (mechanism, outcome)
        ),
    )

    assert outcomes[0][1].diagnostics[0].code == "LP_NATIVE_NOTE"
    assert outcomes[0][1].role_signals == {"scope:1": "active"}
    assert accepted == [outcomes[0]]
    assert errors == []


@pytest.mark.parametrize("failure", ["exception", "diagnostic", "empty", "invalid_status"])
def test_failed_or_empty_dependency_skips_dependents_and_keeps_independent(failure):
    executed = []

    class Plugin(MechanismPlugin):
        def execute(self, context):
            executed.append(self.module_key)
            if self.module_key != "upstream":
                return MechanismOutcome(result={"key": self.module_key})
            if failure == "exception":
                raise RuntimeError("cannot parse")
            if failure == "diagnostic":
                return MechanismOutcome(
                    result={"unusable": True},
                    diagnostics=(Diagnostic(code="LP_INVALID", message="invalid"),),
                )
            if failure == "invalid_status":
                return MechanismOutcome(result={"unusable": True}, status="unknown")
            return MechanismOutcome()

    plugins = [
        Plugin({}, module_key="upstream"),
        Plugin({}, module_key="dependent", dependencies=("upstream",)),
        Plugin({}, module_key="transitive", dependencies=("dependent",)),
        Plugin({}, module_key="independent"),
    ]
    accepted, errors = [], []
    logger = Mock()
    outcomes = MechanismExecutionService({}, timestamp_extractor=None, logger=logger).execute(
        {}, plugins, DiagnosticScanBatch(),
        error_sink=errors.append,
        outcome_sink=lambda plugin, outcome: accepted.append(plugin.module_key),
    )
    by_key = {plugin.module_key: outcome for plugin, outcome in outcomes}

    assert executed == ["upstream", "independent"]
    assert accepted == ["independent"]
    upstream_status = "empty" if failure == "empty" else "failed"
    assert by_key["upstream"].status == upstream_status
    assert by_key["dependent"].status == "skipped"
    assert by_key["transitive"].status == "skipped"
    assert by_key["independent"].status == "success"
    assert by_key["dependent"].diagnostics[0].code == "LP_MECHANISM_DEPENDENCY_SKIPPED"
    assert by_key["dependent"].diagnostics[0].details == {
        "dependencies": {"upstream": upstream_status},
    }
    assert [call.args[1] for call in logger.info.call_args_list] == [
        plugin.module_key for plugin in plugins
    ]
    if failure in {"exception", "invalid_status"}:
        assert by_key["upstream"].diagnostics[0].code == "LP_MECHANISM_FAILED"
        assert by_key["upstream"].result is None
        assert "result=error" in logger.info.call_args_list[0].args[0]
        assert len(errors) == 3


@pytest.mark.parametrize("workers", [1, 2])
def test_scan_keeps_partial_file_results_and_continues_other_files(workers):
    class Source:
        scope_value = "scope"
        payload = None

        def __init__(self, key, fail):
            self.key, self.fail, self.values = key, fail, []

        def iter_lines(self):
            yield self.key
            if self.fail:
                raise PermissionError("read interrupted")

        def timestamps(self):
            return self.values

        def replace_timestamps(self, values):
            self.values = values

    plugin = SimpleNamespace(
        module_key="native",
        build_diagnostic_line_scanner=lambda: lambda line, payload, scope: line,
    )
    runtime = MechanismExecutionService(
        {}, workers=workers,
        timestamp_extractor=SimpleNamespace(extract_from_text=lambda line: []),
    )
    errors = []
    batch = runtime.scan(
        [Source("broken", True), Source("healthy", False)],
        [plugin], error_sink=errors.append,
    )

    assert batch.entries_by_module == {"native": ("broken", "healthy")}
    assert (batch.file_count, batch.line_count) == (2, 2)
    assert len(batch.diagnostics) == len(errors) == 1
    assert batch.diagnostics[0].code == "LP_DIAGNOSTIC_READ_FAILED"
    assert batch.diagnostics[0].details == {"source": "broken", "lines_read": 1}


@pytest.mark.parametrize("kind", ["missing", "corrupt_gzip", "permission"])
def test_file_iterator_reports_read_errors(tmp_path, monkeypatch, kind):
    from pathlib import Path
    from backend.parsing.file_iter import iter_log_entry_lines
    from backend.extensions.products.current.models import LogEntry

    path = tmp_path / ("bad.gz" if kind == "corrupt_gzip" else "missing.log")
    if kind == "corrupt_gzip":
        path.write_bytes(b"invalid gzip data")
    if kind == "permission":
        def denied(*args, **kwargs):
            raise PermissionError("access denied")
        monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(OSError):
        list(iter_log_entry_lines(LogEntry(path=str(path), name=path.name)))


def test_explicit_partial_result_remains_available_to_dependencies():
    partial_result = {"entries": ["readable prefix"]}

    class Plugin(MechanismPlugin):
        def execute(self, context):
            if self.module_key == "upstream":
                return MechanismOutcome(
                    result=partial_result,
                    status="partial",
                    diagnostics=(Diagnostic(code="LP_READ_FAILED", message="read interrupted"),),
                )
            assert context.dependency_results["upstream"] is partial_result
            return MechanismOutcome(result={"correlated": True})

    plugins = [
        Plugin({}, module_key="upstream"),
        Plugin({}, module_key="dependent", dependencies=("upstream",)),
    ]
    accepted, errors = [], []
    outcomes = MechanismExecutionService({}, timestamp_extractor=None).execute(
        {}, plugins, DiagnosticScanBatch(), error_sink=errors.append,
        outcome_sink=lambda plugin, outcome: accepted.append(plugin.module_key),
    )

    assert [outcome.status for _, outcome in outcomes] == ["partial", "success"]
    assert accepted == ["upstream", "dependent"]
    assert errors == ["read interrupted"]


@pytest.mark.parametrize(("status", "expected_code"), [
    ("failed", "LP_MECHANISM_FAILED"),
    ("partial", "LP_MECHANISM_PARTIAL"),
    ("skipped", "LP_MECHANISM_DEPENDENCY_SKIPPED"),
    ("empty", None),
])
def test_explicit_outcome_status_is_visible_in_diagnostics_and_controls_dependencies(
    status, expected_code,
):
    result = {"entries": ["available data"]}
    note = Diagnostic(code="LP_NOTE", message="plugin note", severity=DiagnosticSeverity.INFO)

    class Plugin(MechanismPlugin):
        def execute(self, context):
            if self.module_key == "upstream":
                return MechanismOutcome(
                    status=status,
                    result=None if status == "empty" else result,
                    diagnostics=(note,),
                )
            if self.module_key == "dependent":
                assert context.dependency_results == {"upstream": result}
            return MechanismOutcome(result={"key": self.module_key})

    plugins = [
        Plugin({}, module_key="upstream"),
        Plugin({}, module_key="dependent", dependencies=("upstream",)),
        Plugin({}, module_key="independent"),
    ]
    accepted, errors = [], []
    outcomes = MechanismExecutionService({}, timestamp_extractor=None).execute(
        {}, plugins, DiagnosticScanBatch(), error_sink=errors.append,
        outcome_sink=lambda plugin, outcome: accepted.append(plugin.module_key),
    )
    upstream = outcomes[0][1]
    assert upstream.status == status
    assert upstream.diagnostics[0] is note
    if expected_code is None:
        assert upstream.diagnostics == (note,)
    else:
        error = upstream.diagnostics[1]
        assert error.code == expected_code
        assert error.severity == DiagnosticSeverity.ERROR
        assert error.details == {"reported_status": status}
        assert error.message in errors
        if status == "skipped":
            assert "主动跳过" in error.message
    if status == "partial":
        assert accepted == ["upstream", "dependent", "independent"]
        assert outcomes[1][1].status == "success"
    else:
        assert accepted == ["independent"]
        assert outcomes[1][1].status == "skipped"
        assert outcomes[1][1].diagnostics[0].details == {
            "dependencies": {"upstream": status},
        }
