"""Single-JSON CLI adapter for product log onboarding."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Callable, Sequence

from backend.application.product_onboarding import ProductOnboardingService
from backend.contracts.product_onboarding import (
    EXIT_INPUT_ERROR,
    EXIT_POLICY_CONFIRMATION_REQUIRED,
    EXIT_SUCCESS,
    EXIT_TECHNICAL_FAILURE,
    OnboardingError,
    OnboardingInputError,
    OnboardingReport,
    REPORT_CONTRACT,
    REPORT_SCHEMA_VERSION,
)


ServiceFactory = Callable[[], ProductOnboardingService]
_OPERATIONS = {"analyze", "validate", "build-draft"}


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise OnboardingInputError(
            "LP_ONBOARD_CLI_USAGE_INVALID",
            "product-onboarding command arguments are invalid",
        )


def run_product_onboarding(
    argv: Sequence[str],
    *,
    service_factory: ServiceFactory,
) -> int:
    requested_operation = argv[0] if argv else ""
    operation = requested_operation if requested_operation in _OPERATIONS else "unknown"
    try:
        args = _build_parser().parse_args(list(argv))
        service = service_factory()
        if args.operation == "analyze":
            report = service.analyze(args.input_files, encoding=args.encoding)
        elif args.operation == "validate":
            report = service.validate(
                args.input_files,
                args.candidate,
                encoding=args.encoding,
            )
        else:
            report = service.build_draft(
                args.input_files,
                args.candidate,
                encoding=args.encoding,
            )
    except OnboardingError as exc:
        _emit_error(
            operation=operation,
            code=exc.code,
            message=str(exc),
        )
        return EXIT_INPUT_ERROR if isinstance(exc, OnboardingInputError) else EXIT_TECHNICAL_FAILURE
    except Exception:  # noqa: BLE001 - never expose implementation details.
        _emit_error(
            operation=operation,
            code="LP_ONBOARD_INTERNAL_ERROR",
            message="product onboarding failed safely",
        )
        return EXIT_TECHNICAL_FAILURE

    _emit(report.to_dict(), error=False)
    return _exit_code(report)


def _build_parser() -> _JsonArgumentParser:
    parser = _JsonArgumentParser(add_help=False)
    commands = parser.add_subparsers(dest="operation", required=True)
    analyze = commands.add_parser("analyze", add_help=False)
    _add_sample_arguments(analyze)
    validate = commands.add_parser("validate", add_help=False)
    _add_sample_arguments(validate)
    validate.add_argument("--candidate", required=True)
    build = commands.add_parser("build-draft", add_help=False)
    _add_sample_arguments(build)
    build.add_argument("--candidate", required=True)
    return parser


def _add_sample_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", action="append", required=True, dest="input_files")
    parser.add_argument("--encoding", default="utf-8")


def _exit_code(report: OnboardingReport) -> int:
    if report.operation == "build-draft" and report.status == "needs_policy_confirmation":
        return EXIT_POLICY_CONFIRMATION_REQUIRED
    if report.status in {"invalid", "needs_review", "draft_not_built"}:
        return EXIT_TECHNICAL_FAILURE
    return EXIT_SUCCESS


def _emit_error(*, operation: str, code: str, message: str) -> None:
    _emit(
        {
            "contract": REPORT_CONTRACT,
            "schema_version": REPORT_SCHEMA_VERSION,
            "operation": operation,
            "adapter": "",
            "status": "error",
            "diagnostics": [{"code": code, "message": message, "severity": "error"}],
            "final_config_ready": False,
        },
        error=True,
    )


def _emit(payload: dict, *, error: bool) -> None:
    stream = sys.stderr if error else sys.stdout
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        file=stream,
    )
