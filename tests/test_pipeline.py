"""Tests for backend/pipeline.py."""
from __future__ import annotations

import gzip
import zipfile
from pathlib import Path

from backend.pipeline import Pipeline


class TestGzExpansionGate:
    def test_run_expands_plain_gz_in_extracted_by_default(self, tmp_path):
        gz_path = tmp_path / "journal.log.1.gz"
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            f.write("searchable journal\n")

        package = tmp_path / "package.zip"
        with zipfile.ZipFile(package, "w") as zf:
            zf.write(gz_path, "varlog/slot_1/journal.log.1.gz")

        pipeline = Pipeline({"pipeline": {"recursive_extraction": True}})
        pipeline._plugin_cache["default"] = (_FakeDiscovery(), _FakeParser())

        pipeline.run(package, tmp_path / "out", task_id="task")

        extract_dir = tmp_path / "out" / "task" / "extracted" / "varlog" / "slot_1"
        assert (extract_dir / "journal.log.1.gz").exists()
        assert (extract_dir / "journal.log.1").read_text(encoding="utf-8") == "searchable journal\n"
        assert not (extract_dir / "journal.log.1.gz_extracted").exists()

    def test_run_can_disable_plain_gz_expansion(self, tmp_path):
        gz_path = tmp_path / "journal.log.1.gz"
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            f.write("compressed only\n")

        package = tmp_path / "package.zip"
        with zipfile.ZipFile(package, "w") as zf:
            zf.write(gz_path, "varlog/slot_1/journal.log.1.gz")

        pipeline = Pipeline({"pipeline": {"recursive_extraction": True, "debug_expand_gz": False}})
        pipeline._plugin_cache["default"] = (_FakeDiscovery(), _FakeParser())

        pipeline.run(package, tmp_path / "out", task_id="task")

        extract_dir = tmp_path / "out" / "task" / "extracted" / "varlog" / "slot_1"
        assert (extract_dir / "journal.log.1.gz").exists()
        assert not (extract_dir / "journal.log.1").exists()
        assert not (extract_dir / "journal.log.1.gz_extracted").exists()


class _FakeDiscovery:
    def discover(self, extract_dir):
        return [], []


class _FakeParser:
    def parse(self, result):
        return result


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


class TestPipelineCleanup:
    def test_cleanup_extracted_removes_workspace_after_run(self, tmp_path):
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

        pipeline.run(tmp_path / "package.zip", tmp_path / "out", task_id="task")

        assert not (tmp_path / "out" / "task" / "extracted").exists()

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

        pipeline.run(tmp_path / "package.zip", tmp_path / "out", task_id="task")

        slot_dir = tmp_path / "out" / "task" / "extracted" / "diag" / "slot_1"
        assert slot_dir.exists()
        assert not (slot_dir / "diag.zip").exists()
        assert (slot_dir / "diag.zip_extracted" / "inner.log").exists()
        assert (slot_dir / "unexpanded.zip").exists()
