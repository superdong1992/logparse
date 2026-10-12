"""Journal file failures retain preceding evidence and isolate later sources."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.extensions.products.current.models import JournalLogFile, PrivateSlotInfo
from backend.parsing.mech_journal_scanner import MechJournalScanner
from backend.parsing.process_name_resolver import ProcessNameResolver
from backend.parsing.timestamp_extractor import TimestampExtractor


def _scanner() -> MechJournalScanner:
    return MechJournalScanner(
        journal_re=None,
        journal_re2=re.compile(r"^\S+\s+\S+\s+(\S+?)-(\d+):\s+No\[(\d+)\](.+)$"),
        journal_keyword="example",
        seq_re=re.compile(r"No\[(\d+)\]"),
        line_pattern2_required_substrings=None,
        master_keyword=None,
        resolver=ProcessNameResolver(),
        module_name_upper="EXAMPLE",
        ts_extractor=TimestampExtractor(re.compile(
            r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})([+-]\d{2}:\d{2})?",
        )),
    )


def _source(paths: list[Path], *, cpu_id: str = "0") -> PrivateSlotInfo:
    return PrivateSlotInfo(
        dir_name=f"slot_1_cpu_{cpu_id}", slot_id="1", cpu_id=cpu_id,
        path=str(paths[0].parent),
        journal_logs=[JournalLogFile(path=str(path), name=path.name) for path in paths],
    )


def _line(sequence: int) -> str:
    return f"2026-01-03T00:01:0{sequence} host SERVICE-123: No[{sequence}] EXAMPLE log"


@pytest.mark.parametrize("failure", ["missing", "bad_gzip", "permission"])
def test_journal_read_failure_is_reported_and_next_file_is_scanned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    bad = tmp_path / ("bad.log.gz" if failure == "bad_gzip" else "bad.log")
    good = tmp_path / "good.log"
    good.write_text(_line(2) + "\n", encoding="utf-8")
    if failure == "bad_gzip":
        bad.write_bytes(b"not a gzip file")
    elif failure == "permission":
        original_open = Path.open

        def deny_bad(path, *args, **kwargs):
            if path == bad:
                raise PermissionError("private raw details")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", deny_bad)
    diagnostics = []

    entries = _scanner().scan(_source([bad, good]), None, diagnostic_sink=diagnostics.append)

    assert [entry.sequence for entry in entries] == [2]
    assert entries[0].cpu_id == ""
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "LP_JOURNAL_READ_FAILED"
    assert diagnostics[0].details["source"] == f"slot_1_cpu_0/{bad.name}"
    assert "private raw details" not in diagnostics[0].message


@pytest.mark.parametrize("error", [OSError, EOFError])
def test_interrupted_journal_retains_prefix_and_processes_the_next_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[Exception],
) -> None:
    def interrupted(path):
        if path.name == "broken.log":
            yield _line(1)
            raise error("read interrupted")
        yield _line(2)

    monkeypatch.setattr(
        "backend.parsing.mech_journal_scanner.iter_text_file_lines", interrupted,
    )
    diagnostics = []
    entries = _scanner().scan(
        _source([tmp_path / "broken.log", tmp_path / "good.log"], cpu_id="2"),
        None,
        diagnostic_sink=diagnostics.append,
    )

    assert [entry.sequence for entry in entries] == [1, 2]
    assert [entry.cpu_id for entry in entries] == ["2", "2"]
    assert diagnostics[0].details["reason"] == error.__name__


def test_journal_read_failure_is_logged_without_diagnostic_sink(tmp_path: Path, caplog) -> None:
    entries = _scanner().scan(_source([tmp_path / "missing.log"]), None)

    assert entries == []
    assert "Journal 日志读取失败" in caplog.text
