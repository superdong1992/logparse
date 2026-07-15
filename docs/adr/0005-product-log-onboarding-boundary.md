# ADR-0005: Separate Product Log Onboarding Across Red and Green Boundaries

- Status: Accepted
- Date: 2026-07-15

## Context

Onboarding a new log product starts from one or more explicit sample files.  A
future AI agent needs bounded format evidence, a way to replay candidate
patterns, and a technically valid schema-v2 product fragment.  The first local
prototype placed sample-file access, JSON parsing, subprocess isolation,
product field names, configuration projection, and a module CLI in one green
package.  That made security and public compatibility look like routine
product-format knowledge and allowed generic execution code to depend on the
current product.

This change intentionally crosses the red boundary.  The user explicitly
approved the architecture-appropriate red/green split in Codex on 2026-07-15.
No yellow lifecycle or correlation policy is changed.

## Decision

Introduce a read-only `product-onboarding` use case with this ownership:

- Red contracts define versioned sample, candidate, replay, report, diagnostic,
  and generic capture-probe DTOs.  Raw lines are in-process fields with no
  public serializer and are excluded from representations.
- Red ports define the sample reader, candidate reader, regex sandbox, and
  product onboarding adapter interfaces.
- Red application code coordinates the three stages and validates a projected
  fragment inside a schema-v2 root.  It imports no product extension.
- Red infrastructure accepts only explicitly named regular files, applies
  deterministic sampling budgets, streams ordinary gzip files, bounds candidate
  JSON, statically rejects unsafe regex shapes, and executes regex replay in an
  isolated pure-stdlib worker with a hard timeout.
- The red regex worker receives declarative required captures and generic
  probes.  It returns aggregate counts only and contains no current-product
  field, topology, process, or lifecycle knowledge.
- The green `current_module1` adapter owns timestamp templates, field aliases,
  rotation/file-pattern inference, current runtime capture requirements,
  candidate interpretation, product policy caveats, and the schema-v2 product
  fragment projection.  It does not read paths, launch processes, define a
  CLI, or persist configuration.
- `backend/presentation/cli/composition.py` is the sole composition boundary
  that imports both generic implementations and the concrete green adapter.

The stable public interface is the root CLI:

```text
python cli.py product-onboarding analyze ...
python cli.py product-onboarding validate ...
python cli.py product-onboarding build-draft ...
```

It emits at most one JSON document to each stream.  Reports never include raw
log text, the candidate body, or absolute paths.  Exit codes are `0` for a
successful stage, `2` for input/document errors, `3` for technical candidate or
execution failure, and `4` when a usable draft must wait for policy
confirmation.

The root CLI and JSON schema are the only compatibility promise.  Internal
Python services and the green adapter are not public APIs.  The uncommitted
green module CLI, worker, and mixed analyzer are removed without a facade.

The command never invokes a model, traverses a directory, extracts an archive
container, writes YAML, modifies `config.yaml`, enables a product, or writes a
parse artifact.  It may produce a JSON draft only.  Lifecycle, role/topology,
numeric suffix, processor identity, active-period, and corpus-performance
decisions remain unresolved until a separately governed change has evidence.

## Security Boundary

- Inputs are explicit regular files only; directories, symbolic links, archive
  containers, duplicate paths, and case-insensitive duplicate basenames fail
  closed.
- File count, lines per file, characters per line, and total sampled characters
  are bounded.  Decode replacement, binary markers, truncation, unsupported
  timestamps, non-runtime encodings, and non-normalized gzip names remain
  visible only as aggregate adapter requirements.
- Candidate JSON has byte, depth, node, string, type, version, and duplicate-key
  limits.
- Candidate regexes have length, nesting, capture, opcode, assertion,
  backreference, zero-width, and repeat-layout checks before isolated replay.
  The timeout protects onboarding replay only; full LAN corpus validation is
  still required before configuration persistence.

## Consequences

- Future agents can follow one framework-neutral Markdown workflow and a stable
  machine interface while product knowledge remains replaceable.
- Security and compatibility changes remain visible as red changes instead of
  being hidden under a green directory.
- Existing `parse`, `mech-target-logs`, `dfx-output`, configuration, artifact,
  and issue-locator contracts are unchanged.
- No data, artifact, or configuration migration is introduced.

## Rollback

Remove the root `product-onboarding` command registration to disable the
feature immediately, then revert the onboarding presentation, application,
ports, contracts, infrastructure, and green adapter files.  Because the use
case is read-only and never persists configuration or artifacts, rollback has
no data cleanup or migration step.

