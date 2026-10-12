"""任务重跑、失败清单与原始目录输入的端到端边界。"""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import zipfile

import pytest

from backend.application.configuration import load_config_file
from backend.application.parse_service import ParseService, ParseServiceError
from backend.contracts.runtime import ParseRequest, ParseRuntimeOptions
from backend.extensions.products.current.engine import CurrentProductParseEngine
from backend.extensions.products.current.result_serializer import result_to_dict
from backend.infrastructure.parse_artifact_session import RepositoryArtifactSession


ROOT = Path(__file__).parents[1]


class Parser:
    def parse(self, result):
        return result


def application(monkeypatch, *, discover=None, parser=None):
    class Discovery:
        def discover(self, workspace):
            if discover is not None:
                discover(workspace)
            return [], []

    engine = CurrentProductParseEngine(load_config_file(ROOT / "config.yaml"))
    monkeypatch.setattr(engine, "_load_plugins", lambda *_args: (Discovery(), parser or Parser()))
    return ParseService(
        engine, artifact_session_factory=RepositoryArtifactSession,
        result_serializer=result_to_dict,
    )


def nested_package(path, generation):
    nested = BytesIO()
    with zipfile.ZipFile(nested, "w") as archive:
        archive.writestr("data.log", generation)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("nested.zip", nested.getvalue())


def read_manifest(task):
    return json.loads((task / "parse_manifest.json").read_text(encoding="utf-8"))


def test_same_task_rerun_replaces_workspace_evidence_profile_and_dfx(tmp_path, monkeypatch):
    source = tmp_path / "logs.zip"
    nested_package(source, "first")
    seen = []
    service = application(monkeypatch, discover=lambda root: seen.append((root / "nested.zip_extracted/data.log").read_text()))
    request = ParseRequest(source, tmp_path / "out", options=ParseRuntimeOptions(keep_workspace=True, profile=True))
    service.run(request)
    task = tmp_path / "out/logs"
    (task / "extracted/first-only.log").write_text("old extracted log")
    (task / "mech_modules/first-only.log").write_text("old evidence")
    (task / "dfx_report.json").write_text("{}")
    (task / "dfx_summary.txt").write_text("old summary")
    (task / "dfx_context").mkdir()
    (task / "dfx_context/old.log").write_text("old context")
    (task / "user-notes.txt").write_text("retain this")
    nested_package(source, "second")

    service.run(ParseRequest(source, tmp_path / "out", options=ParseRuntimeOptions(keep_workspace=True)))

    assert seen == ["first", "second"]
    for relative in ("extracted/first-only.log", "mech_modules/first-only.log", "performance.json", "dfx_report.json", "dfx_summary.txt", "dfx_context"):
        assert not (task / relative).exists()
    assert (task / "user-notes.txt").read_text() == "retain this"
    assert "performance" not in read_manifest(task)["artifacts"]


@pytest.mark.parametrize("keep_workspace", [False, True])
def test_failed_repeat_drops_old_success_and_records_actual_workspace(tmp_path, monkeypatch, keep_workspace):
    source = tmp_path / "logs.zip"
    nested_package(source, "first")
    parser = Parser()
    service = application(monkeypatch, parser=parser)
    service.run(ParseRequest(source, tmp_path / "out", options=ParseRuntimeOptions(keep_workspace=True, profile=True)))
    task = tmp_path / "out/logs"

    def fail(_result):
        raise RuntimeError("synthetic parser failure")

    monkeypatch.setattr(parser, "parse", fail)
    with pytest.raises(ParseServiceError, match="synthetic parser failure") as captured:
        service.run(ParseRequest(source, tmp_path / "out", options=ParseRuntimeOptions(keep_workspace=keep_workspace)))

    manifest = read_manifest(task)
    assert captured.value.manifest_written is True
    assert manifest["status"] == "failed"
    assert manifest["diagnostics"][0]["code"] == "LP_PARSE_FAILED"
    assert manifest["workspace"]["retained"] is keep_workspace
    assert (task / "extracted").exists() is keep_workspace
    if keep_workspace:
        assert Path(manifest["workspace"]["path"]) == task / "extracted"
    assert not {"result", "metadata", "performance"}.intersection(manifest["artifacts"])
    assert all(not (task / name).exists() for name in ("result.json", "metadata.json", "performance.json"))
    monkeypatch.setattr(parser, "parse", lambda result: result)
    assert service.run(ParseRequest(source, tmp_path / "out")).status == "success"


