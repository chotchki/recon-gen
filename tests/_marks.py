"""Typed pytest marks for the layered test suite (CB.0).

Single source of truth for "what tier does this test belong to, and
what does it need." The runner discovers tests via `pytest
--collect-only -m "<expr>"` / `--tier=X --dialect=Y --l2=Z` instead of
hand-listed file paths — the runner shrinks, drift between code and
runner stops.

Five typed marks. Pyright catches `tier("appp2")` (wrong type),
`dialects("Pg")` (case typo), `l2(L2.SAS)` (wrong member name —
typo bait when there are several short L2 forms) at write time.
Pytest's runtime marks stay strings (can't change that); the
author-facing surface is fully typed. Same trick as the existing
`NewType("VariantName", str)` pattern — typed at the call site,
identity at runtime.

Composition rules validated at collection time
(`pytest_collection_modifyitems`) — see `tests/conftest.py`. The
common errors:

- no `@tier` mark on a test → ERROR (source of truth; can't dispatch
  without it)
- `tier(unit)` + `dialects(...)` → ERROR (unit doesn't open a DB;
  tests that emit + assert SQL strings don't carry a dialects mark —
  they're cross-dialect by construction)
- `tier(qs_*)` without `aws_qs` in `needs` → ERROR (QS-touching tests
  must declare the AWS dep so the runner can skip when AWS is paused)
- `tier(qs_browser)` without `playwright` in `needs` → ERROR (QS embed
  renders in a browser)
- `dialects()` empty + tier ≠ unit → WARNING (tier above unit usually
  means a DB is touched; empty dialects is probably an oversight)
- `l2()` empty + tier ≠ unit → WARNING (same shape as the dialects
  rule)
- `writes()` without an `l2_instance` fixture in the test signature →
  ERROR (a test that mutates DB state but doesn't bind the L2-scoped
  fixture chain can't get proper per-worker isolation)

See `docs/audits/cb_test_layers_update.md` for the full design rationale.
"""

from __future__ import annotations

from enum import StrEnum

import pytest


class Tier(StrEnum):
    """Test tier — required on every test, exactly one of:
    `UNIT | DB | APP2 | QS_API | QS_BROWSER`."""

    UNIT = "unit"
    DB = "db"
    APP2 = "app2"
    QS_API = "qs_api"
    QS_BROWSER = "qs_browser"


class Dialect(StrEnum):
    """DB dialect for tier ≥ DB tests. Post-CB.8 SQLite is gone;
    leaving the enum with three production members keeps the marks
    aligned with the runner's matrix shape."""

    PG = "pg"
    OR = "or"
    DU = "du"


class L2(StrEnum):
    """L2 instance form. Post-CB the runner fans tier ≥ DB tests over
    `SP | SQ | FUZZ`. FUZZ is a family parameterized by seed — breadth
    controlled by `--fuzz-count=N` at the runner level; tests that
    genuinely want property-style mass fuzzing opt out via inline
    `@pytest.mark.parametrize("fuzz_seed", range(...))`."""

    SP = "spec_example"
    SQ = "sasquatch_pr"
    FUZZ = "fuzz"


class Need(StrEnum):
    """Runtime dependency the test needs. Runner pre-dispatch probe
    checks each test's `needs` against probe state; missing deps yield
    a skip with a clear reason rather than a 30-second container-spin-
    up-then-fail."""

    DOCKER = "docker"
    PLAYWRIGHT = "playwright"
    AWS_QS = "aws_qs"
    ORACLEDB_CLIENT = "oracledb_client"


def tier(t: Tier) -> pytest.MarkDecorator:
    """`@tier(Tier.X)` — exactly one of `Tier.UNIT | DB | APP2 | QS_API
    | QS_BROWSER`. Required on every test."""
    return pytest.mark.tier(t.value)


def dialects(*ds: Dialect) -> pytest.MarkDecorator:
    """`@dialects(*Dialect)` — zero or more of `Dialect.PG | OR | DU`.
    Empty means "this test doesn't open a DB" (unit-tier helper SQL
    emit tests, JSON byte-shape tests, etc.)."""
    return pytest.mark.dialects(*[d.value for d in ds])


def all_dialects() -> pytest.MarkDecorator:
    """Sugar for `dialects(*Dialect)` — survives Dialect additions /
    removals without per-test churn. Use for genuinely cross-dialect
    tests; reserve explicit listing when a test SHOULD pin to a subset
    (Oracle-only-quirk tests, etc.)."""
    return dialects(*Dialect)


