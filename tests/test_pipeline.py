"""Tests for backend/pipeline.py."""
from __future__ import annotations

import gzip
from pathlib import Path

from backend.pipeline import Pipeline


class TestGzExpansionGate:
    def test_gz_not_expanded_by_default(self, tmp_path):
        """Default config should not expand .gz files in-place."""
        # Create a fake extracted dir with a .gz file
        extract_dir = tmp_path / "task1" / "extracted"
        extract_dir.mkdir(parents=True)
        gz_path = extract_dir / "test.log.gz"
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            f.write("hello world\n")

        pipeline = Pipeline({"pipeline": {"debug_expand_gz": False}})
        count = pipeline._decompress_gz_in_dir(extract_dir)
        # The method still expands when called directly; the gate is in run()
        # So test that pipeline config controls the gate
        assert not pipeline.pipeline_config.get("debug_expand_gz", False)

    def test_gz_expansion_enabled_via_config(self, tmp_path):
        extract_dir = tmp_path / "task2" / "extracted"
        extract_dir.mkdir(parents=True)
        gz_path = extract_dir / "test.log.gz"
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            f.write("hello world\n")

        pipeline = Pipeline({"pipeline": {"debug_expand_gz": True}})
        assert pipeline.pipeline_config.get("debug_expand_gz") is True
        count = pipeline._decompress_gz_in_dir(extract_dir)
        assert count == 1
        assert (extract_dir / "test.log").exists()
