"""Y.2.gate.b.15.spec — Unit tests for the typed EnvVar registry."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from quicksight_gen.common.env_keys import (
    EnvVar,
    EnvVarInvalid,
    EnvVarRequired,
    QS_E2E_IDENTITY_REGION,
    QS_E2E_USER_ARN,
    QS_GEN_CONFIG,
    QS_GEN_DEMO_DATABASE_URL,
    QS_GEN_E2E,
    QS_GEN_FUZZ_SEED,
    QS_GEN_RUN_DIR,
    QS_GEN_RUNNER_YES,
    QS_GEN_TEST_L2_INSTANCE,
    QS_GEN_TRACE_ALL,
    _bool_coercer,
    matches,
    must_be_dir,
    must_be_file,
    must_exist,
    positive_int,
)


# ---------------------------------------------------------------------------
# Validators


def test_must_exist_passes_on_existing_path(tmp_path: Path) -> None:
    must_exist(tmp_path)  # no raise


def test_must_exist_raises_on_missing_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        must_exist(tmp_path / "nope")


def test_must_be_file_passes_on_file(tmp_path: Path) -> None:
    f = tmp_path / "x.txt"
    f.write_text("hi")
    must_be_file(f)


def test_must_be_file_raises_on_dir(tmp_path: Path) -> None:
    """A directory is not a file — the error must say so."""
    with pytest.raises(ValueError, match="not a file"):
        must_be_file(tmp_path)


def test_must_be_file_raises_on_missing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a file"):
        must_be_file(tmp_path / "nope")


def test_must_be_dir_passes_on_dir(tmp_path: Path) -> None:
    must_be_dir(tmp_path)


def test_must_be_dir_raises_on_file(tmp_path: Path) -> None:
    f = tmp_path / "x.txt"
    f.write_text("")
    with pytest.raises(ValueError, match="not a directory"):
        must_be_dir(f)


def test_positive_int_passes() -> None:
    positive_int(1)
    positive_int(2 ** 32)


def test_positive_int_raises_on_zero_or_negative() -> None:
    with pytest.raises(ValueError, match="positive"):
        positive_int(0)
    with pytest.raises(ValueError, match="positive"):
        positive_int(-5)


def test_matches_passes_on_full_match() -> None:
    check = matches(re.compile(r"[a-z]{2}-[a-z]+-\d+"))
    check("us-east-1")  # no raise


def test_matches_raises_on_partial_match() -> None:
    """`fullmatch` semantics — partial match is rejected."""
    check = matches(re.compile(r"[a-z]{2}-[a-z]+-\d+"))
    with pytest.raises(ValueError, match="does not match"):
        check("us-east-1-extra-stuff")


def test_matches_raises_on_no_match() -> None:
    check = matches(re.compile(r"\d+"))
    with pytest.raises(ValueError, match="does not match"):
        check("abc")


def test_bool_coercer_truthy_for_any_nonempty() -> None:
    assert _bool_coercer("1") is True
    assert _bool_coercer("0") is True  # mirrors bool(os.environ.get(...))
    assert _bool_coercer("false") is True  # ditto — non-empty = True
    assert _bool_coercer("yes") is True


def test_bool_coercer_falsy_for_empty_string() -> None:
    assert _bool_coercer("") is False


# ---------------------------------------------------------------------------
# EnvVar.get_or_none


def test_get_or_none_returns_none_when_unset(monkeypatch: Any) -> None:
    spec = EnvVar(name="UNIT_TEST_FAKE_VAR", description="x", coercer=str)
    monkeypatch.delenv("UNIT_TEST_FAKE_VAR", raising=False)
    assert spec.get_or_none() is None


def test_get_or_none_returns_none_when_empty(monkeypatch: Any) -> None:
    """Empty string is treated as unset — matches existing
    ``bool(os.environ.get(...))`` semantics."""
    spec = EnvVar(name="UNIT_TEST_FAKE_VAR", description="x", coercer=str)
    monkeypatch.setenv("UNIT_TEST_FAKE_VAR", "")
    assert spec.get_or_none() is None


def test_get_or_none_coerces(monkeypatch: Any) -> None:
    spec = EnvVar(name="UNIT_TEST_FAKE_INT", description="x", coercer=int)
    monkeypatch.setenv("UNIT_TEST_FAKE_INT", "42")
    assert spec.get_or_none() == 42


def test_get_or_none_runs_validator(monkeypatch: Any, tmp_path: Path) -> None:
    spec = EnvVar(
        name="UNIT_TEST_FAKE_PATH",
        description="x",
        coercer=Path,
        validator=must_be_dir,
    )
    monkeypatch.setenv("UNIT_TEST_FAKE_PATH", str(tmp_path))
    assert spec.get_or_none() == tmp_path


def test_get_or_none_raises_envvar_invalid_on_validator_failure(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """Validator ValueError → EnvVarInvalid carrying name +
    description (operator-actionable shape)."""
    spec = EnvVar(
        name="UNIT_TEST_FAKE_PATH",
        description="must point at an existing dir",
        coercer=Path,
        validator=must_be_dir,
    )
    monkeypatch.setenv("UNIT_TEST_FAKE_PATH", str(tmp_path / "nope"))
    with pytest.raises(EnvVarInvalid) as exc_info:
        spec.get_or_none()
    msg = str(exc_info.value)
    assert "UNIT_TEST_FAKE_PATH" in msg
    assert "must point at an existing dir" in msg


def test_get_or_none_raises_envvar_invalid_on_coercion_failure(
    monkeypatch: Any,
) -> None:
    """Coercer ValueError → EnvVarInvalid (e.g. int('abc'))."""
    spec = EnvVar(name="UNIT_TEST_FAKE_INT", description="positive int", coercer=int)
    monkeypatch.setenv("UNIT_TEST_FAKE_INT", "not-an-int")
    with pytest.raises(EnvVarInvalid, match="coercion failed"):
        spec.get_or_none()


# ---------------------------------------------------------------------------
# EnvVar.require


def test_require_returns_value_when_set(monkeypatch: Any) -> None:
    spec = EnvVar(name="UNIT_TEST_FAKE_VAR", description="x", coercer=str)
    monkeypatch.setenv("UNIT_TEST_FAKE_VAR", "hello")
    assert spec.require() == "hello"


def test_require_raises_envvar_required_when_unset(monkeypatch: Any) -> None:
    """The error message must carry the description so the operator
    can fix it without grepping the codebase."""
    spec = EnvVar(
        name="UNIT_TEST_FAKE_VAR",
        description="must be set for X to work",
        coercer=str,
    )
    monkeypatch.delenv("UNIT_TEST_FAKE_VAR", raising=False)
    with pytest.raises(EnvVarRequired) as exc_info:
        spec.require()
    msg = str(exc_info.value)
    assert "UNIT_TEST_FAKE_VAR" in msg
    assert "must be set for X to work" in msg


def test_require_raises_envvar_required_when_empty(monkeypatch: Any) -> None:
    spec = EnvVar(name="UNIT_TEST_FAKE_VAR", description="x", coercer=str)
    monkeypatch.setenv("UNIT_TEST_FAKE_VAR", "")
    with pytest.raises(EnvVarRequired):
        spec.require()


# ---------------------------------------------------------------------------
# EnvVar.serialize


def test_serialize_returns_str(tmp_path: Path) -> None:
    spec = EnvVar(name="UNIT_TEST_FAKE", description="x", coercer=Path)
    assert spec.serialize(tmp_path) == str(tmp_path)


def test_serialize_runs_validator_set_side(tmp_path: Path) -> None:
    """Validator runs in BOTH directions — set-side bug ('forgot to
    mkdir') surfaces at the same boundary as get-side bug."""
    spec = EnvVar(
        name="UNIT_TEST_FAKE",
        description="must be a real dir",
        coercer=Path,
        validator=must_be_dir,
    )
    with pytest.raises(EnvVarInvalid, match="not a directory"):
        spec.serialize(tmp_path / "missing")


def test_serialize_no_validator_passes_through(tmp_path: Path) -> None:
    """When the spec has no validator, serialize is just str()."""
    spec = EnvVar(name="UNIT_TEST_FAKE", description="x", coercer=Path)
    assert spec.serialize(tmp_path / "anything") == str(tmp_path / "anything")


# ---------------------------------------------------------------------------
# Spec sanity — all canonical env vars are well-formed


def test_canonical_specs_are_present() -> None:
    """A regression guard — if someone deletes a spec, this catches
    it. Every name on this list is referenced by the runner / config
    / harness wiring."""
    expected_names = {
        "QS_GEN_RUN_DIR",
        "QS_GEN_LAYER",
        "QS_GEN_E2E",
        "QS_GEN_DEMO_DATABASE_URL",
        "QS_GEN_TRACE_ALL",
        "QS_GEN_FUZZ_SEED",
        "QS_GEN_RUNNER_YES",
        "QS_GEN_CONFIG",
        "QS_GEN_TEST_L2_INSTANCE",
        "QS_E2E_USER_ARN",
        "QS_E2E_PAGE_TIMEOUT",
        "QS_E2E_VISUAL_TIMEOUT",
        "QS_E2E_IDENTITY_REGION",
    }
    actual_specs = [
        QS_GEN_RUN_DIR, QS_GEN_E2E, QS_GEN_DEMO_DATABASE_URL,
        QS_GEN_TRACE_ALL, QS_GEN_FUZZ_SEED, QS_GEN_RUNNER_YES,
        QS_GEN_CONFIG, QS_GEN_TEST_L2_INSTANCE,
        QS_E2E_USER_ARN, QS_E2E_IDENTITY_REGION,
    ]
    actual_names = {s.name for s in actual_specs}
    # Subset because we don't import all 13 here (LAYER + the two
    # E2E timeouts intentionally omitted from the smoke check).
    missing = (expected_names - {
        "QS_GEN_LAYER",
        "QS_E2E_PAGE_TIMEOUT",
        "QS_E2E_VISUAL_TIMEOUT",
    }) - actual_names
    assert not missing, f"specs missing from registry: {missing}"


def test_path_specs_have_validators() -> None:
    """Every Path-shaped spec must carry a validator (per b.15.spec
    acceptance criterion #2). Catches regressions where someone
    adds a Path-shaped EnvVar but forgets to wire ``must_exist`` /
    ``must_be_file`` / ``must_be_dir``."""
    path_specs = [
        QS_GEN_RUN_DIR,
        QS_GEN_CONFIG,
        QS_GEN_TEST_L2_INSTANCE,
    ]
    for spec in path_specs:
        assert spec.validator is not None, (
            f"{spec.name} is Path-shaped but has no validator — "
            "wire must_exist / must_be_file / must_be_dir"
        )


def test_user_arn_validator_rejects_non_arn(monkeypatch: Any) -> None:
    """The IAM ARN regex catches 'looks-like-an-arn-but-isn't'."""
    monkeypatch.setenv(QS_E2E_USER_ARN.name, "not-an-arn")
    with pytest.raises(EnvVarInvalid, match="does not match"):
        QS_E2E_USER_ARN.require()


def test_user_arn_validator_accepts_real_arn(monkeypatch: Any) -> None:
    monkeypatch.setenv(
        QS_E2E_USER_ARN.name,
        "arn:aws:quicksight:us-east-1:470656905821:user/default/test-user",
    )
    assert "test-user" in QS_E2E_USER_ARN.require()


def test_identity_region_rejects_non_region(monkeypatch: Any) -> None:
    monkeypatch.setenv(QS_E2E_IDENTITY_REGION.name, "USEAST1")
    with pytest.raises(EnvVarInvalid, match="does not match"):
        QS_E2E_IDENTITY_REGION.get_or_none()


def test_identity_region_accepts_real_region(monkeypatch: Any) -> None:
    monkeypatch.setenv(QS_E2E_IDENTITY_REGION.name, "us-east-1")
    assert QS_E2E_IDENTITY_REGION.get_or_none() == "us-east-1"


def test_fuzz_seed_rejects_negative(monkeypatch: Any) -> None:
    monkeypatch.setenv(QS_GEN_FUZZ_SEED.name, "-1")
    with pytest.raises(EnvVarInvalid, match="positive"):
        QS_GEN_FUZZ_SEED.get_or_none()
