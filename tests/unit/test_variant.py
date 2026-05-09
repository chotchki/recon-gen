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
    DEFAULT_DIALECTS,
    DEFAULT_SCENARIOS_NAMED,
    DEFAULT_TARGETS,
    DIALECTS,
    NAMED_SCENARIOS,
    TARGETS,
    ScenarioCode,
    ScenarioSpec,
    VariantSpec,
    compose_matrix,
    derive_default_fuzz_seed,
    expand_full,
    parse_dialects,
    parse_scenarios,
    parse_targets,
    parse_variant_code,
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


# --- m.1.b: expand_full() default matrix ----------------------------------


def test_expand_full_cell_count() -> None:
    """13 cells per spike §"`full` matrix definition" — locked
    structure. If this number changes, audit + spike + PLAN must
    update together."""
    assert len(expand_full()) == 13


def test_expand_full_no_invalid_cells() -> None:
    """Invariant: every cell `expand_full()` returns satisfies
    `is_valid()`. The matrix expander never emits cells the runner
    would have to skip — invalid combinations are excluded by
    construction, not filtered at the caller."""
    for cell in expand_full():
        assert cell.is_valid(), f"matrix included invalid cell: {cell.name}"


def test_expand_full_named_local_cells() -> None:
    """6 cells: ``{sp, sq} × {pg, or, sl} × {lo}``."""
    cells = expand_full()
    named_local = [
        c for c in cells
        if c.scenario in ("sp", "sq") and c.target == "lo"
    ]
    assert len(named_local) == 6
    expected_names = {
        f"{sc}_{di}_lo" for sc in ("sp", "sq") for di in ("pg", "or", "sl")
    }
    assert {c.name for c in named_local} == expected_names


def test_expand_full_named_aws_cells() -> None:
    """4 cells: ``{sp, sq} × {pg, or} × {aw}`` (sl × aw excluded)."""
    cells = expand_full()
    named_aws = [
        c for c in cells
        if c.scenario in ("sp", "sq") and c.target == "aw"
    ]
    assert len(named_aws) == 4
    expected_names = {
        f"{sc}_{di}_aw" for sc in ("sp", "sq") for di in ("pg", "or")
    }
    assert {c.name for c in named_aws} == expected_names


def test_expand_full_fuzz_cells_share_seed() -> None:
    """3 fuzz cells (one per dialect) all carry the SAME seed —
    cross-dialect coverage on identical synthesized L2 topology.
    Locked design choice (spike §"`full` matrix definition")."""
    fuzz_cells = [c for c in expand_full() if c.scenario.startswith("f")]
    assert len(fuzz_cells) == 3
    seeds = {c.fuzz_seed for c in fuzz_cells}
    assert len(seeds) == 1, f"expected all 3 fuzz cells to share one seed; got {seeds}"
    assert all(c.target == "lo" for c in fuzz_cells)
    assert {c.dialect for c in fuzz_cells} == {"pg", "or", "sl"}


def test_expand_full_fuzz_excluded_from_aws() -> None:
    """No fuzz cells on `aw` target — cost-control default per spike.
    Operator opts in via ``--scenarios=fuzz:N --targets=aw``
    (sub-flag composer territory; m.1.c)."""
    aws_fuzz = [
        c for c in expand_full()
        if c.scenario.startswith("f") and c.target == "aw"
    ]
    assert aws_fuzz == []


def test_expand_full_no_us_scenarios() -> None:
    """`us_*_*` cells excluded from default `full` — operator must
    opt in via ``--scenarios=us:<path>`` (m.1.d special-form parser)."""
    assert not any(c.scenario == "us" for c in expand_full())


def test_expand_full_random_seed_default() -> None:
    """Two consecutive `expand_full()` calls produce different fuzz
    seeds (random by default per m.3 + audit §7.11). Collision
    probability ~2^-32 — if this fails repeatedly something's wrong
    with the RNG path."""
    cells_a = expand_full()
    cells_b = expand_full()
    seed_a = next(c.fuzz_seed for c in cells_a if c.scenario.startswith("f"))
    seed_b = next(c.fuzz_seed for c in cells_b if c.scenario.startswith("f"))
    assert seed_a != seed_b


def test_derive_default_fuzz_seed_in_uint32_range() -> None:
    """Seed is a 32-bit unsigned int — fits in any sensible serialization
    + matches `random_l2_yaml(seed: int)`'s domain."""
    for _ in range(20):
        seed = derive_default_fuzz_seed()
        assert 0 <= seed < 2**32


# --- m.1.c: compose_matrix() sub-flag composer ----------------------------


def test_compose_no_args_equals_expand_full() -> None:
    """All None → expand_full() directly. Same cell count + same shape."""
    composed = compose_matrix()
    full = expand_full()
    assert len(composed) == len(full) == 13


