from __future__ import annotations

import gzip
import os
import shutil
import tarfile
import zipfile
from pathlib import Path

from backend.config import ConfigLoader


class Decompressor:
    """多层递归解压引擎。"""

    def __init__(self, config_loader: ConfigLoader):
        self.config = config_loader

    def extract_all(self, source: Path, dest_dir: Path, recursive: bool = True) -> list[str]:
        """
        解压 source 到 dest_dir。
        recursive=False 时只解压一层，不递归处理内部压缩包。
        返回所有被解压过的文件路径列表。
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        extracted_files: list[str] = []

        self._extract_single(source, dest_dir, extracted_files)

        if not recursive:
            return extracted_files

        # 递归扫描解压出的新压缩包
        changed = True
        while changed:
            changed = False
            for root, dirs, files in os.walk(dest_dir):
                for f in files:
                    if self.config.is_compressed(f):
                        file_path = Path(root) / f
                        relative_parent = Path(root).relative_to(dest_dir)
                        target_dir = dest_dir / relative_parent / f"{f}_extracted"
                        try:
                            self._extract_single(file_path, target_dir, extracted_files)
                        except Exception:
                            raise
                        else:
                            file_path.unlink()
                        changed = True

        return extracted_files

    def _extract_single(self, source: Path, dest_dir: Path, extracted_files: list[str]) -> None:
        if source.stat().st_size == 0:
            return
        name_lower = source.name.lower()
        try:
            if name_lower.endswith(".zip"):
                self._extract_zip(source, dest_dir, extracted_files)
            elif name_lower.endswith(".tar.gz") or name_lower.endswith(".tgz") or name_lower.endswith(".tar"):
                self._extract_tar(source, dest_dir, extracted_files)
            elif name_lower.endswith(".gz"):
                self._extract_gz(source, dest_dir, extracted_files)
            else:
                # 非压缩文件，直接复制
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest_dir / source.name)
        except Exception as e:
            raise RuntimeError(f"解压失败 {source}: {e}") from e

    def _extract_zip(self, source: Path, dest_dir: Path, extracted_files: list[str]) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source, "r") as zf:
            zf.extractall(dest_dir)
            extracted_files.extend(
                str(dest_dir / name) for name in zf.namelist()
            )

    def _extract_tar(self, source: Path, dest_dir: Path, extracted_files: list[str]) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(source, "r:*") as tf:
            tf.extractall(dest_dir)
            extracted_files.extend(
                str(dest_dir / name) for name in tf.getnames()
            )

    def _extract_gz(self, source: Path, dest_dir: Path, extracted_files: list[str]) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        # gzip 单文件解压，去掉 .gz 后缀
        output_name = source.stem  # removes .gz
        output_path = dest_dir / output_name
        with gzip.open(source, "rb") as f_in:
            with open(output_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        extracted_files.append(str(output_path))
