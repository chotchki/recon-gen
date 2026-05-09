"""Tests for `common/variant.py` — Y.2.gate.m.1 variant matrix
primitives.

Coverage shape (added incrementally per m.1.a → m.1.e checklist):

- m.1.a (this file's initial scope): `VariantSpec` construction,
  `__post_init__` validation, `name` property, `is_valid` cell-level
  check.
- m.1.b/c/d (later): `expand_full()`, sub-flag composer, special-form
  parsers, cross-product semantics. Tests added as those land.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quicksight_gen.common.variant import (
    DIALECTS,
    NAMED_SCENARIOS,
    TARGETS,
    ScenarioCode,
    VariantSpec,
)


# --- name property + canonical code form -----------------------------------


def test_name_named_scenario() -> None:
    spec = VariantSpec(ScenarioCode("sp"), "pg", "lo")
    assert spec.name == "sp_pg_lo"


def test_name_fuzz_scenario() -> None:
    spec = VariantSpec(ScenarioCode("f42"), "or", "lo", fuzz_seed=42)
    assert spec.name == "f42_or_lo"


def test_name_user_supplied_scenario() -> None:
    spec = VariantSpec(
        ScenarioCode("us"), "sl", "lo", user_yaml=Path("/tmp/foo.yaml"),
    )
    assert spec.name == "us_sl_lo"


# --- is_valid: invalid cell rejection (sl × aw) ----------------------------


@pytest.mark.parametrize("dialect", ["pg", "or"])
def test_is_valid_aw_with_non_sqlite(dialect: str) -> None:
    """AWS target works for postgres + oracle (the dialects QuickSight
    has remote DataSources for)."""
    spec = VariantSpec(ScenarioCode("sp"), dialect, "aw")  # pyright: ignore[reportArgumentType]: parametrize feeds str, narrowed to DialectCode at runtime
    assert spec.is_valid()


def test_is_valid_rejects_sqlite_aws() -> None:
    """SQLite × AWS is the canonical invalid cell. SQLite is file-based;
    QuickSight has no remote DataSource for it."""
    spec = VariantSpec(ScenarioCode("sp"), "sl", "aw")
    assert not spec.is_valid()


@pytest.mark.parametrize(
    "dialect,target",
    [
        ("pg", "lo"),
        ("or", "lo"),
        ("sl", "lo"),  # sqlite-local IS valid (file-based engine, no AWS needed)
        ("pg", "aw"),
        ("or", "aw"),
    ],
)
def test_is_valid_other_cells(dialect: str, target: str) -> None:
    """Every cell except sl × aw is valid."""
    spec = VariantSpec(ScenarioCode("sp"), dialect, target)  # pyright: ignore[reportArgumentType]: parametrize feeds str, narrowed to DialectCode/TargetCode at runtime
    assert spec.is_valid()


# --- __post_init__ — scenario regex + axis-specific field validation -------


@pytest.mark.parametrize("bad", ["", "sa", "spx", "fuzz", "fx", "f", "us1", "PG"])
def test_post_init_rejects_bad_scenario(bad: str) -> None:
    """Scenarios outside ``sp | sq | us | f<digits>`` raise at construction."""
    with pytest.raises(ValueError, match="invalid scenario code"):
        VariantSpec(ScenarioCode(bad), "pg", "lo")


def test_post_init_requires_fuzz_seed_for_fuzz_scenario() -> None:
    with pytest.raises(ValueError, match="requires fuzz_seed"):
        VariantSpec(ScenarioCode("f42"), "pg", "lo")  # missing fuzz_seed


def test_post_init_rejects_user_yaml_with_fuzz() -> None:
    """Fuzz seed and user_yaml are mutex per spike §"fuzz × L2-axis interaction"."""
    with pytest.raises(ValueError, match="mutex with user_yaml"):
        VariantSpec(
            ScenarioCode("f42"), "pg", "lo",
            fuzz_seed=42,
            user_yaml=Path("/tmp/foo.yaml"),
        )


def test_post_init_rejects_seed_mismatch() -> None:
    """Scenario code suffix must match fuzz_seed — avoids the
    ``f1`` + ``fuzz_seed=2`` lying-spec bug."""
    with pytest.raises(ValueError, match="doesn't match fuzz_seed"):
        VariantSpec(ScenarioCode("f1"), "pg", "lo", fuzz_seed=2)


def test_post_init_requires_user_yaml_for_us_scenario() -> None:
    with pytest.raises(ValueError, match="requires user_yaml"):
        VariantSpec(ScenarioCode("us"), "pg", "lo")  # missing user_yaml


def test_post_init_rejects_fuzz_seed_with_us() -> None:
    """``us`` scenario binds an operator-supplied yaml; fuzz_seed makes
    no sense in that path."""
    with pytest.raises(ValueError, match="mutex with fuzz_seed"):
        VariantSpec(
            ScenarioCode("us"), "pg", "lo",
            fuzz_seed=42,
            user_yaml=Path("/tmp/foo.yaml"),
        )


@pytest.mark.parametrize("named", ["sp", "sq"])
def test_post_init_rejects_extras_on_named_scenario(named: str) -> None:
    """Named scenarios (sp, sq) point at bundled fixtures; both fuzz_seed
    and user_yaml must be None."""
    with pytest.raises(ValueError, match="doesn't take"):
        VariantSpec(
            ScenarioCode(named), "pg", "lo", fuzz_seed=42,
        )
    with pytest.raises(ValueError, match="doesn't take"):
        VariantSpec(
            ScenarioCode(named), "pg", "lo",
            user_yaml=Path("/tmp/foo.yaml"),
        )


# --- frozenset constants ---------------------------------------------------


def test_named_scenarios_set() -> None:
    assert NAMED_SCENARIOS == frozenset({"sp", "sq", "us"})


def test_dialects_set() -> None:
    assert DIALECTS == frozenset({"pg", "or", "sl"})


def test_targets_set() -> None:
    assert TARGETS == frozenset({"lo", "aw"})


# --- frozen-dataclass invariant --------------------------------------------


def test_variant_spec_is_frozen() -> None:
    """`VariantSpec` is immutable — caller-side mutation would break
    the artifact-path / DB-prefix / AWS-tag deconfliction contract."""
    spec = VariantSpec(ScenarioCode("sp"), "pg", "lo")
    with pytest.raises(Exception):  # FrozenInstanceError subclass of AttributeError
        spec.scenario = ScenarioCode("sq")  # pyright: ignore[reportAttributeAccessIssue]: testing frozen-dataclass mutation rejection — assignment IS the test
