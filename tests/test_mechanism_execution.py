from __future__ import annotations

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
        object(),
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