def l2(*xs: L2) -> pytest.MarkDecorator:
    """`@l2(*L2)` — zero or more of `L2.SP | SQ | FUZZ`. Empty means
    "this test doesn't load an L2 yaml." The `l2_instance: L2Instance`
    fixture parameter receives the loaded yaml — tests don't call
    `load_instance` directly."""
    return pytest.mark.l2(*[x.value for x in xs])


def all_l2s() -> pytest.MarkDecorator:
    """Sugar for `l2(*L2)` — fans over spec_example + sasquatch_pr +
    fuzz together. Use for tests that should run on every shape."""
    return l2(*L2)


def needs(*ns: Need) -> pytest.MarkDecorator:
    """`@needs(*Need)` — runtime dependencies the test requires."""
    return pytest.mark.needs(*[n.value for n in ns])


def writes() -> pytest.MarkDecorator:
    """`@writes()` — flag declaring this test mutates DB state. The
    conftest's DB fixture branches on it: writes + DuckDB → per-worker
    isolated DB; unmarked → read_only against the cell's shared
    seeded DB. CB.7 builds the fixture branching; CB.0 just defines
    the flag."""
    return pytest.mark.writes


def serial(reason: str) -> pytest.MarkDecorator:
    """`@serial(reason)` — declare this test must run with `-n 1`.

    CB.6 (operator-flagged 2026-06-02): typed wrapper around the
    runner's per-worker forced-serial path. The `reason` argument is
    mandatory + visible — it's the contract for WHY this test can't
    parallelize. The operator's observation when proposing the typed
    form: **"this smells like a `@writes()` is hidden in there"** —
    most serial-needing tests are actually mutating shared state at
    module/session scope (the canonical case: the audit-agreement
    test's `seeded_audit` fixture re-applies the dialect schema with
    DROP MATERIALIZED VIEW + CREATE; on `-n 4` two workers race the
    schema apply and Oracle's auto-commit DDL produces ORA-00955).

    The right LONG-TERM fix is `@writes()` + per-worker isolation
    (CB.7). `@serial(...)` is the temporary band-aid that surfaces
    the latent debt — every `serial` mark IS a `@writes`-without-
    isolation debt entry.

    Example:

        @serial(reason="seeded_audit module-scope fixture DROPs + "
                       "CREATEs the dialect schema; concurrent workers "
                       "race the schema apply. CB.7 follow-up: migrate "
                       "to per-worker isolation via @writes().")
        def test_audit_invariant_agreement(): ...

    Runner consumption (CB.6): the browser-tier dispatch splits into
    `-m "not serial" -n 4` (main) + `-m "serial" -n 1` (sequential).
    Replaces the current hardcoded `--ignore=test_audit_dashboard_agreement.py`
    + the second pytest invocation against that one file.
    """
    return pytest.mark.serial(reason)


def inputs(*nodeids: str) -> pytest.MarkDecorator:
    """`@inputs(*nodeids)` — declare cross-test artifact dependencies.

    CB.5 addendum (operator-flagged 2026-06-02): promotes test-graph
    coupling from runtime to collection-time. A validator test that
    reads artifacts written by earlier tests names them via
    pytest nodeid strings — the conftest validates at collection time
    that every referenced nodeid actually exists, so renaming /
    moving / deleting an input test SCREAMS instead of silently
    detaching the validator.

    Example shape (agreement-test decomposition):

        @tier(Tier.QS_BROWSER)
        @needs(Need.AWS_QS, Need.PLAYWRIGHT)
        @inputs(
            "tests/e2e/db/test_inv_matview_direct.py::test_drift",
            "tests/e2e/app2/test_inv_renders_app2.py::test_drift_sheet",
            "tests/e2e/qs_browser/test_inv_renders_qs.py::test_drift_sheet",
        )
        def test_inv_agreement_validator_drift(): ...

    Parametrize handling: a bare `<file>::<func>` nodeid matches ANY
    parametrize instance of that function (the conftest checks
    `nodeid.startswith(<ref>)` for parametrized matches). To pin a
    specific parametrize instance, spell the full nodeid
    (`<file>::<func>[<param-id>]`).

    Runner ordering: the validator can't fire standalone — when the
    runner runs `--tier=qs_browser` without first running tier=db +
    tier=app2, the artifact reads fail with "missing artifact" by
    design. The validator's place in the chain is at the high
    watermark (qs_browser tier), which the runner's
    `unit → db → app2 → deploy → api → browser` chain naturally
    satisfies via `./run_tests.sh up_to=browser`.

    Type intent: `nodeids` are pytest nodeids
    (`<rel_path>::<func>` or `<rel_path>::<Class>::<method>` or those
    with `[<param-id>]` appended). Pyright won't typecheck the string
    contents — the collection-time hook in `tests/conftest.py` IS
    the contract.
    """
    return pytest.mark.inputs(*nodeids)
