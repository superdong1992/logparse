"""Tests for cli.py — exit code behavior on fatal parse errors."""
from __future__ import annotations

import zipfile
from pathlib import Path

from click.testing import CliRunner

from cli import cli


class TestParseExitCode:
    """Verify that the parse command returns non-zero on fatal errors."""

    def test_parse_bad_zip_exit_nonzero(self, tmp_path):
        """A corrupt zip file should cause a non-zero exit code."""
        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_text("not a zip file", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(cli, ["parse", str(bad_zip), "-o", str(tmp_path / "out")])

        assert result.exit_code != 0, (
            f"Expected non-zero exit code for bad zip, got {result.exit_code}.\n"
            f"Output:\n{result.output}"
        )

    def test_parse_valid_package_exit_zero(self, tmp_path):
        """A minimal valid package should exit with code 0."""
        # Build outer zip with diag/slot_1/diag.zip containing a log line
        diag_inner = tmp_path / "diag_inner.zip"
        with zipfile.ZipFile(diag_inner, "w") as zf:
            zf.writestr("diag.log", "2026-01-03T00:00:00+08:00 dummy line\n")

        outer_zip = tmp_path / "diagnostic_information_20260103.zip"
        with zipfile.ZipFile(outer_zip, "w") as zf:
            zf.write(str(diag_inner), "diag/slot_1/diag.zip")

        runner = CliRunner()
        result = runner.invoke(
            cli, ["parse", str(outer_zip), "-o", str(tmp_path / "out")]
        )

        assert result.exit_code == 0, (
            f"Expected exit code 0 for valid package, got {result.exit_code}.\n"
            f"Output:\n{result.output}"
        )

    def test_parse_unknown_product_exit_nonzero(self, tmp_path):
        """Unknown product must produce non-zero exit without traceback."""
        runner = CliRunner()
        result = runner.invoke(cli, [
            "parse",
            "tests/mock_data/diagnostic_information_20260103.zip",
            "-o",
            str(tmp_path / "out"),
            "--product",
            "nope",
        ])

        assert result.exit_code != 0
        assert "未找到产品配置" in result.output
        assert "Traceback" not in result.output