def test_compose_dialects_only_narrows() -> None:
    """`--dialects=pg` alone → cross-product mode. Defaults: named
    scenarios × pg × all targets. {sp, sq} × {pg} × {lo, aw} = 4 cells."""
    cells = compose_matrix(dialects=["pg"])
    assert len(cells) == 4
    assert {c.name for c in cells} == {"sp_pg_lo", "sp_pg_aw", "sq_pg_lo", "sq_pg_aw"}


def test_compose_targets_only_narrows() -> None:
    """`--targets=lo` alone → {sp, sq} × all dialects × {lo} = 6 cells.
    Note: NO fuzz cells — sub-flag mode uses DEFAULT_SCENARIOS_NAMED
    which excludes fuzz (operator names fuzz explicitly if wanted)."""
    cells = compose_matrix(targets=["lo"])
    assert len(cells) == 6
    assert all(c.target == "lo" for c in cells)
    assert {c.scenario for c in cells} == {"sp", "sq"}
    assert {c.dialect for c in cells} == {"pg", "or", "sl"}


def test_compose_scenarios_only_named() -> None:
    """`--scenarios=sp` → {sp} × all dialects × all targets, filtered
    for is_valid(). sp × {pg, or, sl} × {lo, aw} = 6 minus invalid
    sp_sl_aw = 5 cells."""
    cells = compose_matrix(scenarios=[ScenarioSpec(ScenarioCode("sp"))])
    assert len(cells) == 5
    assert {c.name for c in cells} == {
        "sp_pg_lo", "sp_or_lo", "sp_sl_lo", "sp_pg_aw", "sp_or_aw",
    }


def test_compose_invalid_cells_filtered() -> None:
    """`is_valid()` filter applies in cross-product mode — sl × aw cells
    auto-skip even when targets=aw includes them."""
    cells = compose_matrix(dialects=["sl"], targets=["aw"])
    # {sp, sq} × {sl} × {aw} would be 2 cells, but both invalid → 0
    assert cells == []


def test_compose_with_explicit_fuzz_scenario() -> None:
    """Caller passes pre-built ScenarioSpec for fuzz (with fuzz_seed
    set). Cross-product spreads it across specified axes."""
    fuzz = ScenarioSpec(ScenarioCode("f12345"), fuzz_seed=12345)
    cells = compose_matrix(scenarios=[fuzz], dialects=["pg", "or"], targets=["lo"])
    assert len(cells) == 2
    assert {c.name for c in cells} == {"f12345_pg_lo", "f12345_or_lo"}
    assert all(c.fuzz_seed == 12345 for c in cells)


def test_compose_with_user_supplied_scenario() -> None:
    """`us` scenario carries user_yaml through to each cell."""
    yaml_path = Path("/tmp/customer_acme.yaml")
    us = ScenarioSpec(ScenarioCode("us"), user_yaml=yaml_path)
    cells = compose_matrix(scenarios=[us], dialects=["pg"], targets=["lo"])
    assert len(cells) == 1
    assert cells[0].name == "us_pg_lo"
    assert cells[0].user_yaml == yaml_path


def test_compose_explicit_full_intent_via_sub_flags() -> None:
    """Operator who DOES want full local matrix can spell it explicitly:
    --scenarios=sp,sq --dialects=pg,or,sl --targets=lo = 6 cells."""
    cells = compose_matrix(
        scenarios=[
            ScenarioSpec(ScenarioCode("sp")),
            ScenarioSpec(ScenarioCode("sq")),
        ],
        dialects=["pg", "or", "sl"],
        targets=["lo"],
    )
    assert len(cells) == 6


def test_compose_default_constants() -> None:
    """Sanity check on the constants referenced when sub-flag axes are
    unspecified."""
    assert DEFAULT_SCENARIOS_NAMED == (ScenarioCode("sp"), ScenarioCode("sq"))
    assert DEFAULT_DIALECTS == ("pg", "or", "sl")
    assert DEFAULT_TARGETS == ("lo", "aw")


# --- m.1.d: special-form parsers ------------------------------------------


# parse_scenarios — comma-separated; named + fuzz + us special-forms


def test_parse_scenarios_named() -> None:
    specs = parse_scenarios("sp,sq")
    assert len(specs) == 2
    assert specs[0] == ScenarioSpec(ScenarioCode("sp"))
    assert specs[1] == ScenarioSpec(ScenarioCode("sq"))


def test_parse_scenarios_fuzz_no_count() -> None:
    """`fuzz` (no `:N`) → 1 random fuzz seed."""
    specs = parse_scenarios("fuzz")
    assert len(specs) == 1
    assert specs[0].scenario.startswith("f")
    assert specs[0].fuzz_seed is not None
    assert specs[0].scenario == f"f{specs[0].fuzz_seed}"


def test_parse_scenarios_fuzz_with_count() -> None:
    """`fuzz:5` → 5 random fuzz seeds, all distinct (with high probability)."""
    specs = parse_scenarios("fuzz:5")
    assert len(specs) == 5
    seeds = [s.fuzz_seed for s in specs]
    assert all(s is not None for s in seeds)
    # 5 random uint32s — collision probability ~5*4/2 / 2^32 ≈ 2.3e-9.
    assert len(set(seeds)) == 5


