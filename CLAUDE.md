# CLAUDE.md

This file gives repository guidance to Claude Code and other coding agents.

## Mandatory Rule Preflight

Before repo analysis or code edits, run the local rule preflight for the files
you will inspect or change:

```bash
python scripts/rule_preflight.py --paths backend/parsing/lifecycle_splitter_v3.py
python scripts/rule_preflight.py --changed
```

Read every returned rule source before making claims or edits. In particular:

- `CPU_Id=0` or an empty `cpu_id` is board-level.
- Only non-zero CPU ids create nested CPU lifecycles.
- Module1 always uses lifecycle split V3.
- Module2 reuses module1 lifecycle context and must not add independent lifecycle splitting.

## Current Architecture

The project preprocesses diagnostic log packages into structured evidence for
humans and diagnosis agents. The pipeline is:

```text
archive
-> Decompressor
-> DirectoryDiscoveryPlugin
-> LogParserPlugin
-> mechanism module plugins
-> MechOutputWriter
-> metadata.json + result.json
```

Important boundaries:

- `Decompressor` owns archive extraction. Scanner and parser plugins inspect an already prepared workspace.
- Ordinary `.gz` logs are streamed by parser code. Use `--debug-expand-gz` only for manual full-text debugging.
- `metadata.json` is scan and overview metadata. `result.json` is the compact query index. Do not merge them.
- `ScannerPlugin` and `CompactScannerPlugin` are separate product layouts and should not be collapsed.

## LAN DFX Operating Model

Read `docs/lan-dfx-operating-model.md` before changing diagnosis, query, DFX,
CLI, or output artifacts.

- Codex work often happens outside the LAN with synthetic or mock logs.
- Final execution and real-log diagnosis happen inside the LAN.
- Real logs generally cannot be copied out of the LAN; external handoff should
  be only one line: `ERROR_CODE: 中文结论`.
- Standalone logparse must remain deterministic and must not invoke Claude CLI
  by default.
- GLM5.1 quantized is available in the LAN through Claude CLI, but should only
  consume structured DFX reports and bounded context, not perform broad file
  exploration or path/lifecycle selection.

## Lifecycle Split Contract

Lifecycle splitting is V3-only:

- `Module1Plugin` always uses `LifecycleSplitterV3`.
- Current `lifecycle_split` config supports only:
  - `process_name_mapping`
  - `reliable_processes`
  - `multi_instance_processes`
- Current output lifecycle diagnostics live under `lifecycle_split_result`.
- The only current algorithm value is `interval_v3`.
- V3 lifecycle evidence is:
  - `candidate_segments`
  - `merge_decisions`
  - `lifecycles`
  - `journal_evidence`
  - `issues`
  - `lifecycle_reliable`

Do not reintroduce old lifecycle fields or compatibility paths into current code.
Archived V2 material lives under `docs/archive/lifecycle-v2/`.

## Main Modules

| Path | Responsibility |
| --- | --- |
| `backend/decompressor.py` | Safe recursive archive extraction |
| `backend/pipeline.py` | Product-neutral parse orchestration |
| `backend/plugins/loader.py` | Config-driven plugin loading |
| `backend/plugins/default/scanner.py` | Default `diag/ + varlog/` discovery |
| `backend/plugins/compact/scanner.py` | Compact `boards/ + logs/` discovery |
| `backend/plugins/default/parser.py` | Parser orchestration and shared diagnostic scan |
| `backend/plugins/mechanisms/module1.py` | Module1 scanning, V3 lifecycle split, role signals |
| `backend/plugins/mechanisms/module2.py` | Module2 output derived from module1 lifecycle context |
| `backend/parsing/lifecycle_splitter_v3.py` | Current lifecycle split algorithm |
| `backend/parsing/lifecycle_common.py` | Shared V3 lifecycle config/helpers |
| `backend/parsing/file_iter.py` | Streaming text and extracted-entry iteration |
| `backend/parsing/timestamp_extractor.py` | Timestamp extraction from text lines |
| `backend/parsing/process_name_resolver.py` | Current process name/PID parsing |
| `backend/result_serializer.py` | Compact `result.json` serialization |
| `backend/metadata.py` | `metadata.json` generation |
| `backend/query.py` | Query service over result and metadata files |
| `cli.py` | Human and automation CLI |

## Useful Commands

```bash
python cli.py check-config -c config.yaml
python scripts/rule_preflight.py --changed
python -m pytest tests -q --basetemp output\pytest-tmp-local -p no:cacheprovider
python cli.py parse tests\mock_data\diagnostic_information_20260103.zip -c config.yaml --product default -o output\smoke-default
python cli.py parse tests\mock_data_compact\compact_package_20260103.zip -c config.yaml --product compact -o output\smoke-compact
```

The `tests/mock_data*` packages and their generator scripts are demo/smoke assets.
They are useful for end-to-end manual checks, but unit tests should build their
own focused fixtures unless they explicitly need a full package.

## Testing Notes

Keep tests aligned with current contracts:

- `tests/test_lifecycle_splitter_v3.py` is the lifecycle algorithm contract.
- `tests/test_module1_plugin.py` verifies module1 always produces V3 lifecycle output.
- `tests/test_config_validation.py` should reject old lifecycle fields.
- `tests/test_cli.py` should show only V3 lifecycle DFX for current output.
- `tests/test_query.py` should verify current query fields flow through serializer -> query -> CLI.

When a refactor touches lifecycle code, check serializer, query, CLI, metadata,
docs, and tests in the same pass.
