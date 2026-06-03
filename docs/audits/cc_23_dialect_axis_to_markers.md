# CC.2 + CC.3 — Move dialect axis to markers; collapse runner cells

**Status:** Design audit. Implementation slices follow.
**Date:** 2026-06-03
**Depends on:** CC.0 (l2_instance unified) + CC.1.a (auto-fuzz hook live).

## Today's shape

The dialect axis is **double-represented**:

1. **Runner cells** — `expand_full()` fans `{sp, sq, fuzz} × {pg, or, du} × {lo, aw}` into 13 cells. Each cell runs `pytest` once with `RECON_GEN_DEMO_DATABASE_URL` + `RECON_GEN_CONFIG` pointing at one dialect.
2. **Test-level parametrize** — files like `tests/e2e/test_audit_dashboard_agreement.py:189` already parametrize at fixture scope: `@pytest.fixture(scope="module", params=["postgres", "oracle"])`.

When the runner fans into `sp_pg_lo` AND the test parametrizes `["postgres", "oracle"]`, the `oracle` callspec inside the pg cell hits `load_dialect_cfg("oracle")` which calls `pytest.skip(...)` — pre-existing logic in `tests/e2e/_agreement_helpers.py:88-130`.

So today: **fan-out at two levels, with skip-deduplication at the inner level.** Wasteful but functional.

The dialect-marker vocabulary already exists:
- `@dialects(Dialect.PG, Dialect.OR, Dialect.DU)` in `tests/_marks.py:123`
- Used by the unit-spike at `tests/unit/test_cb0_marks_spike.py`
- Filter at `tests/conftest.py:507-545` honors `--dialect=X` selector
- Test composition gates enforce `qs_api / qs_browser` require AWS_QS need (`tests/conftest.py:484+`)

What's missing: the dialect marker doesn't drive **fixture dispatch** yet. Tests still parametrize their own `dialect_cfg(params=[...])`.

## Target shape

- **Runner brings up exactly two containers per `./run_tests.sh` invocation**: 1 PG + 1 Oracle (when dialects requested), 1 DuckDB tempfile (when present). URLs go into env vars: `RECON_GEN_DEMO_DATABASE_URL_PG`, `RECON_GEN_DEMO_DATABASE_URL_OR`, `RECON_GEN_DEMO_DATABASE_URL_DU`.
- **Each test layer is ONE `pytest` invocation**, not fan-out per cell.
- **Marker-driven fixture dispatch** — when a test declares `@dialects(Dialect.PG, Dialect.OR)`, the `cfg` / `db_conn` fixtures parametrize over those dialects, reading the matching env URL.
- **No more `dialect_cfg(params=["postgres", "oracle"])` in test files** — replaced by the typed marker + a shared `cfg_by_dialect` fixture in the root conftest.
- **`VariantSpec` / `cell_chain` / per-cell setup goes away.** The fuzz axis (which actually IS scenario-level fan-out, since each fuzz seed is a different L2 topology) stays as test-level parametrize, handled by the existing CC.1 auto-fuzz hook.

## Cell-collapse cost-benefit

Old shape (13 cells × ~5 min db-tier each = 65 min wall, plus aw cells):
```
sp_pg_lo  ──┐
sq_pg_lo  ──┼── 7 lo cells × Docker spin-down/up + N pytest runs
fuzz_pg_lo ──┘
sp_or_lo  ──┐
sq_or_lo  ──┼── 3 or cells × Oracle cold-start (~90s each)
fuzz_or_lo──┘
… aw cells with QS rate-limit fan-out …
```

New shape:
```
./run_tests.sh up_to=db
  ├── boot 1× PG container       (~10s)
  ├── boot 1× Oracle container    (~90s ONCE, not 3×)
  ├── boot 1× DuckDB tempfile     (~0s, in-process)
  ├── pytest -n auto (full suite, marker-driven)
  └── tear down
```

Approximate savings:
- 2× Oracle cold-start (= ~3 min saved per run)
- 6× testcontainers Reaper overhead saved
- Coverage merge becomes trivial — one `.coverage.<host>.<pid>` shard set, not 13

## Decision A — dialect dispatch fixture shape

```python
# tests/conftest.py
@pytest.fixture
def cfg_for_dialect(request: pytest.FixtureRequest) -> Config:
    """Return the cfg pointing at the requested dialect's DB."""
    if hasattr(request, "param") and request.param is not None:
        dialect = request.param  # "postgres" / "oracle" / "duckdb"
    else:
        # Single-dialect default — read DB_URL from env, infer dialect from scheme
        return _load_runner_cfg_single()
    env_var = {
        "postgres": RECON_GEN_DEMO_DATABASE_URL_PG,
        "oracle":   RECON_GEN_DEMO_DATABASE_URL_OR,
        "duckdb":   RECON_GEN_DEMO_DATABASE_URL_DU,
    }[dialect]
    url = env_var.require()
    return _load_runner_cfg_for_dialect(dialect, url)
```

The `pytest_generate_tests` hook (already in `tests/conftest.py`) extends to parametrize `cfg_for_dialect` from `@dialects(...)` similar to how it parametrizes `l2_instance` from `@l2(...)`.

## Decision B — runner's setup_variant → setup_environment

`setup_variant(spec)` becomes `setup_environment(needs: set[Dialect]) -> EnvironmentURLs`:

```python
@dataclass
class EnvironmentURLs:
    pg_url: str | None
    oracle_url: str | None
    duckdb_url: str | None
    teardown: Callable[[], None]
```

