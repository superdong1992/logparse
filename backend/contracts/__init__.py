"""Stable, product-neutral contracts used by the logparse core."""

from backend.contracts.artifacts import (
    ARTIFACT_CONTRACT_VERSION,
    PARSE_MANIFEST_SCHEMA_VERSION,
    ArtifactIntegrityRecord,
    ManifestStageRecord,
    ManifestStageStatus,
    ParseManifest,
    ParseStatus,
    WorkspaceRecord,
)
from backend.contracts.diagnostics import DiagnosticRecord
from backend.contracts.plugins import (
    DiagnosticScanBatch,
    DiscoveryContext,
    DiscoveryResult,
    MechanismContext,
    MechanismDescriptor,
    MechanismOutcome,
    LegacyMechanismContext,
    PluginCapability,
)
from backend.contracts.runtime import (
    ArtifactRecord,
    Diagnostic,
    DiagnosticSeverity,
    ParseEngineOutcome,
    ParseRequest,
    ParseRun,
    ParseRuntimeOptions,
    StageResult,
    StageStatus,
)
from backend.contracts.scopes import CycleRef, ScopeRef, ScopeSegment

__all__ = [
    "ARTIFACT_CONTRACT_VERSION",
    "PARSE_MANIFEST_SCHEMA_VERSION",
    "ArtifactIntegrityRecord",
    "ArtifactRecord",
    "CycleRef",
    "Diagnostic",
    "DiagnosticRecord",
    "DiagnosticScanBatch",
    "DiagnosticSeverity",
    "DiscoveryContext",
    "DiscoveryResult",
    "ManifestStageRecord",
    "ManifestStageStatus",
    "MechanismContext",
    "MechanismDescriptor",
    "MechanismOutcome",
    "LegacyMechanismContext",
    "ParseEngineOutcome",
    "ParseManifest",
    "ParseStatus",
    "ParseRequest",
    "ParseRun",
    "ParseRuntimeOptions",
    "PluginCapability",
    "ScopeRef",
    "ScopeSegment",
    "StageResult",
    "StageStatus",
    "WorkspaceRecord",
]
