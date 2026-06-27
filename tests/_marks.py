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
    `UNIT | DB | APP2 | AGREEMENT | QS_API | QS_BROWSER`.

    `AGREEMENT` (DW.3) is the high-watermark cross-renderer validator
    tier — pure JSON-artifact readers that compare what the db + app2
    producers rendered (scenario_plants ⊆ direct == App2 == PDF). It
    needs no AWS, no browser, no DB of its own; it reads the artifacts
    the earlier layers wrote under the shared run dir. (QS_API +
    QS_BROWSER are the QuickSight tiers being removed in Phase DW.)"""

    UNIT = "unit"
    DB = "db"
    APP2 = "app2"
    AGREEMENT = "agreement"
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
    `@pytest.mark.parametrize("fuzz_seed", range(...))`.

    Auto-fuzz semantics (CB.7-followup, 2026-06-02):

    A test that takes the `l2_instance` fixture but DOES NOT pin to a
    specific named scenario (i.e., its `@l2(...)` marker doesn't list
    `L2.SP` or `L2.SQ`) implicitly opts into a per-run fuzz. The hook
    is wired in `tests/conftest.py::pytest_generate_tests`:

    - `@l2(L2.SP)` or `@l2(L2.SQ)` or `@l2(L2.SP, L2.SQ)` → pinned;
      no fuzz cell added.
    - `@l2(L2.FUZZ)` → fuzz only (one seed per run).
    - `@all_l2s()` → sp + sq + fuzz (all three).
    - No `@l2` marker BUT test signature takes `l2_instance` → auto
      fuzz (one seed per run).
    - No `@l2` marker AND test signature doesn't take `l2_instance`
      → no L2 needed; no parametrize.

    Rationale: a test that works on "any topology" should exercise a
    randomly-generated one too, per run. The author doesn't have to
    add `L2.FUZZ` to every unpinned test — coverage breadth becomes a
    property of the tier, not a per-test decision. The runner sets
    `RECON_GEN_FUZZ_SEED` once per invocation; xdist passes it to
    workers, so all workers parametrize over the same seed.
    """

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


class IsolationScope(StrEnum):
    """Cross-tier isolation key (CB.7 refactor 2026-06-02).

    Each scope identifies ONE cross-tier agreement chain. Adding a new
    chain = adding a new enum variant. The enum is the source of truth
    for the chain catalogue.

    The chain has EXACTLY ONE producer file (the writer fixture lives
    there) and one-or-more consumer files (read from the producer's
    DB state). Producer + consumers declare the SAME scope at module
    level via `@isolation_producer(...)` / `@isolation_consumer(...)`
    respectively. Both decorators emit the same `isolation_scope`
    marker so `isolated_cfg` uses the same prefix suffix — readers and
    writers in the chain see consistent state.

    The single-producer rule is load-bearing. If two files both write
    into the same scope's prefix, they either:
    - run concurrently → DROP+CREATE deadlock; or
    - run sequentially → the second's seed wipes the first's plants and
      cross-tier consumers see broken state.

    The right fix when you think you have "two producers for one chain"
    is to merge them — one file, one writer fixture seeding all the
    plant sets the chain needs, multiple test functions asserting their
    pieces of the shared state. The CB.7 inv-direct merge is the model
    (see `tests/e2e/db/test_inv_direct.py`).

    Producer files ALSO pair their `pytestmark` with
    `pytest.mark.xdist_group(scope.value)` even when there is only one
    producer file. Reason: pytest module-scoped fixtures cache per
    xdist WORKER, not globally — without xdist_group pinning, `-n auto`
    scatters the file's individual tests across workers and each worker
    reseeds the shared scope prefix → PG schema-create race. With the
    pin, all tests in the producer file land on one worker, the writer
    fixture seeds once, and the tests share its state.

    Consumer files don't need xdist_group: they READ via the same
    scope prefix the producer wrote, and concurrent reads don't race.

    Codebase invariant captured in the AST check (CB.7-followup):
    each `IsolationScope` variant has exactly one `@isolation_producer`
    declaration across `tests/e2e/**`. Multi-producer = lint error.

    Adding a new chain:
    1. Add a new variant here (e.g. `AGREEMENT_L2FT = "l2ft"`).
    2. The single producer file declares
       `@isolation_producer(IsolationScope.X)` + `xdist_group(X.value)`
       at module-level pytestmark.
    3. Each consumer file declares `@isolation_consumer(IsolationScope.X)`
       — no xdist_group needed.
    """

    AGREEMENT_INV = "ai"
    AGREEMENT_AUDIT = "aa"


def isolation_producer(scope: IsolationScope) -> pytest.MarkDecorator:
    """`@isolation_producer(IsolationScope.X)` — declare this module
    as the WRITER for scope X. Its writer fixtures (requesting
    `isolated_cfg`) seed the chain's shared prefix.

    There must be exactly one producer file per scope. Static check
    enforced by `tests/unit/test_typing_smells.py::test_isolation_scopes`
    (CB.7-followup) once the migration completes.
    """
    return pytest.mark.isolation_scope(scope.value, "producer")


def isolation_consumer(scope: IsolationScope) -> pytest.MarkDecorator:
    """`@isolation_consumer(IsolationScope.X)` — declare this module
    as a READER for scope X. Its tests read the prefix the producer
    seeded.

    Tests in a consumer file should not write — they trust the
    producer's seeded state and assert against it. Multiple consumer
    files per scope are expected (one per tier: app2, qs_browser).
    """
    return pytest.mark.isolation_scope(scope.value, "consumer")


def writes() -> pytest.MarkDecorator:
    """`@writes()` — DEPRECATED 2026-06-02. Use the provider-marked
    isolation pattern instead: writer FIXTURES request
    `isolated_cfg` (see `tests/e2e/db/conftest.py`); test functions
    don't carry a writes mark.

    The original design put the marker on test functions, which
    misnamed the writer — the test is the consumer; the FIXTURE that
    calls apply_db_seed / DROP / CREATE is the actual writer. The
    refactored pattern moves the declaration to the writer fixture
    via its `isolated_cfg` dependency.

    Kept here as a no-op stub so old call sites don't immediately
    break; sweep on next pass."""
    return pytest.mark.writes


def serial(reason: str) -> pytest.MarkDecorator:
    """`@serial(reason)` — DEPRECATED 2026-06-02. Same root cause
    as `@writes()` (see above): the marker named the wrong layer.
    The reason tests need serial execution is almost always
    "shared-state mutation across workers" — fixed at the writer
    fixture via `isolated_cfg`, not at the test via forced -n 1.

    Kept here as a no-op stub so old call sites don't immediately
    break; sweep on next pass."""
    del reason
    return pytest.mark.serial


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
