from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from backend.contracts.product_onboarding import OnboardingInputError, SamplingLimits
from backend.infrastructure.product_onboarding_candidate import SafeCandidateReader
from backend.infrastructure.product_onboarding_samples import SecureSampleReader


def test_plain_and_gzip_samples_are_streamed_in_deterministic_order(
    tmp_path: Path,
) -> None:
    plain = tmp_path / "z.log"
    compressed = tmp_path / "a.log.gz"
    plain.write_text("plain\n", encoding="utf-8")
    with gzip.open(compressed, "wt", encoding="utf-8") as stream:
        stream.write("compressed\n")

    batch = SecureSampleReader().read(
        [plain, compressed], encoding="utf-8", limits=SamplingLimits()
    )

    assert [sample.name for sample in batch.files] == ["a.log.gz", "z.log"]
    assert batch.files[0].compressed is True
    assert batch.files[0].lines == ("compressed",)


def test_global_budget_is_fair_across_explicit_files(tmp_path: Path) -> None:
    first = tmp_path / "a.log"
    second = tmp_path / "b.log"
    first.write_text("abcdefghij\n", encoding="utf-8")
    second.write_text("klmnopqrst\n", encoding="utf-8")

    batch = SecureSampleReader().read(
        [second, first],
        encoding="utf-8",
        limits=SamplingLimits(
            max_lines_per_file=2,
            max_characters_per_line=20,
            max_total_characters=10,
        ),
    )

    assert [sample.sampled_characters for sample in batch.files] == [5, 5]
    assert all(sample.truncated for sample in batch.files)


@pytest.mark.parametrize("kind", ["directory", "archive", "duplicate"])
def test_unsafe_input_shapes_are_rejected_without_paths(
    tmp_path: Path,
    kind: str,
) -> None:
    log = tmp_path / "sample.log"
    log.write_text("PRIVATE_BODY\n", encoding="utf-8")
    if kind == "directory":
        target = tmp_path / "logs"
        target.mkdir()
        values = [target]
    elif kind == "archive":
        target = tmp_path / "logs.zip"
        target.write_bytes(b"PRIVATE_BODY")
        values = [target]
    else:
        values = [log, log]

    with pytest.raises(OnboardingInputError) as captured:
        SecureSampleReader().read(values, encoding="utf-8", limits=SamplingLimits())

    assert str(tmp_path) not in repr(captured.value)
    assert "PRIVATE_BODY" not in repr(captured.value)


def test_casefolded_basename_conflicts_are_rejected(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    first = left / "Sample.LOG"
    second = right / "sample.log"
    first.write_text("a\n", encoding="utf-8")
    second.write_text("b\n", encoding="utf-8")

    with pytest.raises(OnboardingInputError) as captured:
        SecureSampleReader().read([first, second], encoding="utf-8", limits=SamplingLimits())

    assert captured.value.code == "LP_ONBOARD_INPUT_BASENAME_CONFLICT"


def test_symbolic_link_is_rejected_when_supported(tmp_path: Path) -> None:
    target = tmp_path / "target.log"
    link = tmp_path / "link.log"
    target.write_text("line\n", encoding="utf-8")
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(OnboardingInputError) as captured:
        SecureSampleReader().read([link], encoding="utf-8", limits=SamplingLimits())

    assert captured.value.code == "LP_ONBOARD_INPUT_SYMLINK_REJECTED"


def test_broken_gzip_and_non_text_codec_fail_safely(tmp_path: Path) -> None:
    broken = tmp_path / "broken.log.gz"
    broken.write_bytes(b"PRIVATE_BROKEN_GZIP")
    reader = SecureSampleReader()

    with pytest.raises(OnboardingInputError) as gzip_error:
        reader.read([broken], encoding="utf-8", limits=SamplingLimits())
    with pytest.raises(OnboardingInputError) as codec_error:
        reader.read([broken], encoding="base64_codec", limits=SamplingLimits())

    assert str(tmp_path) not in repr(gzip_error.value)
    assert "PRIVATE_BROKEN_GZIP" not in repr(gzip_error.value)
    assert codec_error.value.code == "LP_ONBOARD_INPUT_ENCODING_INVALID"


def test_binary_markers_and_decode_replacements_never_enter_repr(tmp_path: Path) -> None:
    secret = "PRIVATE_BINARY_BODY"
    log = tmp_path / "binary.log"
    log.write_bytes(b"\xff\x00" + secret.encode("ascii") + b"\n")

    batch = SecureSampleReader().read([log], encoding="utf-8", limits=SamplingLimits())

    assert batch.files[0].binary_likely is True
    assert batch.files[0].replacement_characters > 0
    assert secret not in repr(batch)
    assert str(tmp_path) not in repr(batch)


def test_uppercase_gzip_is_streamed_but_name_is_preserved(tmp_path: Path) -> None:
    log = tmp_path / "sample.log.GZ"
    with gzip.open(log, "wt", encoding="utf-8") as stream:
        stream.write("line\n")

    batch = SecureSampleReader().read([log], encoding="utf-8", limits=SamplingLimits())

    assert batch.files[0].compressed is True
    assert batch.files[0].name == "sample.log.GZ"


def test_candidate_reader_accepts_versioned_envelope(tmp_path: Path) -> None:
    path = tmp_path / "candidate.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "adapter": "adapter-a",
                "pattern": "value",
            }
        ),
        encoding="utf-8",
    )

    document = SafeCandidateReader().read(path)

    assert document.schema_version == 1
    assert document.adapter == "adapter-a"
    assert dict(document.payload) == {"pattern": "value"}


@pytest.mark.parametrize(
    "body",
    [
        "PRIVATE_NOT_JSON",
        '{"schema_version":1,"schema_version":1,"adapter":"a"}',
        '{"schema_version":1,"adapter":"a","value":NaN}',
        '{"schema_version":1,"adapter":"a","value":' + "[" * 30 + "0" + "]" * 30 + "}",
        '{"schema_version":1,"adapter":"a","value":' + "9" * 5000 + "}",
    ],
)
def test_malformed_or_deep_candidate_is_rejected_without_content(
    tmp_path: Path,
    body: str,
) -> None:
    path = tmp_path / "candidate.json"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(OnboardingInputError) as captured:
        SafeCandidateReader().read(path)

    assert str(tmp_path) not in repr(captured.value)
    assert "PRIVATE_NOT_JSON" not in repr(captured.value)


def test_oversized_candidate_is_rejected_before_json_parse(tmp_path: Path) -> None:
    path = tmp_path / "candidate.json"
    path.write_text("x" * (64 * 1024 + 1), encoding="utf-8")

    with pytest.raises(OnboardingInputError) as captured:
        SafeCandidateReader().read(path)

    assert captured.value.code == "LP_ONBOARD_CANDIDATE_DOCUMENT_TOO_LARGE"
