"""Tests for backend/decompressor.py."""
from __future__ import annotations

import gzip
import tarfile
import zipfile
from pathlib import Path

import pytest

from backend.decompressor import Decompressor, MAX_UNCOMPRESSED_SIZE


@pytest.fixture
def decompressor():
    return Decompressor()


def _create_zip(path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


class TestIsCompressed:
    def test_zip(self, decompressor):
        assert decompressor.is_compressed("file.zip")

    def test_tar_gz(self, decompressor):
        assert decompressor.is_compressed("file.tar.gz")

    def test_plain(self, decompressor):
        assert not decompressor.is_compressed("file.log")


class TestSafePath:
    def test_normal_path(self):
        assert Decompressor._is_safe_path("dir/file.txt")

    def test_path_traversal(self):
        assert not Decompressor._is_safe_path("../etc/passwd")

    def test_absolute_unix(self):
        assert not Decompressor._is_safe_path("/etc/passwd")

    def test_absolute_windows(self):
        assert not Decompressor._is_safe_path("C:\\Windows\\system32")

    def test_absolute_windows_forward_slash(self):
        assert not Decompressor._is_safe_path("C:/Windows/system32")

    def test_unc_path(self):
        assert not Decompressor._is_safe_path("\\\\server\\share")

    def test_empty_path(self):
        assert not Decompressor._is_safe_path("")

    def test_nested_traversal(self):
        assert not Decompressor._is_safe_path("dir/../../etc/passwd")


class TestCheckZipBomb:
    def test_safe_file(self):
        assert Decompressor._check_zip_bomb(100, 1000, "safe.txt")

    def test_oversized_file(self):
        assert not Decompressor._check_zip_bomb(100, MAX_UNCOMPRESSED_SIZE + 1, "big.txt")

    def test_high_compression_ratio(self):
        assert not Decompressor._check_zip_bomb(1, 200, "bomb.txt")

    def test_zero_compressed(self):
        assert Decompressor._check_zip_bomb(0, 1000, "ok.txt")


class TestExtractZip:
    def test_basic_extraction(self, decompressor, tmp_path):
        zip_path = tmp_path / "test.zip"
        _create_zip(zip_path, {"hello.txt": "world"})

        dest = tmp_path / "out"
        extracted = []
        decompressor._extract_zip(zip_path, dest, extracted)

        assert (dest / "hello.txt").read_text() == "world"
        assert len(extracted) == 1

    def test_skips_path_traversal(self, decompressor, tmp_path):
        zip_path = tmp_path / "evil.zip"
        _create_zip(zip_path, {"../escape.txt": "evil", "safe.txt": "ok"})

        dest = tmp_path / "out"
        extracted = []
        decompressor._extract_zip(zip_path, dest, extracted)

        assert (dest / "safe.txt").exists()
        assert not (tmp_path / "escape.txt").exists()
        assert len(extracted) == 1

    def test_skips_directories(self, decompressor, tmp_path):
        zip_path = tmp_path / "test.zip"
        _create_zip(zip_path, {"subdir/": ""})

        dest = tmp_path / "out"
        extracted = []
        decompressor._extract_zip(zip_path, dest, extracted)

        assert len(extracted) == 0


class TestExtractGz:
    def test_basic_gz(self, decompressor, tmp_path):
        gz_path = tmp_path / "test.log.gz"
        with gzip.open(gz_path, "wb") as f:
            f.write(b"hello world")

        dest = tmp_path / "out"
        extracted = []
        decompressor._extract_gz(gz_path, dest, extracted)

        out_file = dest / "test.log"
        assert out_file.read_text() == "hello world"
        assert len(extracted) == 1


class TestExtractAll:
    def test_non_recursive(self, decompressor, tmp_path):
        zip_path = tmp_path / "outer.zip"
        _create_zip(zip_path, {"inner.zip": "not-a-real-zip"})

        dest = tmp_path / "out"
        decompressor.extract_all(zip_path, dest, recursive=False)

        # inner.zip should still exist (not recursively extracted)
        assert (dest / "inner.zip").exists()

    def test_recursive(self, decompressor, tmp_path):
        inner_zip = tmp_path / "inner.zip"
        _create_zip(inner_zip, {"data.txt": "content"})

        outer_zip = tmp_path / "outer.zip"
        with zipfile.ZipFile(outer_zip, "w") as zf:
            zf.write(inner_zip, "inner.zip")

        dest = tmp_path / "out"
        decompressor.extract_all(outer_zip, dest, recursive=True)

        # After recursive extraction, inner.zip should be extracted and removed
        inner_extracted = dest / "inner.zip_extracted"
        assert (inner_extracted / "data.txt").read_text() == "content"

    def test_empty_file_skipped(self, decompressor, tmp_path):
        zip_path = tmp_path / "empty.zip"
        zip_path.write_bytes(b"")

        dest = tmp_path / "out"
        extracted = decompressor.extract_all(zip_path, dest)
        assert extracted == []


class TestExtractTarSymlinks:
    def test_tar_symlink_rejected(self, decompressor, tmp_path):
        tar_path = tmp_path / "bad.tar"
        with tarfile.open(tar_path, "w") as tf:
            info = tarfile.TarInfo("safe_link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/tmp/evil_target"
            tf.addfile(info)

        out = tmp_path / "out"
        extracted = []
        decompressor._extract_tar(tar_path, out, extracted)

        assert not (out / "safe_link").exists()
        assert not (out / "safe_link").is_symlink()
        assert extracted == []

    def test_tar_hardlink_rejected(self, decompressor, tmp_path):
        # Need a real file to create a hardlink reference
        tar_path = tmp_path / "bad_hardlink.tar"
        with tarfile.open(tar_path, "w") as tf:
            # Add a regular file first
            data = b"content"
            regular = tarfile.TarInfo("regular.txt")
            regular.size = len(data)
            regular.type = tarfile.REGTYPE
            tf.addfile(regular, __import__("io").BytesIO(data))

            # Add a hardlink pointing to it
            link = tarfile.TarInfo("hard_link")
            link.type = tarfile.LNKTYPE
            link.linkname = "regular.txt"
            tf.addfile(link)

        out = tmp_path / "out"
        extracted = []
        decompressor._extract_tar(tar_path, out, extracted)

        # Regular file should be extracted, hardlink should be rejected
        assert (out / "regular.txt").exists()
        assert not (out / "hard_link").exists()
        assert len(extracted) == 1


class TestRecursiveGzGate:
    def test_recursive_skips_plain_gz_by_default(self, decompressor, tmp_path):
        gz_file = tmp_path / "journal.log.1.gz"
        with gzip.open(gz_file, "wt", encoding="utf-8") as f:
            f.write("hello\n")

        zip_path = tmp_path / "pkg.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(gz_file, "journal.log.1.gz")

        out = tmp_path / "out"
        decompressor.extract_all(zip_path, out, recursive=True, expand_gz=False)

        assert (out / "journal.log.1.gz").exists()
        assert not (out / "journal.log.1.gz_extracted").exists()
        assert not (out / "journal.log.1").exists()

    def test_recursive_expands_plain_gz_when_enabled(self, decompressor, tmp_path):
        gz_file = tmp_path / "journal.log.1.gz"
        with gzip.open(gz_file, "wt", encoding="utf-8") as f:
            f.write("hello\n")

        zip_path = tmp_path / "pkg.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(gz_file, "journal.log.1.gz")

        out = tmp_path / "out"
        decompressor.extract_all(zip_path, out, recursive=True, expand_gz=True)

        assert (out / "journal.log.1.gz").exists()
        assert (out / "journal.log.1").exists()
        assert (out / "journal.log.1").read_text() == "hello\n"

    def test_recursive_gz_skips_when_target_exists(self, decompressor, tmp_path):
        gz_file = tmp_path / "journal.log.1.gz"
        with gzip.open(gz_file, "wt", encoding="utf-8") as f:
            f.write("new_content\n")

        zip_path = tmp_path / "pkg.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(gz_file, "journal.log.1.gz")

        # Extract non-recursively first to get the .gz on disk
        out = tmp_path / "out"
        decompressor.extract_all(zip_path, out, recursive=False)

        # Pre-create the target file with different content
        target = out / "journal.log.1"
        target.write_text("old_content\n", encoding="utf-8")

        # Now run with expand_gz=True — should NOT overwrite
        decompressor.extract_all(zip_path, out, recursive=True, expand_gz=True)

        assert target.read_text(encoding="utf-8") == "old_content\n"


class TestTarNoDeprecationWarning:
    def test_extract_tar_no_deprecation_warning(self, decompressor, tmp_path):
        import io
        import warnings

        tar_path = tmp_path / "test.tar"
        with tarfile.open(tar_path, "w") as tf:
            data = b"hello world"
            info = tarfile.TarInfo("test.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

        out = tmp_path / "out"
        extracted = []

        with warnings.catch_warnings(record=True) as records:
            warnings.simplefilter("always")
            decompressor._extract_tar(tar_path, out, extracted)

        assert not any("Python 3.14" in str(w.message) for w in records)
        assert (out / "test.txt").exists()
