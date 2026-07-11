"""Read-only environment and configuration checks for the ``doctor`` command."""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import os
from pathlib import Path
import sys
from typing import Any, Mapping

from backend.application.configuration import explain_config


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    ok: bool
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [
                {
                    "name": check.name,
                    "ok": check.ok,
                    "message": check.message,
                    "details": dict(check.details),
                }
                for check in self.checks
            ],
        }


def run_doctor(
    raw_config: Mapping[str, Any],
    *,
    product: str | None = None,
    output_root: Path | None = None,
) -> DoctorReport:
    """Run deterministic, read-only readiness checks."""

    checks: list[DoctorCheck] = []
    version_ok = sys.version_info >= (3, 12)
    checks.append(
        DoctorCheck(
            name="python",
            ok=version_ok,
            message=(
                "Python 3.12+ available"
                if version_ok
                else "Python 3.12+ is required"
            ),
            details={"version": sys.version.split()[0]},
        )
    )

    missing_dependencies: list[str] = []
    for module_name in ("click", "pydantic", "yaml"):
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing_dependencies.append(module_name)
    checks.append(
        DoctorCheck(
            name="dependencies",
            ok=not missing_dependencies,
            message=(
                "required Python dependencies available"
                if not missing_dependencies
                else "missing Python dependencies"
            ),
            details={"missing": missing_dependencies},
        )
    )

    try:
        explanation = explain_config(raw_config, product=product)
    except Exception as exc:
        checks.append(
            DoctorCheck(
                name="config",
                ok=False,
                message=str(exc),
            )
        )
    else:
        checks.append(
            DoctorCheck(
                name="config",
                ok=True,
                message="configuration and plugin graph are valid",
                details={
                    "schema_version": explanation["schema_version"],
                    "products": list(explanation["products"]),
                },
            )
        )

    if output_root is not None:
        parent = output_root if output_root.exists() else output_root.parent
        accessible = parent.exists() and os.access(parent, os.W_OK | os.X_OK)
        checks.append(
            DoctorCheck(
                name="output_root",
                ok=accessible,
                message=(
                    "output root is writable"
                    if accessible
                    else "output root parent is not writable"
                ),
                details={"path": str(output_root)},
            )
        )

    return DoctorReport(tuple(checks))

