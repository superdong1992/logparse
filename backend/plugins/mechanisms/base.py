"""Frozen, product-neutral mechanism plugin compatibility interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from backend.contracts.plugins import (
    LegacyMechanismContext,
    MechanismContext,
    MechanismOutcome,
)
from backend.contracts.runtime import Diagnostic, DiagnosticSeverity
from backend.extensions.mechanisms.base import MechanismPlugin


class MechanismModulePlugin(MechanismPlugin, ABC):
    """Bridge legacy ``parse`` plugins to the versioned mechanism contract."""

    requires_legacy_parse_state = True

    @abstractmethod
    def parse(self, state: Any) -> Any | None:
        ...

    def execute(self, context: MechanismContext) -> MechanismOutcome:
        """Execute a legacy plugin through the versioned mechanism contract."""

        if not isinstance(context, LegacyMechanismContext):
            raise TypeError("legacy mechanism requires LegacyMechanismContext")
        state = context.parse_state
        scan_batch = context.scan_batch
        if scan_batch is not None and self.module_key in scan_batch.entries_by_module:
            self.set_precomputed_diagnostic_entries(
                scan_batch.entries_by_module[self.module_key],
                file_count=scan_batch.file_count,
                line_count=scan_batch.line_count,
            )

        errors = getattr(state, "errors", None)
        error_count = len(errors) if isinstance(errors, list) else 0
        self._dependency_results = dict(context.dependency_results)
        try:
            result = self.parse(state)
        finally:
            self._dependency_results = {}
        new_errors = errors[error_count:] if isinstance(errors, list) else []
        diagnostics = tuple(
            Diagnostic(
                code="LP_PLUGIN_DIAGNOSTIC",
                message=str(message),
                severity=DiagnosticSeverity.ERROR,
                stage=f"mechanism.{self.module_key}",
            )
            for message in new_errors
        )
        return MechanismOutcome(result=result, diagnostics=diagnostics)

    def apply_roles(self, state: Any, mechanism_result: Any) -> None:
        pass
