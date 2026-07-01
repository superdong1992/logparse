# LAN DFX Operating Model

This repository is designed and partly tested outside the LAN with Codex, while
final execution and real-log diagnosis happen inside the LAN. Treat that split
as a product constraint, not an environment accident.

## Runtime Split

- Non-LAN: use Codex for design, implementation, unit tests, mock fixtures, and
  deterministic artifact review.
- LAN: run real-log parsing, target diagnosis, and optional deep DFX. Real logs
  generally cannot be copied out.
- External handoff back to non-LAN Codex should be only one line:
  `ERROR_CODE: 中文结论`.

## logparse Responsibility

`logparse` is deterministic DFX infrastructure. Standalone logparse must not
invoke Claude CLI by default.

- Generate structured artifacts from `output/{task_id}`.
- Prefer stable error codes and explicit diagnostics over prose-only failures.
- Keep `result.json`, `metadata.json`, `performance.json`, and `mech_modules`
  responsibilities separate.
- Default DFX must avoid reading log bodies. Deep DFX may read only selected,
  bounded target windows inside the LAN.
- One-line summaries must not quote raw log text.

## issue-locator Responsibility

`issue-locator` is the Claude CLI orchestration layer. It may call logparse DFX
for failed, stalled, invalid-zip, or manual diagnosis flows, then optionally ask
Claude CLI with GLM5.1 quantized to compress deterministic DFX into one line.

Do not move Claude CLI orchestration into standalone logparse unless the project
explicitly changes this boundary.

## GLM5.1 Boundary

GLM5.1 quantized should receive small, already-selected context:

- `dfx_report.json`
- small files under `dfx_context/`
- bounded deep windows selected by deterministic target resolution

Do not ask GLM5.1 to parse directory trees, choose lifecycle/cycle, assemble
paths, inspect broad logs, or decide target resolution from raw packages.

## Coding Bias

Optimize future changes for fast localization:

- stable error codes
- deterministic JSON reports
- transparent target-resolution diagnostics
- bounded context windows
- debug bundles that exclude uploads and full log bodies
- summaries shaped as `ERROR_CODE: 中文结论`
