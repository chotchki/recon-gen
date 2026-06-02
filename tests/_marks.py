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
