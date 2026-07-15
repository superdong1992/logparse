"""CLI composition root for the current LAN product extensions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from backend.application.configuration import runtime_config
from backend.application.parse_service import ParseService
from backend.application.product_onboarding import ProductOnboardingService
from backend.config_validation import validate_config
from backend.extensions.products.onboarding.current_module1 import (
    CurrentModule1OnboardingAdapter,
)
from backend.extensions.products.current.engine import CurrentProductParseEngine
from backend.extensions.products.current.result_serializer import result_to_dict
from backend.infrastructure.parse_artifact_session import RepositoryArtifactSession
from backend.infrastructure.product_onboarding_candidate import SafeCandidateReader
from backend.infrastructure.product_onboarding_regex import IsolatedRegexSandbox
from backend.infrastructure.product_onboarding_samples import SecureSampleReader


@dataclass(frozen=True, slots=True)
class ParseApplication:
    service: ParseService
    engine: CurrentProductParseEngine


def build_parse_application(raw_config: Mapping[str, Any]) -> ParseApplication:
    engine = CurrentProductParseEngine(runtime_config(raw_config))
    service = ParseService(
        engine,
        artifact_session_factory=RepositoryArtifactSession,
        result_serializer=result_to_dict,
    )
    return ParseApplication(service=service, engine=engine)


def build_product_onboarding_application() -> ProductOnboardingService:
    """Compose the generic use case with one explicit product adapter."""

    return ProductOnboardingService(
        sample_reader=SecureSampleReader(),
        candidate_reader=SafeCandidateReader(),
        sandbox=IsolatedRegexSandbox(),
        adapter=CurrentModule1OnboardingAdapter(),
        fragment_validator=validate_config,
    )
