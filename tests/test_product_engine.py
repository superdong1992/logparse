"""直接产品引擎的解压、配置隔离、故障和工作区回归。"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from unittest.mock import Mock
import zipfile

import pytest

from backend.application.configuration import load_config_file
from backend.contracts.runtime import Diagnostic, ParseEngineError, ParseRequest, ParseRuntimeOptions, StageStatus
from backend.extensions.products.current.engine import CurrentProductParseEngine
from backend.extensions.products.current.models import MechResult

ROOT = Path(__file__).parents[1]


class Discovery:
    def discover(self, workspace):
        return [], []


class Parser:
    last_mechanism_diagnostics = ()

    def parse(self, result):
        return result

    def write_output(self, result, task_dir):
        return task_dir


def make_engine(monkeypatch, config=None, *, discovery=None, parser=None):
    engine = CurrentProductParseEngine(config or load_config_file(ROOT / "config.yaml"))
    monkeypatch.setattr(engine, "_load_plugins", lambda *_args: (discovery or Discovery(), parser or Parser()))
    return engine


def package(tmp_path):
    path = tmp_path / "logs.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("diag/slot_1/data.log", "2026-01-03T00:00:00 test\n")
    return path


@pytest.mark.parametrize("expand", [False, True])
def test_gzip_expansion_is_explicit(tmp_path, monkeypatch, expand):
    source = tmp_path / "logs.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("varlog/slot_1/journal.log.gz", gzip.compress(b"readable journal\n"))
    engine = make_engine(monkeypatch)
    outcome = engine.execute(ParseRequest(source, tmp_path / "out", options=ParseRuntimeOptions(keep_workspace=True, debug_expand_gz=expand)))
    root = outcome.workspace / "varlog/slot_1"
    assert (root / "journal.log.gz").is_file()
    assert (root / "journal.log").exists() is expand
    if expand:
        assert (root / "journal.log").read_text() == "readable journal\n"


def test_single_file_is_copied_before_discovery(tmp_path, monkeypatch):
    source = tmp_path / "single.log"
    source.write_text("one line\n", encoding="utf-8")
    seen = []
    discovery = Discovery()
    def discover(root):
        seen.append((root / source.name).read_text())
        return [], []
    discovery.discover = discover
    outcome = make_engine(monkeypatch, discovery=discovery).execute(ParseRequest(source, tmp_path / "out"))
    assert seen == ["one line\n"]
    assert outcome.workspace is None
    assert not Path(outcome.result.extracted_root).exists()


@pytest.mark.parametrize("reverse", [False, True])
def test_product_archive_options_are_isolated_and_order_independent(tmp_path, monkeypatch, reverse):
    config = load_config_file(ROOT / "config.yaml")
    config["products"]["default"]["archive"] = {"recursive_extraction": False, "compressed_extensions": [".zip"]}
    config["products"]["compact"]["archive"] = {"recursive_extraction": True, "compressed_extensions": [".tar"]}
    if reverse:
        config["products"] = dict(reversed(list(config["products"].items())))
    captured = []
    class Extractor:
        def __init__(self, compressed_extensions):
            self.extensions = compressed_extensions
        def is_compressed(self, name):
            return True
        def extract_all(self, source, workspace, **options):
            captured.append((self.extensions, options))
            return []
    monkeypatch.setattr("backend.extensions.products.current.engine.Decompressor", Extractor)
    engine = make_engine(monkeypatch, config)
    source = tmp_path / "input.tar"
    source.touch()
    for index, product in enumerate(("compact", "default", "compact")):
        engine.execute(ParseRequest(source, tmp_path / str(index), product=product, options=ParseRuntimeOptions(extraction_workers=3)))
    assert [item[0] for item in captured] == [[".tar"], [".zip"], [".tar"]]
    assert [item[1]["recursive"] for item in captured] == [True, False, True]
    assert all(item[1]["workers"] == 3 for item in captured)
    assert engine.config == config


def test_runtime_options_can_override_configured_true_with_false(tmp_path, monkeypatch):
    config = load_config_file(ROOT / "config.yaml")
    config["pipeline"]["keep_workspace"] = True
    engine = make_engine(monkeypatch, config)
    source = package(tmp_path)
    kept = engine.execute(ParseRequest(source, tmp_path / "retained"))
    removed = engine.execute(ParseRequest(source, tmp_path / "temporary", options=ParseRuntimeOptions(keep_workspace=False)))
    assert kept.workspace.is_dir()
    assert removed.workspace is None
    assert not Path(removed.result.extracted_root).exists()


def test_profile_has_stages_without_log_content(tmp_path, monkeypatch):
    outcome = make_engine(monkeypatch).execute(ParseRequest(package(tmp_path), tmp_path / "out", options=ParseRuntimeOptions(profile=True)))
    report = json.loads((tmp_path / "out" / outcome.result.task_id / "performance.json").read_text())
    assert report["schema_version"] == 1
    assert {stage["name"] for stage in report["stages"]} >= {"pipeline.extract", "pipeline.discovery", "pipeline.parse"}
    assert "readable journal" not in json.dumps(report)


@pytest.mark.parametrize("failed_stage,code", [
    ("extract", "LP_EXTRACTION_FAILED"),
    ("discovery", "LP_DISCOVERY_FAILED"),
    ("parse", "LP_PARSE_FAILED"),
    ("write_output", "LP_EVIDENCE_WRITE_FAILED"),
    ("metadata", "LP_METADATA_WRITE_FAILED"),
])
def test_fatal_stage_has_structured_diagnostic_and_cleans_workspace(tmp_path, monkeypatch, failed_stage, code):
    discovery, parser = Discovery(), Parser()
    engine = make_engine(monkeypatch, discovery=discovery, parser=parser)
    fail = Mock(side_effect=OSError("synthetic failure"))
    if failed_stage == "extract":
        monkeypatch.setattr(engine, "_extract", fail)
    elif failed_stage == "discovery":
        discovery.discover = fail
    elif failed_stage == "parse":
        parser.parse = fail
    elif failed_stage == "write_output":
        def parse(result):
            result.mech_results.append(MechResult(module_name="TEST", module_key="test"))
        parser.parse = parse
        parser.write_output = fail
    else:
        monkeypatch.setattr("backend.extensions.products.current.engine.MetadataGenerator.generate", fail)
    with pytest.raises(ParseEngineError) as captured:
        engine.execute(ParseRequest(package(tmp_path), tmp_path / "out"))
    assert captured.value.diagnostics[-1].code == code
    assert captured.value.stages[-1].status == StageStatus.FAILED
    assert captured.value.stages[-1].name == "pipeline." + failed_stage
    assert not (tmp_path / "out/logs/extracted").exists()


def test_mechanism_error_is_partial_and_structured(tmp_path, monkeypatch):
    parser = Parser()
    parser.last_mechanism_diagnostics = (Diagnostic("LP_MECHANISM_FAILED", "合成机制错误", stage="mechanism.test"),)
    outcome = make_engine(monkeypatch, parser=parser).execute(ParseRequest(package(tmp_path), tmp_path / "out"))
    assert outcome.status == "partial"
    assert outcome.diagnostics[0].code == "LP_MECHANISM_FAILED"
    assert outcome.stages[-1].status == StageStatus.SUCCEEDED


def test_unknown_product_is_rejected_before_reading_input(tmp_path):
    engine = CurrentProductParseEngine(load_config_file(ROOT / "config.yaml"))
    with pytest.raises(ParseEngineError) as captured:
        engine.execute(ParseRequest(tmp_path / "missing", tmp_path / "out", product="unknown"))
    assert captured.value.diagnostics[0].code == "LP_CONFIG_INVALID"
