"""CLI composition root for the current LAN product extensions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from backend.application.configuration import runtime_config
from backend.application.parse_service import ParseService
from backend.extensions.products.current.engine import CurrentProductParseEngine
from backend.extensions.products.current.result_serializer import result_to_dict
from backend.infrastructure.parse_artifact_session import RepositoryArtifactSession


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
