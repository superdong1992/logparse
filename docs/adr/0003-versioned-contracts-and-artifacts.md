# ADR-0003: Version Plugin, Configuration, and Artifact Contracts

- Status: Accepted
- Date: 2026-07-11

## Context

Implicit plugin order, loosely validated configuration, duplicated artifact
fields, and paths owned by multiple writers made compatibility difficult to
reason about and failures easy to misread as empty results.

## Decision

- Plugins declare an API version, capabilities, configuration schema, and
  dependencies. Dependencies are topologically validated before scanning.
- Configuration uses an explicit schema version and deterministic migration.
- `ArtifactLayout` owns paths and `ArtifactRepository` performs atomic writes.
- `parse_manifest.json` records run status, stages, artifact versions/hashes,
  counts, diagnostics, and workspace retention.
- `metadata.json` is discovery/scan coverage; `result.json` is a compact query
  index; `mech_modules/` is evidence.
- Unknown schema versions fail with stable errors instead of appearing empty.
- Existing issue-locator CLI entrypoints and current-product projections remain
  compatibility contracts during migration.

## Consequences

- Consumers can validate integrity and reject unsupported output explicitly.
- Plugin dependencies no longer depend on YAML order.
- Full/raw result modes and long-lived extraction paths are not supported.
- Contract changes are red and require migration and rollback planning.
