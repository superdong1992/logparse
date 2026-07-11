"""Tests for backend/pipeline.py."""
from __future__ import annotations

import gzip
import json
import zipfile

import pytest

from backend.pipeline import Pipeline, PipelineStageError


class TestGzExpansionGate:
    def test_run_skips_plain_gz_expansion_by_default(self, tmp_path):
        gz_path = tmp_path / "journal.log.1.gz"
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            f.write("searchable journal\n")

        package = tmp_path / "package.zip"
        with zipfile.ZipFile(package, "w") as zf:
            zf.write(gz_path, "varlog/slot_1/journal.log.1.gz")

        pipeline = Pipeline({"pipeline": {"recursive_extraction": True}})
        pipeline._plugin_cache["default"] = (_FakeDiscovery(), _FakeParser())

        pipeline.run(
            package,
            tmp_path / "out",
            task_id="task",
            keep_workspace=True,
        )

        extract_dir = tmp_path / "out" / "task" / "extracted" / "varlog" / "slot_1"
        assert (extract_dir / "journal.log.1.gz").exists()
        assert not (extract_dir / "journal.log.1").exists()
        assert not (extract_dir / "journal.log.1.gz_extracted").exists()

    def test_run_expands_plain_gz_when_configured(self, tmp_path):
        gz_path = tmp_path / "journal.log.1.gz"
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            f.write("searchable journal\n")

        package = tmp_path / "package.zip"
        with zipfile.ZipFile(package, "w") as zf:
            zf.write(gz_path, "varlog/slot_1/journal.log.1.gz")

        pipeline = Pipeline({"pipeline": {"recursive_extraction": True, "debug_expand_gz": True}})
        pipeline._plugin_cache["default"] = (_FakeDiscovery(), _FakeParser())

        pipeline.run(
            package,
            tmp_path / "out",
            task_id="task",
            keep_workspace=True,
        )

        extract_dir = tmp_path / "out" / "task" / "extracted" / "varlog" / "slot_1"
        assert (extract_dir / "journal.log.1.gz").exists()
        assert (extract_dir / "journal.log.1").read_text(encoding="utf-8") == "searchable journal\n"
        assert not (extract_dir / "journal.log.1.gz_extracted").exists()


class TestSingleFileInput:
    def test_run_copies_uncompressed_single_file_into_extracted_workspace(self, tmp_path):
        source = tmp_path / "single_diag.log"
        source.write_text("2026-01-03T00:00:00 EXAMPLE single file\n", encoding="utf-8")

        seen = {}

        class FakeDiscovery:
            def discover(self, extract_dir):
                copied = extract_dir / "single_diag.log"
                seen["copied_exists"] = copied.exists()
                seen["copied_text"] = copied.read_text(encoding="utf-8") if copied.exists() else ""
                return [], []

        pipeline = Pipeline({"pipeline": {"recursive_extraction": True}})
        pipeline._plugin_cache["default"] = (FakeDiscovery(), _FakeParser())

        pipeline.run(source, tmp_path / "out", task_id="task")

        assert seen == {
            "copied_exists": True,
            "copied_text": "2026-01-03T00:00:00 EXAMPLE single file\n",
        }


class _FakeDiscovery:
    def discover(self, extract_dir):
        return [], []


