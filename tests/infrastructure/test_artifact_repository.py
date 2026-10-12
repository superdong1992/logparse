"""Untrusted manifest and filesystem boundaries for artifact persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.infrastructure.artifact_layout import ArtifactLayout
from backend.infrastructure.artifact_repository import ArtifactRepository


@pytest.fixture
def repository(tmp_path: Path) -> ArtifactRepository:
    repository = ArtifactRepository(ArtifactLayout(tmp_path, "task"))
    repository.write_result({"schema_version": 2, "mech_results": []})
    return repository


@pytest.mark.parametrize("artifacts", [None, [], "result.json"])
def test_manifest_artifacts_must_be_an_object(repository, artifacts) -> None:
    report = repository.verify_manifest({"artifacts": artifacts})

    assert report["ok"] is False
    assert report["checked"] == 0
    assert [issue["code"] for issue in report["issues"]] == ["invalid_artifacts"]


@pytest.mark.parametrize(
    ("record", "code"),
    [
        ([], "invalid_record"),
        ({}, "invalid_path"),
        ({"path": "../outside.json"}, "path_escape"),
        ({"path": "missing.json"}, "missing"),
    ],
)
def test_malformed_manifest_record_does_not_prevent_checking_valid_artifacts(
    repository, record, code,
) -> None:
    valid = repository.inspect_artifact(repository.layout.result).to_dict()
    report = repository.verify_manifest({"artifacts": {"bad": record, "result": valid}})

    assert report["ok"] is False
    assert report["checked"] == 1
    assert [(issue["artifact"], issue["code"]) for issue in report["issues"]] == [
        ("bad", code),
    ]


def test_missing_integrity_fields_cannot_pass_verification(repository) -> None:
    report = repository.verify_manifest({"artifacts": {"result": {"path": "result.json"}}})

    assert report["ok"] is False
    assert {issue["code"] for issue in report["issues"]} == {
        "kind_mismatch", "size_bytes_mismatch", "sha256_mismatch", "file_count_mismatch",
    }


def test_absolute_external_manifest_path_is_rejected_without_reading_it(
    repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("private document", encoding="utf-8")
    original_open = Path.open

    def guard_external(path, *args, **kwargs):
        assert path != outside, "manifest verification must never open an external path"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guard_external)
    report = repository.verify_manifest({"artifacts": {"outside": {"path": str(outside)}}})

    assert report["checked"] == 0
    assert report["issues"][0]["code"] == "path_escape"


def test_outside_write_is_rejected_and_preserves_existing_document(repository, tmp_path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"unchanged")

    with pytest.raises(ValueError, match="outside task directory"):
        repository.write_bytes(outside, b"replacement")

    assert outside.read_bytes() == b"unchanged"


def test_directory_inspection_hashes_files_and_ignores_empty_directories(repository) -> None:
    evidence = repository.ensure_mech_modules()
    (evidence / "empty").mkdir()
    nested = evidence / "nested"
    nested.mkdir()
    log = nested / "process.log"
    log.write_bytes(b"before")

    first = repository.inspect_artifact(evidence)
    log.write_bytes(b"after!")
    second = repository.inspect_artifact(evidence)

    assert first.kind == "directory"
    assert first.file_count == second.file_count == 1
    assert first.size_bytes == second.size_bytes == 6
    assert first.sha256 != second.sha256


@pytest.mark.parametrize("directory", [False, True])
def test_filesystem_reported_symlink_is_rejected_before_reading_content(
    repository, monkeypatch: pytest.MonkeyPatch, directory: bool,
) -> None:
    # Simulate the filesystem flag so this security check also runs on Windows
    # accounts that cannot create real symlinks. Resolution/containment is
    # covered separately by the external-path tests.
    if directory:
        target = repository.ensure_mech_modules()
        link = target / "linked.log"
        link.write_bytes(b"secret")
    else:
        target = link = repository.layout.result
    original_is_symlink = Path.is_symlink
    original_open = Path.open

    def report_link(path):
        return path == link or original_is_symlink(path)

    def guard_link(path, *args, **kwargs):
        assert path != link, "symlink contents must not be hashed"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "is_symlink", report_link)
    monkeypatch.setattr(Path, "open", guard_link)

    with pytest.raises(ValueError, match="symlinks are not supported"):
        repository.inspect_artifact(target)
    report = repository.verify_manifest({
        "artifacts": {"linked": {"path": target.relative_to(repository.layout.task_dir).as_posix()}},
    })

    assert report["ok"] is False
    assert report["checked"] == 0
    assert report["issues"][0]["code"] == "unsafe_artifact"
