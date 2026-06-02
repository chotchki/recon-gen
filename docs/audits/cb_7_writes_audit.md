# CB.7 — `@writes()` fixture branching + per-test audit

**Status:** Skeleton (this doc) + initial fixture-branching landed; per-test
audit pending. Tracks the multi-session work to flip DuckDB tests onto
declared-isolation semantics.

## Why

The DuckDB swap (Phase CA) introduced a strict single-writer-per-file lock
that SQLite's TEXT-affinity-and-permissive-locking masked. Two related
issues (#199, #200) surfaced when xdist workers raced on shared seeded
DBs: "works on SQLite, races on DuckDB" — exactly the strict-engines-
surface-isolation-bugs principle ([[feedback_strict_engines_surface_isolation_bugs]]).

The runner's CA.8 mitigation (`RECON_GEN_DB_READ_ONLY=1` for db/app2/
qs_browser layers) made every DuckDB open read-only across the matrix.
That works as long as no test mutates state — but the seeded DB is
shared across xdist workers, so any test that writes corrupts the shared
state for every co-tenant.

CB.7 closes this honestly: tests declare what they do via `@writes()`,
fixtures match the isolation strategy to the declaration. Read tests get
the fast shared-DB path; write tests get per-worker isolated DBs.

## The contract

```python
from tests._marks import Tier, tier, writes

@tier(Tier.DB)
@dialects(Dialect.PG, Dialect.DU)
@writes()
def test_plant_apply_mutates_matview_then_asserts(l2_instance, db_cfg):
    """Test that mutates DB state (@writes() opt-in).

    The `db_cfg` fixture sees the @writes() mark and overrides
    cfg.demo_database_url to a per-worker isolated DuckDB file —
    concurrent xdist workers can't race because each gets its own DB.

    `l2_instance` is the L2-scoped seed driver — required when @writes()
    is present (the composition rule errors when @writes() appears
    without it).
    """
```

For tests WITHOUT `@writes()`, the `db_cfg` fixture returns the session
cfg unchanged — read_only=True against the shared seeded DB. Same
ergonomics as today; no behavior change for the read-only majority.

## Fixture-branching design

Implementation lives at `tests/e2e/db/conftest.py` (and parallel app2,
qs_browser conftests when CB.7's audit reaches those tiers):

```python
@pytest.fixture
def db_cfg(request: pytest.FixtureRequest, cfg: Config, tmp_path_factory):
    """Per-test cfg with @writes()-driven URL override.

    No-op for read-only tests; clones the cfg with a fresh per-worker
    DuckDB path for @writes() tests. The fresh DB is seeded from the
    shared session-scope seeded DB via DuckDB's EXPORT/IMPORT (since
    DuckDB has no Connection.backup()).
    """
    if not any(m.name == "writes" for m in request.node.iter_markers()):
        return cfg  # read-only path — shared seeded DB
    if cfg.dialect is not Dialect.DUCKDB:
        return cfg  # PG / Oracle have proper isolation already
    # @writes() + DuckDB → per-worker isolated file
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    isolated_path = tmp_path_factory.mktemp(f"db_writes_{worker_id}") / "demo.duckdb"
    _clone_db(source_url=cfg.demo_database_url, dest=isolated_path)
    return replace(cfg, demo_database_url=make_demo_database_url(Dialect.DUCKDB, isolated_path))
```

## Per-test audit

Each existing DB-tier / app2-tier test needs classification:

1. **Read-only**: SELECT-only against the seeded DB. No mark needed
   (default).
2. **Write**: ANY of:
   - Calls `cur.execute("INSERT/UPDATE/DELETE/CREATE/DROP/TRUNCATE ...")`
   - Calls `replace_config(...)` / `emit_seed(...)` / `apply_plant_sql(...)`
   - Re-applies schema (`emit_schema(...)` + `execute_script(...)`)
   - Uses the `seeded_audit` / `isolated_inv_cfg` shape (already-isolated
     fixtures — these stay as-is or migrate to @writes() + db_cfg)

The audit is agent-friendly: each test file is independent, the
classification rule is grep-able (`INSERT INTO`, `DELETE FROM`, etc.),
and the change is local (add `@writes()` decorator + change `cfg` to
`db_cfg` in the test signature).

## Migration order

1. **tests/e2e/db/** — smallest population (~9 files), fastest verify
2. **tests/e2e/app2/** — App2 driver tests; mostly read-only, audit is
   a quick filter
3. **tests/e2e/qs_browser/** — largest; the agreement tests (`*_agreement.py`)
   already use isolated-fixture patterns that map cleanly to @writes()

## Composition rule (collection-time validation)

The conftest's `pytest_collection_modifyitems` adds one new rule:

```
@writes() without an `l2_instance` fixture in the test signature → ERROR
```

This catches the case where a test mutates state but doesn't bind the
L2-scoped fixture chain — no chain = no per-worker isolation = silent
data corruption.

## Closes

- #199 — DuckDB pytest-xdist intra-cell file-lock contention
- #200 — Migrate db-tier fixtures to :memory: DuckDB per xdist worker
- The "`@serial(reason)` is a `@writes()`-without-isolation debt entry"
  observation from `_marks.py` (every `@serial` mark becomes a CB.7
  audit candidate).

## Open questions

- **Schema apply cost.** Re-applying the seed schema per-worker per-test
  for @writes() tests adds wall-clock. Mitigation: `tmp_path_factory.mktemp`
  is worker-scoped so the cloned DB persists across tests within a worker;
  only the FIRST @writes() test in a worker pays the clone cost. The
  audit doc's `:memory:` shape is faster but loses persistence across
  test functions within the same worker — fine for tests that don't need
  cross-test state, slower for those that do.
- **PG / Oracle parity.** PG transactions roll back cleanly; Oracle DDL
  auto-commits. The `@writes()` fixture branching is a no-op for both
  (PG via session-rollback; Oracle via the existing isolated-prefix
  scheme from Phase Z.B.14). CB.7 audits DuckDB only; PG / Oracle stay
  on their existing isolation paths.
