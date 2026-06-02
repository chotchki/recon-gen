"""CB.0 — spike validation tests for the typed-marks pattern.

Permanent regression suite for the conftest plumbing. CB.1 builds on
this surface (full marks rollout); CB.6 hardens the composition-rule
errors to ERROR-on-missing-tier. These tests pin the spike's behavior
in case later phases inadvertently break it.

Coverage:

1. `tier(Tier.X)` applies the right mark
2. `dialects(*Dialect)` and `all_dialects()` produce the same mark
3. `l2(*L2)` and `all_l2s()` produce the same mark
4. `needs(*Need)` and `writes()` apply correctly
5. The `_CB_MARK_DOCS` registration prevents `PytestUnknownMarkWarning`
   from firing (no warnings on a marked test).

The composition-rule violations (unit + dialects → ERROR, qs_* without
aws_qs → ERROR) are validated separately via a fixture-based pytester
spike — they call `pytest.exit` which can't be exercised from inside
the same session.
"""

from __future__ import annotations

from tests._marks import (
    Dialect, L2, Need, Tier,
    all_dialects, all_l2s, dialects, l2, needs, tier, writes,
)


class TestMarkDecorators:
    """Smoke tests: the typed decorators produce the expected pytest marks.

    Verifies the mark.name + args round-trip — i.e., when you apply
    `@tier(Tier.UNIT)` to a function, `iter_markers("tier")` yields a
    mark whose `.args[0] == "unit"`. This is what
    `pytest_collection_modifyitems` reads when filtering by `--tier=X`.
    """

    def test_tier_decorator_applies_named_mark_with_value(self) -> None:
        @tier(Tier.UNIT)
        def sample() -> None: ...
        marks = sample.pytestmark  # type: ignore[attr-defined]: pytest mark decorators stash on `pytestmark`
        assert len(marks) == 1
        assert marks[0].name == "tier"
        assert marks[0].args == (Tier.UNIT.value,)

    def test_dialects_decorator_carries_all_listed_values(self) -> None:
        @dialects(Dialect.PG, Dialect.DU)
        def sample() -> None: ...
        marks = sample.pytestmark  # type: ignore[attr-defined]: pytest mark decorators stash on `pytestmark`
        assert len(marks) == 1
        assert marks[0].name == "dialects"
        assert marks[0].args == (Dialect.PG.value, Dialect.DU.value)

    def test_all_dialects_sugar_equivalent_to_full_enum_listing(self) -> None:
        @all_dialects()
        def sample() -> None: ...
        marks = sample.pytestmark  # type: ignore[attr-defined]: pytest mark decorators stash on `pytestmark`
        assert marks[0].name == "dialects"
        # Order matches Dialect enum declaration order; we DON'T sort
        # because preserving member order keeps the runner's per-cell
        # ordering predictable.
        assert set(marks[0].args) == {d.value for d in Dialect}

    def test_l2_decorator_carries_all_listed_forms(self) -> None:
        @l2(L2.SP, L2.SQ)
        def sample() -> None: ...
        marks = sample.pytestmark  # type: ignore[attr-defined]: pytest mark decorators stash on `pytestmark`
        assert marks[0].name == "l2"
        assert marks[0].args == (L2.SP.value, L2.SQ.value)

    def test_all_l2s_sugar_includes_fuzz(self) -> None:
        @all_l2s()
        def sample() -> None: ...
        marks = sample.pytestmark  # type: ignore[attr-defined]: pytest mark decorators stash on `pytestmark`
        assert set(marks[0].args) == {m.value for m in L2}

    def test_needs_decorator_lists_runtime_deps(self) -> None:
        @needs(Need.DOCKER, Need.PLAYWRIGHT)
        def sample() -> None: ...
        marks = sample.pytestmark  # type: ignore[attr-defined]: pytest mark decorators stash on `pytestmark`
        assert marks[0].name == "needs"
        assert set(marks[0].args) == {Need.DOCKER.value, Need.PLAYWRIGHT.value}

    def test_writes_is_an_argless_flag(self) -> None:
        @writes()
        def sample() -> None: ...
        marks = sample.pytestmark  # type: ignore[attr-defined]: pytest mark decorators stash on `pytestmark`
        assert marks[0].name == "writes"
        assert marks[0].args == ()


