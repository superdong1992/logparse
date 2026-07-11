"""Atomic artifact persistence and parse manifest integrity collection."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from backend.contracts.artifacts import (
    ArtifactIntegrityRecord,
    ManifestStageRecord,
    ParseManifest,
    ParseStatus,
    WorkspaceRecord,
)
from backend.contracts.diagnostics import DiagnosticRecord
from backend.infrastructure.artifact_layout import ArtifactLayout


class ArtifactRepository:
    """Write all official artifacts through one canonical layout."""

    def __init__(self, layout: ArtifactLayout):
        self.layout = layout

    @classmethod
    def for_task_dir(cls, task_dir: Path) -> "ArtifactRepository":
        return cls(ArtifactLayout.from_task_dir(task_dir))

    def ensure_task(self) -> None:
        self.layout.task_dir.mkdir(parents=True, exist_ok=True)

    def ensure_mech_modules(self) -> Path:
        self.layout.mech_modules.mkdir(parents=True, exist_ok=True)
        return self.layout.mech_modules

    def write_metadata(self, payload: Mapping[str, Any]) -> Path:
        return self.write_json(self.layout.metadata, payload)

    def write_result(self, payload: Mapping[str, Any]) -> Path:
        return self.write_json(self.layout.result, payload)

    def write_performance(self, payload: Mapping[str, Any]) -> Path:
        return self.write_json(self.layout.performance, payload)

    def write_manifest(self, manifest: ParseManifest) -> Path:
        return self.write_json(self.layout.parse_manifest, manifest.to_dict())

    def refresh_manifest(
        self,
        *,
        product: str,
        status: ParseStatus | str,
        stages: Iterable[ManifestStageRecord] = (),
        counters: Mapping[str, int | float] | None = None,
        diagnostics: Iterable[DiagnosticRecord | Mapping[str, Any] | str] = (),
        workspace_retained: bool = False,
        workspace_path: str | None = None,
        created_at: str | None = None,
    ) -> Path:
        """Recompute integrity records and atomically replace the manifest."""
        self.ensure_task()
        self.ensure_mech_modules()
        manifest = ParseManifest.create(
            task_id=self.layout.task_id,
            product=product,
            status=status,
            artifacts=self.collect_parse_artifacts(),
            stages=stages,
            counters=counters,
            diagnostics=diagnostics,
            workspace=WorkspaceRecord(
                retained=workspace_retained,
                path=workspace_path if workspace_retained else None,
            ),
            created_at=created_at,
        )
        return self.write_manifest(manifest)

    def collect_parse_artifacts(self) -> dict[str, ArtifactIntegrityRecord]:
        """Collect parse artifacts only; DFX outputs have their own manifest."""
        candidates = {
            "metadata": self.layout.metadata,
            "result": self.layout.result,
            "mech_modules": self.layout.mech_modules,
            "performance": self.layout.performance,
        }
        records: dict[str, ArtifactIntegrityRecord] = {}
        for name, path in candidates.items():
            if path.is_file():
                records[name] = self._file_record(path)
            elif path.is_dir():
                records[name] = self._directory_record(path)
        return records

    def inspect_artifact(self, path: Path) -> ArtifactIntegrityRecord:
        """Return integrity data for one task-local artifact."""

        target = self._validate_target(path)
        if target.is_dir():
            return self._directory_record(target)
        return self._file_record(target)

    def verify_manifest(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        """Verify declared artifact paths, sizes and hashes without log reads."""
        declared = manifest.get("artifacts")
        if not isinstance(declared, Mapping):
            return {
                "ok": False,
                "checked": 0,
                "issues": [
                    {
                        "code": "invalid_artifacts",
                        "message": "manifest artifacts must be an object",
                    }
                ],
            }

        issues: list[dict[str, Any]] = []
        checked = 0
        for name in sorted(declared):
            expected = declared[name]
            if not isinstance(expected, Mapping):
                issues.append(
                    {
                        "artifact": str(name),
                        "code": "invalid_record",
                    }
                )
                continue
            relative_path = str(expected.get("path") or "")
            if not relative_path:
                issues.append(
                    {
                        "artifact": str(name),
                        "code": "invalid_path",
                        "path": relative_path,
                    }
                )
                continue
            try:
                path = self._validate_target(self.layout.task_dir / relative_path)
            except ValueError:
                issues.append(
                    {
                        "artifact": str(name),
                        "code": "path_escape",
                        "path": relative_path,
                    }
                )
                continue
            if not path.exists():
                issues.append(
                    {
                        "artifact": str(name),
                        "code": "missing",
                        "path": relative_path,
                    }
                )
                continue
            try:
                actual = (
                    self._directory_record(path)
                    if path.is_dir()
                    else self._file_record(path)
                )
            except ValueError as exc:
                issues.append(
                    {
                        "artifact": str(name),
                        "code": "unsafe_artifact",
                        "path": relative_path,
                        "message": str(exc),
                    }
                )
                continue
            checked += 1
            for field in ("kind", "size_bytes", "sha256", "file_count"):
                if expected.get(field) != actual.to_dict().get(field):
                    issues.append(
                        {
                            "artifact": str(name),
                            "code": f"{field}_mismatch",
                            "path": relative_path,
                        }
                    )
        return {"ok": not issues, "checked": checked, "issues": issues}

    def write_json(self, path: Path, payload: Mapping[str, Any]) -> Path:
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
        return self.write_text(path, text)

    def write_text(self, path: Path, content: str) -> Path:
        return self.write_bytes(path, content.encode("utf-8"))

    def write_bytes(self, path: Path, content: bytes) -> Path:
        target = self._validate_target(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
            os.replace(temp_path, target)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        return target

    def _validate_target(self, path: Path) -> Path:
        target = Path(path)
        root = self.layout.task_dir.resolve(strict=False)
        resolved = target.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"artifact path is outside task directory: {path}"
            ) from exc
        return target

    def _file_record(self, path: Path) -> ArtifactIntegrityRecord:
        if path.is_symlink():
            raise ValueError(f"artifact symlinks are not supported: {path}")
        return ArtifactIntegrityRecord(
            path=self.layout.relative_path(path),
            kind="file",
            size_bytes=path.stat().st_size,
            sha256=_sha256_file(path),
            file_count=1,
        )

    def _directory_record(self, path: Path) -> ArtifactIntegrityRecord:
        digest = hashlib.sha256()
        size_bytes = 0
        file_count = 0
        for item in sorted(path.rglob("*"), key=lambda value: value.as_posix()):
            if item.is_symlink():
                raise ValueError(f"artifact symlinks are not supported: {item}")
            if not item.is_file():
                continue
            relative = item.relative_to(path).as_posix()
            item_digest = _sha256_file(item)
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(item_digest.encode("ascii"))
            digest.update(b"\n")
            size_bytes += item.stat().st_size
            file_count += 1
        return ArtifactIntegrityRecord(
            path=self.layout.relative_path(path),
            kind="directory",
            size_bytes=size_bytes,
            sha256=digest.hexdigest(),
            file_count=file_count,
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