class _FakeParser:
    performance_recorder = None

    def parse(self, result):
        return result

    def write_output(self, mech_result, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir


class TestPipelineProfile:
    def test_run_passes_resolved_extraction_workers(self, tmp_path):
        seen = {}

        class FakeDecompressor:
            def extract_all(self, source, dest_dir, recursive=True, expand_gz=False, workers=1):
                seen["workers"] = workers
                dest_dir.mkdir(parents=True, exist_ok=True)
                return []

            def is_compressed(self, name):
                return name.endswith(".zip")

        package = tmp_path / "package.zip"
        package.write_text("placeholder", encoding="utf-8")
        pipeline = Pipeline({"pipeline": {"extraction_workers": 3}})
        pipeline.decompressor = FakeDecompressor()
        pipeline._plugin_cache["default"] = (_FakeDiscovery(), _FakeParser())

        pipeline.run(package, tmp_path / "out", task_id="task")

        assert seen["workers"] == 3

    def test_run_profile_writes_sanitized_performance_json(self, tmp_path):
        package = tmp_path / "package.zip"
        with zipfile.ZipFile(package, "w") as zf:
            zf.writestr("diag/slot_1/diag.zip", "not-a-real-inner-zip")

        pipeline = Pipeline(
            {
                "pipeline": {
                    "recursive_extraction": False,
                    "debug_expand_gz": False,
                    "extraction_workers": 1,
                    "diagnostic_scan_workers": 1,
                }
            }
        )
        pipeline._plugin_cache["default"] = (_FakeDiscovery(), _FakeParser())

        pipeline.run(package, tmp_path / "out", task_id="task", profile=True)

        perf_path = tmp_path / "out" / "task" / "performance.json"
        data = json.loads(perf_path.read_text(encoding="utf-8"))
        payload = json.dumps(data, ensure_ascii=False)

        assert data["schema_version"] == 1
        assert data["config"]["debug_expand_gz"] is False
        assert any(stage["name"] == "pipeline.extract" for stage in data["stages"])
        assert any(stage["name"] == "pipeline.discovery" for stage in data["stages"])
        assert any(stage["name"] == "pipeline.parse" for stage in data["stages"])
        assert "raw" not in payload.lower()
        assert "context" not in payload.lower()


class TestUnifiedExtractionBoundary:
    def test_pipeline_has_no_middle_inner_extraction_stage(self):
        assert not hasattr(Pipeline, "_extract_inner_contents")

    def test_run_does_not_perform_middle_inner_extraction(self, tmp_path):
        class FakeDecompressor:
            def extract_all(self, source, dest_dir, recursive=True, expand_gz=False):
                dest_dir.mkdir(parents=True, exist_ok=True)
                return []

        class FakeDiscovery:
            def discover(self, extract_dir):
                return [], []

        class FakeParser:
            def parse(self, result):
                return result

        pipeline = Pipeline({"pipeline": {"inner_extraction": True}})
        pipeline.decompressor = FakeDecompressor()
        pipeline._plugin_cache["default"] = (FakeDiscovery(), FakeParser())

        pipeline.run(tmp_path / "package.zip", tmp_path / "out")


class TestFatalStagePolicy:
    def test_extraction_failure_is_fatal(self, tmp_path):
        class BrokenDecompressor:
            def extract_all(self, *args, **kwargs):
                raise OSError("unsafe archive")

            def is_compressed(self, name):
                return name.endswith(".zip")

        pipeline = Pipeline({"pipeline": {}})
        pipeline.decompressor = BrokenDecompressor()
        pipeline._plugin_cache["default"] = (_FakeDiscovery(), _FakeParser())

        with pytest.raises(PipelineStageError) as caught:
            pipeline.run(tmp_path / "package.zip", tmp_path / "out")

        assert caught.value.stage_name == "pipeline.extract"
        assert pipeline.stage_records[-1]["success"] is False

    def test_discovery_failure_is_fatal(self, tmp_path):
        class BrokenDiscovery:
            def discover(self, _extract_dir):
                raise ValueError("unknown layout")

        pipeline = Pipeline({"pipeline": {}})
        pipeline._plugin_cache["default"] = (BrokenDiscovery(), _FakeParser())
        package = tmp_path / "package.zip"
        with zipfile.ZipFile(package, "w"):
            pass

        with pytest.raises(PipelineStageError) as caught:
            pipeline.run(package, tmp_path / "out")

        assert caught.value.stage_name == "pipeline.discovery"
        assert pipeline.stage_records[-1]["success"] is False


class TestPipelineCleanup:
    def test_default_workspace_is_temporary(self, tmp_path):
        pipeline = Pipeline({"pipeline": {}})
        pipeline._plugin_cache["default"] = (_FakeDiscovery(), _FakeParser())
        package = tmp_path / "package.zip"
        with zipfile.ZipFile(package, "w"):
            pass

        pipeline.run(package, tmp_path / "out", task_id="task")

        assert pipeline.last_workspace is None
        assert not (tmp_path / "out" / "task" / "extracted").exists()

    def test_keep_workspace_is_not_overridden_by_legacy_cleanup_flag(self, tmp_path):
        class FakeDecompressor:
            def extract_all(self, source, dest_dir, recursive=True, expand_gz=False):
                dest_dir.mkdir(parents=True, exist_ok=True)
                (dest_dir / "diag.zip").write_text("archive", encoding="utf-8")
                return []

        class FakeDiscovery:
            def discover(self, extract_dir):
                return [], []

        class FakeParser:
            def parse(self, result):
                return result

        pipeline = Pipeline({"pipeline": {"cleanup_extracted": True}})
        pipeline.decompressor = FakeDecompressor()
        pipeline._plugin_cache["default"] = (FakeDiscovery(), FakeParser())

        pipeline.run(
            tmp_path / "package.zip",
            tmp_path / "out",
            task_id="task",
            keep_workspace=True,
        )

        assert (tmp_path / "out" / "task" / "extracted").exists()

    def test_cleanup_inner_archives_keeps_extracted_workspace(self, tmp_path):
        class FakeDecompressor:
            def extract_all(self, source, dest_dir, recursive=True, expand_gz=False):
                slot_dir = dest_dir / "diag" / "slot_1"
                slot_dir.mkdir(parents=True, exist_ok=True)
                (slot_dir / "diag.zip").write_text("archive", encoding="utf-8")
                (slot_dir / "diag.zip_extracted").mkdir()
                (slot_dir / "diag.zip_extracted" / "inner.log").write_text("log", encoding="utf-8")
                (slot_dir / "unexpanded.zip").write_text("keep", encoding="utf-8")
                return []

            def is_compressed(self, name):
                return name.endswith(".zip")

        class FakeDiscovery:
            def discover(self, extract_dir):
                return [], []

        class FakeParser:
            def parse(self, result):
                return result

        pipeline = Pipeline({"pipeline": {"cleanup_inner_archives": True}})
        pipeline.decompressor = FakeDecompressor()
        pipeline._plugin_cache["default"] = (FakeDiscovery(), FakeParser())

        pipeline.run(
            tmp_path / "package.zip",
            tmp_path / "out",
            task_id="task",
            keep_workspace=True,
        )

        slot_dir = tmp_path / "out" / "task" / "extracted" / "diag" / "slot_1"
        assert slot_dir.exists()
        assert not (slot_dir / "diag.zip").exists()
        assert (slot_dir / "diag.zip_extracted" / "inner.log").exists()
        assert (slot_dir / "unexpanded.zip").exists()