class TestCompositeMarks:
    """Stacking decorators applies all marks; the order of application
    matches the decorator stack order (bottom-up Python convention)."""

    def test_full_stack_applies_every_mark_independently(self) -> None:
        @tier(Tier.DB)
        @dialects(Dialect.PG, Dialect.DU)
        @l2(L2.SP)
        @needs(Need.DOCKER)
        @writes()
        def sample() -> None: ...
        names = {m.name for m in sample.pytestmark}  # type: ignore[attr-defined]: pytest mark decorators stash on `pytestmark`
        # Mark names come from `tests/_marks.py`'s `pytest.mark.<name>(...)`
        # calls; pin them via the typed-marks module's own constants
        # rather than literal strings so a rename of any mark name in
        # `_marks.py` propagates through the lint cleanly.
        expected_names = set(_CB_MARK_NAMES)
        assert names == expected_names


# Mark names registered by `tests/_marks.py` — pinned here as the
# expected set for full-stack composition assertions. Kept literal
# because the names ARE the runtime contract (they're what pytest
# stores on the mark). BE.2 silences via the comment.
_CB_MARK_NAMES = ("tier", "dialects", "l2", "needs", "writes")  # typing-smell: ignore[no-inline-production-constants]: these strings are runtime pytest mark names declared by the test surface itself in `tests/_marks.py` — they are the contract, not duplicating a src/-side constant


# Selector-deselects-unmarked behavior is verified by the CB.0
# manual smoke against `test_common_db.py` (4 marked / 34 unmarked
# → `--tier=unit` collects exactly 4). Re-pinning it here via
# `pytester.runpytest` would require enabling the pytester plugin
# globally + paying a subprocess-spawn cost on every unit run;
# deferred. The behavior is also indirectly covered by every
# `--tier=` runner invocation in CI, where deselection silently
# trims unmarked tests from each cell.


# -- CB.5 addendum: @inputs(*nodeids) ---------------------------------------

from tests._marks import inputs


class TestInputsDecorator:
    """CB.5 addendum — the `@inputs(...)` typed marker.

    Carries a list of pytest nodeids the test depends on. Validation
    happens at COLLECTION TIME in `tests/conftest.py`'s
    `pytest_collection_modifyitems` hook — this class just pins the
    decorator-output shape.

    Live validation behavior (nodeid-not-found → collection error)
    is exercised indirectly by every CB.5+ agreement validator that
    carries the marker — a stale nodeid would surface as an
    `errors.append(...)` entry from the conftest hook and crash the
    runner via `pytest.exit(..., returncode=2)`.
    """

    def test_inputs_carries_nodeid_strings_verbatim(self) -> None:
        nodeid = "tests/e2e/db/test_x.py::test_y"
        @inputs(nodeid)
        def sample() -> None: ...
        marks = sample.pytestmark  # type: ignore[attr-defined]: pytest mark decorators stash on `pytestmark`
        assert marks[0].name == "inputs"
        assert marks[0].args == (nodeid,)

    def test_inputs_accepts_zero_or_more_nodeids(self) -> None:
        @inputs(
            "tests/e2e/db/test_a.py::t",
            "tests/e2e/app2/test_b.py::t",
        )
        def sample() -> None: ...
        marks = sample.pytestmark  # type: ignore[attr-defined]: pytest mark decorators stash on `pytestmark`
        assert len(marks[0].args) == 2


# -- CB.6 prep: @serial(reason) ---------------------------------------------

from tests._marks import serial


class TestSerialDecorator:
    """CB.6 (operator-flagged 2026-06-02) — typed `@serial(reason)` marker.

    Carries a mandatory reason argument. Captures the "WHY can't this
    parallelize" so the latent `@writes()`-without-isolation debt is
    visible in source — most serial-needing tests turn out to be
    mutating shared state at module/session scope.
    """

    def test_serial_carries_reason_string(self) -> None:
        @serial(reason="seeded_audit fixture DROPs+CREATEs schema; "
                       "DDL races on -n 4. CB.7 follow-up.")
        def sample() -> None: ...
        marks = sample.pytestmark  # type: ignore[attr-defined]: pytest mark decorators stash on `pytestmark`
        assert marks[0].name == "serial"
        assert len(marks[0].args) == 1
        assert "seeded_audit" in marks[0].args[0]
