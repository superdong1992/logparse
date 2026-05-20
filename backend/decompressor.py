from __future__ import annotations

import gzip
import logging
import os
import tarfile
import zipfile
from pathlib import Path

from typing import Iterable

logger = logging.getLogger(__name__)

# 安全限制
MAX_UNCOMPRESSED_SIZE = 500 * 1024 * 1024       # 单文件最大解压后 500MB
MAX_COMPRESSION_RATIO = 100                      # 压缩比上限（解压后/压缩前）
MAX_RECURSIVE_PASSES = 10                        # 最大递归轮次

DEFAULT_COMPRESSED_EXTENSIONS = [".gz", ".zip", ".tar.gz", ".tgz", ".tar"]


class Decompressor:
    """多层递归解压引擎。

    可传入 config_loader（向后兼容）或 compressed_extensions 列表。
    """

    def __init__(
        self,
        config_loader=None,
        compressed_extensions: Iterable[str] | None = None,
    ):
        if compressed_extensions is not None:
            self._compressed_extensions = list(compressed_extensions)
        elif config_loader is not None:
            self._compressed_extensions = config_loader.get_config().compressed_extensions
        else:
            self._compressed_extensions = DEFAULT_COMPRESSED_EXTENSIONS
        self.config = config_loader

    def is_compressed(self, name: str) -> bool:
        """检查文件名是否属于已知压缩格式。"""
        name_lower = name.lower()
        for ext in self._compressed_extensions:
            if name_lower.endswith(ext):
                return True
        return False

    @staticmethod
    def _is_safe_path(member_name: str) -> bool:
        """检查压缩包内文件路径是否安全（无路径穿越）。"""
        # 拒绝绝对路径（含跨平台）
        if os.path.isabs(member_name):
            return False
        normed = member_name.replace("\\", "/")
        if normed.startswith("/"):
            return False
        # 拒绝 .. 路径穿越
        return ".." not in normed.split("/")

    @staticmethod
    def _check_zip_bomb(compressed_size: int, uncompressed_size: int, name: str) -> bool:
        """检查是否为 zip 炸弹。返回 True 表示安全。"""
        if compressed_size == 0:
            return True
        if uncompressed_size > MAX_UNCOMPRESSED_SIZE:
            logger.warning("文件过大，跳过: %s (解压后 %d bytes)", name, uncompressed_size)
            return False
        ratio = uncompressed_size / compressed_size
        if ratio > MAX_COMPRESSION_RATIO:
            logger.warning("压缩比异常 (%.0fx)，可能为 zip 炸弹，跳过: %s", ratio, name)
            return False
        return True

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
        passes = 0
        changed = True
        pass_log: list[list[str]] = []
        while changed:
            passes += 1
            this_pass: list[str] = []
            changed = False
            for root, dirs, files in os.walk(dest_dir):
                for f in files:
                    if self.is_compressed(f):
                        file_path = Path(root) / f
                        relative_parent = Path(root).relative_to(dest_dir)
                        target_dir = dest_dir / relative_parent / f"{f}_extracted"
                        if target_dir.exists():
                            continue
                        try:
                            self._extract_single(file_path, target_dir, extracted_files)
                        except Exception as e:
                            logger.warning("解压失败 %s: %s", file_path, e)
                            # 清理失败时可能已创建的空目录，避免阻止后续重试
                            if target_dir.is_dir() and not any(target_dir.iterdir()):
                                target_dir.rmdir()
                            continue
                        this_pass.append(str(file_path.relative_to(dest_dir)))
                        changed = True
            pass_log.append(this_pass)
            if passes > MAX_RECURSIVE_PASSES:
                logger.warning(
                    "递归解压超过最大轮次 %d，终止。各轮解压文件：",
                    MAX_RECURSIVE_PASSES,
                )
                for i, files in enumerate(pass_log, 1):
                    logger.warning(
                        "  第 %d 轮: %d 个文件 — %s",
                        i, len(files), files,
                    )
                break

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
                logger.warning("未识别的文件类型，跳过: %s", source)
        except Exception as e:
            raise RuntimeError(f"解压失败 {source}: {e}") from e

    def _extract_zip(self, source: Path, dest_dir: Path, extracted_files: list[str]) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source, "r") as zf:
            for info in zf.infolist():
                if not self._is_safe_path(info.filename):
                    logger.warning("路径穿越风险，跳过: %s", info.filename)
                    continue
                if info.is_dir():
                    continue
                if not self._check_zip_bomb(info.compress_size, info.file_size, info.filename):
                    continue
                zf.extract(info, dest_dir)
                extracted_files.append(str(dest_dir / info.filename))

    def _extract_tar(self, source: Path, dest_dir: Path, extracted_files: list[str]) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(source, "r:*") as tf:
            safe_members = []
            for info in tf.getmembers():
                if not self._is_safe_path(info.name):
                    logger.warning("路径穿越风险，跳过: %s", info.name)
                    continue
                if info.isdir():
                    continue
                if info.size > MAX_UNCOMPRESSED_SIZE:
                    logger.warning("文件过大，跳过: %s (%d bytes)", info.name, info.size)
                    continue
                safe_members.append(info)
            tf.extractall(dest_dir, members=safe_members)
            extracted_files.extend(
                str(dest_dir / m.name) for m in safe_members
            )

    def _extract_gz(self, source: Path, dest_dir: Path, extracted_files: list[str]) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        # 检查压缩比（gzip 无原生 uncompressed size，用 stat 文件大小近似判断）
        compressed_size = source.stat().st_size
        if compressed_size > MAX_UNCOMPRESSED_SIZE:
            logger.warning("压缩文件过大，跳过: %s (%d bytes)", source, compressed_size)
            return
        # gzip 单文件解压，去掉 .gz 后缀
        output_name = source.stem  # removes .gz
        output_path = dest_dir / output_name
        # 解压时限制输出大小
        exceeded = False
        with gzip.open(source, "rb") as f_in:
            with open(output_path, "wb") as f_out:
                written = 0
                while True:
                    chunk = f_in.read(1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_UNCOMPRESSED_SIZE:
                        logger.warning("gzip 解压后超过大小上限，跳过: %s", source)
                        exceeded = True
                        break
                    f_out.write(chunk)
        if exceeded:
            output_path.unlink()
        else:
            extracted_files.append(str(output_path))
