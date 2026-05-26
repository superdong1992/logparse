"""Tests for backend/pipeline.py."""
from __future__ import annotations

import gzip
import struct
import zipfile
from pathlib import Path

import yaml

from backend.models import LogEntry, ParseResult, SlotInfo
from backend.pipeline import Pipeline


def _load_test_config() -> dict:
    """Load the real config.yaml for integration tests."""
    config_path = Path("config.yaml")
    if config_path.exists():
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return {}


class TestInnerExtractionErrors:
    def test_corrupt_inner_zip_recorded_in_result_errors(self, tmp_path):
        """Inner extraction failure should be recorded in result.errors."""
        # Create a corrupt inner zip file on disk
        corrupt_inner = tmp_path / "diag.zip"
        corrupt_inner.write_bytes(b"PK\x03\x04" + b"\x00" * 50)  # invalid zip

        # Build a minimal ParseResult pointing at the corrupt file
        entry = LogEntry(
            path=str(corrupt_inner),
            name="diag.zip",
            compressed=True,
        )
        slot = SlotInfo(
            slot_id="1",
            name="slot_1",
            path=str(tmp_path),
            diagnostic_logs=[entry],
        )
        result = ParseResult(
            task_id="test",
            diagnostic_slots=[slot],
            errors=[],
        )

        pipeline = Pipeline({})
        pipeline._extract_inner_contents(result, tmp_path / "output")

        assert any("内层解压失败" in err for err in result.errors)


class TestGzExpansionIntegration:
    """Integration tests for .gz expansion gate through Pipeline.run()."""

    @staticmethod
    def _build_test_package(tmp_path: Path) -> Path:
        """Build a minimal test zip with varlog containing a .gz file.

        Structure:
          test_package.zip
            varlog/
              slot_1/
                varlog.zip
                  varlog/
                    journal.log
                    journal.log.1.gz
        """
        # Create inner varlog content with a .gz file
        varlog_inner = tmp_path / "varlog_inner"
        varlog_inner.mkdir()
        varlog_subdir = varlog_inner / "varlog"
        varlog_subdir.mkdir()

        gz_content = "2026-01-03T00:00:00+08:00 test log line from gz\n"
        gz_path = varlog_subdir / "journal.log.1.gz"
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            f.write(gz_content)

        # Also add a plain journal.log so the scanner finds journal files
        (varlog_subdir / "journal.log").write_text(
            "2026-01-03T00:01:00+08:00 current log line\n", encoding="utf-8"
        )

        # Create varlog.zip containing the varlog/ subtree
        varlog_zip = tmp_path / "varlog.zip"
        with zipfile.ZipFile(varlog_zip, "w") as zf:
            for f in varlog_inner.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(varlog_inner))

        # Create outer zip with proper default-product structure
        outer_zip = tmp_path / "test_package.zip"
        with zipfile.ZipFile(outer_zip, "w") as zf:
            zf.write(varlog_zip, "varlog/slot_1/varlog.zip")

        return outer_zip

    def test_pipeline_does_not_expand_gz_by_default(self, tmp_path):
        """Pipeline.run() should not expand plain .gz when debug_expand_gz is False."""
        config = _load_test_config()
        config.setdefault("pipeline", {})["debug_expand_gz"] = False

        input_zip = self._build_test_package(tmp_path)
        out_dir = tmp_path / "out"

        pipeline = Pipeline(config)
        result = pipeline.run(input_zip, out_dir, product="default")

        assert result is not None
        # Find .gz files in output — the original .gz should still be present
        gz_files = list(out_dir.rglob("*.gz"))
        # For each .gz, the plain file (without .gz suffix) should NOT exist
        for gz_file in gz_files:
            plain_file = gz_file.with_suffix("")
            assert not plain_file.exists(), (
                f"{plain_file} should not exist when debug_expand_gz=False"
            )

    def test_pipeline_expands_gz_when_debug_enabled(self, tmp_path):
        """Pipeline.run() should expand plain .gz when debug_expand_gz is True."""
        config = _load_test_config()
        config.setdefault("pipeline", {})["debug_expand_gz"] = True

        input_zip = self._build_test_package(tmp_path)
        out_dir = tmp_path / "out"

        pipeline = Pipeline(config)
        result = pipeline.run(input_zip, out_dir, product="default")

        assert result is not None
        gz_files = list(out_dir.rglob("*.gz"))
        assert gz_files, "Should have at least one .gz file in output"
        # At least one .gz should have been expanded (plain file exists alongside it)
        assert any(gz.with_suffix("").exists() for gz in gz_files), (
            "At least one .gz should have been expanded when debug_expand_gz=True"
        )


class TestPipelineFatalHandling:
    def test_stops_after_outer_extract_failure(self, tmp_path):
        """Outer extraction failure should not proceed to discovery/parse."""
        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_text("not zip", encoding="utf-8")

        pipeline = Pipeline({})
        result = pipeline.run(bad_zip, tmp_path / "out")

        assert any("[1/6] 解压" in err for err in result.errors)
        assert result.diagnostic_slots == []
        assert result.private_slots == []

    def test_stops_on_unknown_product(self, tmp_path):
        """Unknown product should stop pipeline with plugin loading error."""
        # Create a minimal valid zip for outer extraction to succeed
        import zipfile
        outer_zip = tmp_path / "test.zip"
        with zipfile.ZipFile(outer_zip, "w") as zf:
            zf.writestr("dummy.txt", "test")

        pipeline = Pipeline({})
        result = pipeline.run(outer_zip, tmp_path / "out", product="nonexistent")

        assert any("未找到产品配置" in err for err in result.errors)