@pytest.mark.parametrize("conflict", ["result.json", "extracted", "parse_manifest.json"])
def test_unowned_reserved_path_is_never_overwritten(tmp_path, monkeypatch, conflict):
    source = tmp_path / "logs.zip"
    nested_package(source, "first")
    task = tmp_path / "out/logs"
    task.mkdir(parents=True)
    destination = task / conflict
    if conflict == "extracted":
        destination.mkdir()
        destination /= "user.log"
    destination.write_bytes(b"user-owned content")

    with pytest.raises(ParseServiceError) as captured:
        application(monkeypatch).run(ParseRequest(source, tmp_path / "out"))

    assert captured.value.manifest_written is False
    assert destination.read_bytes() == b"user-owned content"
    assert sorted(path.name for path in task.iterdir()) == [conflict]


@pytest.mark.parametrize("field,value", [("task_id", "another-task"), ("schema_version", 99), ("artifact_contract_version", 99), ("artifact_contract_version", True), ("status", []), ("status", {}), ("artifacts", None), ("workspace", None)])
def test_invalid_task_ownership_preserves_existing_artifacts(tmp_path, monkeypatch, field, value):
    source = tmp_path / "logs.zip"
    nested_package(source, "first")
    service = application(monkeypatch)
    request = ParseRequest(source, tmp_path / "out")
    service.run(request)
    task = tmp_path / "out/logs"
    manifest = read_manifest(task)
    manifest[field] = value
    (task / "parse_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in task.iterdir() if path.is_file()}

    with pytest.raises(ParseServiceError) as captured:
        service.run(request)

    assert captured.value.manifest_written is False
    assert before == {path.name: path.read_bytes() for path in task.iterdir() if path.is_file()}


def test_reset_io_failure_replaces_old_success_manifest_with_failure(tmp_path, monkeypatch):
    source = tmp_path / "logs.zip"
    nested_package(source, "first")
    service = application(monkeypatch)
    request = ParseRequest(source, tmp_path / "out", options=ParseRuntimeOptions(keep_workspace=True))
    service.run(request)
    task = tmp_path / "out/logs"
    original = Path.unlink

    def fail_result_delete(path, *args, **kwargs):
        if path == task / "result.json":
            raise PermissionError("synthetic deletion failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_result_delete)
    with pytest.raises(ParseServiceError, match="synthetic deletion failure") as captured:
        service.run(request)
    manifest = read_manifest(task)
    assert captured.value.manifest_written is True
    assert not (task / "metadata.json").exists()
    assert manifest["status"] == "failed"
    assert manifest["diagnostics"][0]["code"] == "LP_TASK_RESET_FAILED"
    assert manifest["workspace"]["retained"] is True


def test_temporary_workspace_cleanup_failure_has_failed_stage(tmp_path, monkeypatch):
    import shutil

    source = tmp_path / "logs.zip"
    nested_package(source, "first")
    original = shutil.rmtree

    def fail_extracted_delete(path, *args, **kwargs):
        if Path(path).name == "extracted":
            raise PermissionError("synthetic workspace cleanup failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", fail_extracted_delete)
    with pytest.raises(ParseServiceError, match="synthetic workspace cleanup failure"):
        application(monkeypatch).run(ParseRequest(source, tmp_path / "out"))
    manifest = read_manifest(tmp_path / "out/logs")
    assert manifest["status"] == "failed"
    assert manifest["stages"][-1] == {"name": "pipeline.cleanup", "status": "failed"}
    assert manifest["diagnostics"][0]["code"] == "LP_WORKSPACE_CLEANUP_FAILED"


def test_raw_directory_copies_logs_and_expands_nested_archives(tmp_path, monkeypatch):
    source = tmp_path / "raw"
    (source / "diag/slot_1").mkdir(parents=True)
    (source / "empty_slot").mkdir()
    (source / "diag/slot_1/plain.log").write_text("original content")
    with zipfile.ZipFile(source / "diag/slot_1/extra.zip", "w") as archive:
        archive.writestr("nested.log", "nested content")
    before = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    seen = []

    def inspect(root):
        seen.append((root / "diag/slot_1/plain.log").read_text())
        assert (root / "empty_slot").is_dir()
        assert (root / "diag/slot_1/extra.zip_extracted/nested.log").read_text() == "nested content"

    run = application(monkeypatch, discover=inspect).run(ParseRequest(source, tmp_path / "out"))

    assert seen == ["original content"]
    assert run.workspace is None
    assert before == {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    assert not Path(run.result.extracted_root).exists()


@pytest.mark.parametrize("input_inside_task", [False, True])
def test_overlapping_input_and_task_output_are_rejected_before_writing(tmp_path, monkeypatch, input_inside_task):
    output = tmp_path / "raw" if not input_inside_task else tmp_path / "out"
    source = tmp_path / "raw" if not input_inside_task else output / "task/raw"
    source.mkdir(parents=True)
    original = source / "log.txt"
    original.write_text("unchanged")
    with pytest.raises(ParseServiceError, match="互相包含") as captured:
        application(monkeypatch).run(ParseRequest(source, output, task_id="task"))
    assert captured.value.manifest_written is False
    assert original.read_text() == "unchanged"
    assert not (output / "task/parse_manifest.json").exists()


@pytest.mark.parametrize("link_method", ["is_symlink", "is_junction"])
@pytest.mark.parametrize("location", ["source", "source_child", "task", "artifact_child"])
def test_links_are_rejected_without_changing_existing_task(tmp_path, monkeypatch, link_method, location):
    source = tmp_path / "raw"
    source.mkdir()
    child = source / "data.log"
    child.write_text("original")
    service = application(monkeypatch)
    request = ParseRequest(source, tmp_path / "out")
    service.run(request)
    task = tmp_path / "out/raw"
    evidence = task / "mech_modules/linked.log"
    evidence.write_text("protected evidence")
    selected = {"source": source, "source_child": child, "task": task, "artifact_child": evidence}[location]
    original = getattr(Path, link_method)
    monkeypatch.setattr(Path, link_method, lambda self: self == selected or original(self))
    manifest = (task / "parse_manifest.json").read_bytes()

    with pytest.raises(ParseServiceError, match="符号链接或目录联接") as captured:
        service.run(request)

    assert captured.value.manifest_written is False
    assert (task / "parse_manifest.json").read_bytes() == manifest
    assert evidence.read_text() == "protected evidence"


def test_real_directory_symlink_is_not_followed(tmp_path, monkeypatch):
    source = tmp_path / "raw"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.log").write_text("must not copy")
    link = source / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 账户不能创建符号链接；拒绝分支另有平台无关测试。")
    with pytest.raises(ParseServiceError):
        application(monkeypatch).run(ParseRequest(source, tmp_path / "out"))
    assert (outside / "private.log").read_text() == "must not copy"
    assert not (tmp_path / "out").exists()


@pytest.mark.skipif(os.name != "nt", reason="目录联接是 Windows 专用边界")
def test_real_windows_junction_is_not_followed(tmp_path, monkeypatch):
    source = tmp_path / "raw"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.log").write_text("must not copy")
    link = source / "linked"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        check=True, capture_output=True,
    )
    try:
        assert link.is_junction()
        with pytest.raises(ParseServiceError):
            application(monkeypatch).run(ParseRequest(source, tmp_path / "out"))
        assert (outside / "private.log").read_text() == "must not copy"
        assert not (tmp_path / "out").exists()
    finally:
        # Remove only the junction itself, never the referenced directory.
        link.rmdir()