def test_parse_scenarios_us() -> None:
    specs = parse_scenarios("us:run/customer.yaml")
    assert len(specs) == 1
    assert specs[0].scenario == "us"
    assert specs[0].user_yaml == Path("run/customer.yaml")


def test_parse_scenarios_mixed() -> None:
    """Comma-separated mix of named + fuzz + us — composes."""
    specs = parse_scenarios("sp,fuzz:2,us:foo.yaml")
    assert len(specs) == 4  # 1 sp + 2 fuzz + 1 us
    assert specs[0].scenario == "sp"
    assert specs[1].scenario.startswith("f")
    assert specs[2].scenario.startswith("f")
    assert specs[3].scenario == "us"


def test_parse_scenarios_whitespace_tolerated() -> None:
    """Operators may put spaces around commas."""
    specs = parse_scenarios(" sp , sq ")
    assert [s.scenario for s in specs] == ["sp", "sq"]


def test_parse_scenarios_empty_value_rejected() -> None:
    with pytest.raises(ValueError, match="value is empty"):
        parse_scenarios("")
    with pytest.raises(ValueError, match="value is empty"):
        parse_scenarios("   ")


def test_parse_scenarios_empty_entry_rejected() -> None:
    with pytest.raises(ValueError, match="empty entry"):
        parse_scenarios("sp,,sq")


def test_parse_scenarios_unknown_entry_rejected() -> None:
    with pytest.raises(ValueError, match="unknown entry"):
        parse_scenarios("sp,xyz")


def test_parse_scenarios_fuzz_count_non_int() -> None:
    with pytest.raises(ValueError, match="fuzz count must be an integer"):
        parse_scenarios("fuzz:abc")


def test_parse_scenarios_fuzz_count_zero_rejected() -> None:
    with pytest.raises(ValueError, match="fuzz count must be ≥1"):
        parse_scenarios("fuzz:0")


def test_parse_scenarios_us_missing_path_rejected() -> None:
    with pytest.raises(ValueError, match="us:<path> missing path"):
        parse_scenarios("us:")


# parse_dialects + parse_targets


def test_parse_dialects_csv() -> None:
    assert parse_dialects("pg,or") == ["pg", "or"]


def test_parse_dialects_unknown_rejected() -> None:
    with pytest.raises(ValueError, match="unknown code"):
        parse_dialects("pg,xyz")


def test_parse_dialects_empty_rejected() -> None:
    with pytest.raises(ValueError, match="value is empty"):
        parse_dialects("")


def test_parse_targets_csv() -> None:
    assert parse_targets("lo,aw") == ["lo", "aw"]


def test_parse_targets_unknown_rejected() -> None:
    with pytest.raises(ValueError, match="unknown code"):
        parse_targets("lo,xyz")


def test_parse_targets_empty_rejected() -> None:
    with pytest.raises(ValueError, match="value is empty"):
        parse_targets("")


# parse_variant_code — single-cell triage escape hatch


def test_parse_variant_code_named() -> None:
    spec = parse_variant_code("sp_pg_lo")
    assert spec.scenario == "sp"
    assert spec.dialect == "pg"
    assert spec.target == "lo"
    assert spec.fuzz_seed is None


def test_parse_variant_code_fuzz_extracts_seed() -> None:
    """Fuzz seed integer is extracted from the scenario suffix."""
    spec = parse_variant_code("f12345_or_lo")
    assert spec.scenario == "f12345"
    assert spec.fuzz_seed == 12345
    assert spec.dialect == "or"


def test_parse_variant_code_us_rejected() -> None:
    """`us` cells require operator-supplied yaml; --variants= triage
    doesn't carry that context. Error directs operator to
    --scenarios=us:<path>."""
    with pytest.raises(ValueError, match="us cells require an operator-supplied yaml"):
        parse_variant_code("us_pg_lo")


@pytest.mark.parametrize("bad", [
    "sp_pg",       # too few components
    "sp_pg_lo_x",  # too many components
    "SP_PG_LO",    # uppercase rejected
    "sp-pg-lo",    # wrong separator
    "sp_xyz_lo",   # bad dialect
    "sp_pg_xyz",   # bad target
    "fa_pg_lo",    # f without digit
    "",            # empty
])
def test_parse_variant_code_bad(bad: str) -> None:
    with pytest.raises(ValueError, match="unknown code"):
        parse_variant_code(bad)


def test_parse_variant_code_invalid_cell_constructs() -> None:
    """Bug guard: parse_variant_code constructs the spec, but invalid
    cells (sl × aw) construct fine — caller checks is_valid() if it
    cares. The triage path may want to inspect why a cell is invalid."""
    spec = parse_variant_code("sp_sl_aw")
    assert not spec.is_valid()
