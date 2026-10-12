"""Candidate reader rejects inaccessible input and enforces read-time bounds."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from backend.contracts.product_onboarding import OnboardingInputError
from backend.infrastructure.product_onboarding_candidate import SafeCandidateReader


@pytest.mark.parametrize("kind", ["invalid_path", "missing", "directory", "invalid_utf8"])
def test_candidate_input_failures_do_not_expose_paths_or_content(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "private-candidate.json"
    value = path
    if kind == "invalid_path":
        value = None
    elif kind == "directory":
        path.mkdir()
    elif kind == "invalid_utf8":
        path.write_bytes(b"\xffPRIVATE_CONTENT")

    with pytest.raises(OnboardingInputError) as caught:
        SafeCandidateReader().read(value)

    assert caught.value.code == "LP_ONBOARD_CANDIDATE_DOCUMENT_INVALID"
    assert "private-candidate" not in str(caught.value)
    assert "PRIVATE_CONTENT" not in str(caught.value)


def test_candidate_permission_failure_is_a_safe_input_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "candidate.json"
    path.write_text("{}", encoding="utf-8")

    def deny_open(*_args, **_kwargs):
        raise PermissionError("PRIVATE_DETAILS")

    monkeypatch.setattr(Path, "open", deny_open)
    with pytest.raises(OnboardingInputError) as caught:
        SafeCandidateReader().read(path)

    assert caught.value.code == "LP_ONBOARD_CANDIDATE_DOCUMENT_INVALID"
    assert "PRIVATE_DETAILS" not in str(caught.value)


def test_candidate_growth_after_stat_is_still_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "candidate.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: io.BytesIO(b"x" * (64 * 1024 + 1)))

    with pytest.raises(OnboardingInputError) as caught:
        SafeCandidateReader().read(path)

    assert caught.value.code == "LP_ONBOARD_CANDIDATE_DOCUMENT_TOO_LARGE"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"schema_version": True, "adapter": "a"},
        {"schema_version": "1", "adapter": "a"},
        {"schema_version": 1, "adapter": " a "},
        {"schema_version": 1, "adapter": "a\nsecret"},
        {"schema_version": 1, "adapter": "a", "pattern": "x" * (16 * 1024 + 1)},
    ],
)
def test_candidate_rejects_invalid_envelope_and_oversized_individual_strings(
    tmp_path: Path, payload,
) -> None:
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(OnboardingInputError) as caught:
        SafeCandidateReader().read(path)

    assert caught.value.code == "LP_ONBOARD_CANDIDATE_DOCUMENT_INVALID"