Implementation: brings up one container per requested dialect, parallelized (the Oracle cold-start runs in parallel with the PG bringup). Returns the URLs + a teardown callable that stops everything at the end of `./run_tests.sh`.

## Decision C — scenario axis (sp/sq/fuzz) post-collapse

CC.1's auto-fuzz hook ALREADY moves the scenario axis to test-level. After CC.2:
- `--scenarios=sp,sq` → adds `@l2(L2.SP, L2.SQ)` semantics globally? Or filters which test signatures get parametrized? Lean **filter** — operator's `--scenarios=sp` says "only show me the spec_example callspecs". The hook still parametrizes over the test's declared `@l2(...)` set; the conftest `pytest_collection_modifyitems` deselects items whose param id isn't in the operator's set.

## Decision D — fuzz seed fan-out

Today's `--scenarios=fuzz:5` makes 5 fuzz cells. Post-CC, this becomes:
- pytest-level: a `--fuzz-count=N` CLI option, the auto-fuzz hook reads it, and `@l2(L2.FUZZ)` tests get N callspecs (one per seed).
- Already drafted at `tests/conftest.py:300+`: `pytest_addoption("--fuzz-count", default=1)` exists per the markers.py docstring (`L2.FUZZ` resolution rules).

## Migration steps

### CC.2.a — Minimal end-to-end proof

1. Add `cfg_for_dialect` fixture to root `tests/conftest.py`.
2. Migrate `tests/e2e/test_audit_dashboard_agreement.py`'s module-scope `dialect_cfg(params=...)` → `@dialects(Dialect.PG, Dialect.OR)` + use root `cfg_for_dialect`.
3. Verify test fan-out cardinality preserved.

### CC.2.b — Sweep + replace

Same pattern across:
- `tests/e2e/qs_browser/test_audit_invariants_qs.py:73` (`dialect_cfg`)
- `tests/e2e/app2/test_audit_invariants_app2.py:58` (`dialect_cfg`)
- `tests/e2e/db/test_audit_direct.py` (`dialect_cfg`)
- Any other `params=["postgres", "oracle"]` modules

Each migration deletes the local fixture + adds the module mark. The shared `load_dialect_cfg` helper can retire once all callsites consume the root fixture.

### CC.3.a — Runner: drop dialect cell fan-out

1. `setup_variant(spec)` → keep for scenario axis (the runner still needs to pick the right L2 yaml per cell)
2. NEW `setup_environment(dialects: set[str])` → brings up containers once per `./run_tests.sh` invocation; called by `main()` before any cell dispatches
3. Variant matrix shrinks: `expand_full()` returns `{sp, sq, fuzz} × {lo, aw}` = 6 cells (no dialect axis)

### CC.3.b — Runner: collapse scenario axis too

After CC.3.a, the only remaining axis is scenario. With the auto-fuzz hook driving fuzz seed expansion at pytest level, the scenario axis can collapse too:
- `setup_environment` becomes the single bring-up
- The runner just calls `pytest` once per layer
- `--scenarios=sp` becomes a passthrough flag to pytest

### CC.3.c — Delete `VariantSpec`, `cell_chain`, `_run_one_variant`

Final cleanup. The runner becomes ~300 lines, mostly:
- AWS / Docker / cfg discovery
- Container bring-up via `setup_environment`
- Per-layer `pytest` invocation
- Coverage aggregation

Memory: [project_test_layer_chain] stays correct ("unit → db → app2 → deploy → qs_api → qs_browser; invoking layer N requires 1..N-1"). The chain is now expressed as layer-level commands, not cell-level.

## Open questions

1. **Coverage shard merging** — current shape uses per-cell `.coverage.<variant>.<layer>` files. Post-CC.3 it's `.coverage.<host>.<pid>`. The CI badge job's combine step works with either; verify no shape-specific path code lurks.
2. **xdist isolation** — without per-cell prefix isolation, the per-test hash-suffixed prefix (CB.7-followup) carries the load. Verify the isolation contract holds for ~3k parallel tests against 2 long-lived containers.
3. **Oracle connection cap** — long-lived Oracle container × many xdist workers risks exhausting the SESSIONS limit. May need to bump the connection cap on the container or fall back to a per-worker test partition.
4. **`aw` target post-CC.3** — does it disappear? The aw target was Aurora cluster + RDS Oracle — both decommissioned in CB.12. With AWS-side DBs gone, aw is dead; only `lo` cells exist. Collapsing this would simplify the matrix even further.

## Risks

- **In-flight tests assume cell-scoped env vars.** Tests reading `RECON_GEN_DEMO_DATABASE_URL` directly (vs through the cfg) need migration to the new per-dialect vars.
- **`load_dialect_cfg`'s skip-logic encodes the inverse of the bug we're removing.** Once the runner stops setting per-cell env URLs, the skip logic at line 107-113 of `_agreement_helpers.py` becomes unreachable. Delete it.
- **Producer/consumer files** (`isolation_scope` chains) currently assume single-cell same-DB state. With marker-driven parametrize, producer + consumer parametrize independently — verify the per-test hash isolation still gives matching prefixes when same `@dialects(...)` + `@l2(...)` marks land on both.

## Reference points

- Variant matrix: `src/recon_gen/common/variant.py::expand_full` (13 cells)
- Setup dispatch: `src/recon_gen/_dev/runner.py::setup_variant` (lines 1773-1810)
- Dialect-cfg helper: `tests/e2e/_agreement_helpers.py::load_dialect_cfg` (lines 88-130)
- Marker vocab: `tests/_marks.py::dialects` (line 123)
- Filter hook: `tests/conftest.py::pytest_collection_modifyitems` (lines 351-560)
