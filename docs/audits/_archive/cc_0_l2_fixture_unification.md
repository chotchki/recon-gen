# CC.0 — Unify `l2_instance` fixtures spike

**Status:** Design audit. Implementation is CC.1 onward.
**Date:** 2026-06-03
**Sequencing:** CC.0 is the first step in Phase CC (cell collapse — move scenario/dialect matrix from runner cells to test markers).

## Current state — three fixture shapes, one L2 list

The codebase has three independent `l2_instance`-shaped fixtures that all parametrize over the same source list but with different scopes and different names.

### 1. `tests/data/test_l2_seed_contract.py` — source of truth for `L2_INSTANCES`

```python
L2_INSTANCES = [
    pytest.param(L2_DIR / "spec_example.yaml", id="spec_example"),
    pytest.param(L2_DIR / "sasquatch_pr.yaml", id="sasquatch_pr"),
    pytest.param(_fuzz_yaml_path(), id=f"fuzz-seed-{FUZZ_SEED}"),
]

@pytest.fixture(params=L2_INSTANCES)
def l2_yaml(request: pytest.FixtureRequest) -> Path:
    return Path(request.param)

@pytest.fixture
def instance(l2_yaml: Path) -> L2Instance:
    return load_instance(l2_yaml)
```

- Function-scoped
- Three-form fan-out (spec_example × sasquatch_pr × fuzz)
- Local to this file; `L2_INSTANCES` is imported elsewhere as a constant

### 2. `tests/json/test_l2_flow_tracing_matrix.py` — re-uses the same list

```python
from tests.data.test_l2_seed_contract import L2_INSTANCES

@pytest.fixture(params=L2_INSTANCES)
def l2_instance(request: pytest.FixtureRequest) -> L2Instance:
    return load_instance(request.param)
```

- Function-scoped
- Identical 3-form fan-out
- Different name (`l2_instance` not `instance`) → no fixture inheritance from #1

### 3. `tests/e2e/conftest.py` — session-scoped single-instance

```python
@pytest.fixture(scope="session")
def l2(cfg: Config) -> "L2Instance":
    del cfg
    return _resolve_test_l2_instance()
```

- Session-scoped (entire e2e session sees one L2)
- No parametrize; resolves a single yaml from `RECON_GEN_TEST_L2_INSTANCE` env var
- Named `l2` not `l2_instance` (third naming)
- Multiple session-autouse fixtures depend on it (`_refresh_matviews_once_per_session`, `_qs_pre_warm_dashboards`)

### Marker vocabulary — already typed and live

`tests/_marks.py` defines:

```python
@l2(L2.SP, L2.SQ, L2.FUZZ)   # opt into specific L2 forms
@l2()                          # explicitly no L2
all_l2s()                      # sugar for all three
```

The `@l2(...)` mark is already collected by the auto-fuzz hook stub in `tests/conftest.py::pytest_generate_tests`:

```python
declared = {arg for mark in metafunc.definition.iter_markers("l2")
                for arg in mark.args}
has_sp = "spec_example" in declared
has_sq = "sasquatch_pr" in declared
if not has_sp and not has_sq:
    declared.add("fuzz")           # auto-fuzz rule
if len(declared) <= 1:
    return                          # single-form → env-var path
# TODO(CB.7-followup): metafunc.parametrize("l2_instance", sorted(declared), indirect=True)
```

The hook is **scaffolded but disabled** — flipping `parametrize(... indirect=True)` on without unifying the fixture would break ~3000 tests because most tier fixtures don't accept indirect params.

## Why unify

1. **Fixture sprawl** — three near-identical shapes diverge over time. The naming collision (`l2`, `l2_instance`, `instance`) already costs cognitive load.
2. **Runner duplication** — the runner's per-cell L2 dispatch (`RECON_GEN_TEST_L2_INSTANCE` env var per variant) **and** pytest parametrize both express the same axis. CC's whole thesis is "tests own the matrix"; one fixture is the precondition.
3. **Auto-fuzz can't ship** — the hook is dead code until a unified fixture exists that accepts the indirect param uniformly across tiers.
4. **Marker semantics already correct** — `@l2(...)` is the right type; we just lack the fixture wiring to honor it.

## Three options

### Option A — shared module `tests/_l2_fixtures.py`

A dedicated module defines `l2_instance(request)` with dual-mode body:
- If parametrized (`request.param` set) → load that yaml
- If not parametrized → fall back to env-var single-instance

Every conftest re-exports it.

| Pros | Cons |
|---|---|
| One body, one source | Convoluted dual-mode |
| No collision with `l2` legacy | Two callsite spellings still exist (`l2` vs `l2_instance`) — confusing |
| Easy to gate the e2e tier with `if RECON_GEN_E2E` | Still requires e2e callsite renames |

### Option B — root `tests/conftest.py::l2_instance` (RECOMMENDED)

```python
@pytest.fixture
def l2_instance(request: pytest.FixtureRequest) -> L2Instance:
    """Function-scoped L2 fixture.

    Parametrized by the auto-fuzz hook based on @l2(...) markers:
    - indirect param present → load that L2 form
    - no param → resolve from RECON_GEN_TEST_L2_INSTANCE env var
      (the runner-supplied single-instance path, kept for backward
      compat during the CC roll-out)
    """
    if hasattr(request, "param") and request.param is not None:
        return _load_l2_by_name(request.param)
    return _resolve_test_l2_instance()
```

- Function-scope by default
- The auto-fuzz hook decides if it parametrizes
- e2e tier's session-scope `l2` becomes a thin shim:
  ```python
  @pytest.fixture(scope="session")
  def l2(l2_instance: L2Instance) -> L2Instance:  # session-scope alias
      return l2_instance
  ```
  Existing e2e callsites referencing `l2` keep working unchanged.

| Pros | Cons |
|---|---|
| One canonical fixture, root-conftest discoverable | Session-scope `l2` shim is a wart (transitional) |
| Plays cleanly with auto-fuzz hook (same file) | Function-scope changes default if e2e tests start consuming `l2_instance` directly — they shouldn't until intentional |
| Backward-compat at the callsite | Need to migrate `tests/data/test_l2_seed_contract.py::instance` + `tests/json/test_l2_flow_tracing_matrix.py::l2_instance` to consume root fixture |
| Marker-driven; no env-var sprawl | Mid-migration the auto-fuzz hook needs to gate on existing-fixture override to avoid double-parametrize |

### Option C — Hybrid (function + session, both alive)

Keep both `l2_instance` (function, parametrize-aware) AND `l2` (session, env-var). They diverge by design: `l2_instance` is the marker-driven one, `l2` is the legacy single-deploy one.

| Pros | Cons |
|---|---|
| Zero churn in e2e callsites | Two fixtures with different semantics — confusing |
| Session-scope optimization preserved unconditionally | Doubles the surface that CC wants to collapse |
| Easy to land | Doesn't actually unify — punts the design question |

## Recommendation: **Option B**

Marker-driven, single canonical fixture, session-scope `l2` survives as a transitional shim during the CC roll-out. After CC.3 finishes (runner reduced to 1-pytest-per-layer), the env-var fallback path drops out and `l2_instance` becomes purely marker-driven.

## Migration steps (CC.1 → CC.3)

### CC.1 — land `l2_instance` in `tests/conftest.py`, flip the auto-fuzz hook

1. Add `l2_instance` fixture to `tests/conftest.py` per Option B spec above.
2. Add `_load_l2_by_name(name: str) -> L2Instance` helper that maps `"spec_example"` / `"sasquatch_pr"` / `"fuzz"` → the right yaml load.
3. Flip the auto-fuzz hook's `# TODO` to live code:
   ```python
   metafunc.parametrize("l2_instance", sorted(declared), indirect=True)
   ```
4. Add e2e tier shim:
   ```python
   # tests/e2e/conftest.py
   @pytest.fixture(scope="session")
   def l2(l2_instance: L2Instance) -> L2Instance:
       return l2_instance
   ```
   (Delete the existing `l2` body.)
5. Delete `tests/data/test_l2_seed_contract.py::l2_yaml` + `::instance` — replace with `l2_instance` consumer.
6. Delete `tests/json/test_l2_flow_tracing_matrix.py::l2_instance` — inherits from root.
7. Add unit tests for the auto-fuzz hook: a stub test file with `@l2()` / `@l2(L2.SP)` / `@l2(L2.SP, L2.SQ)` / no-mark cases proving the right parametrize-set fires.

### CC.2 — Move dialect axis to markers

Independent of CC.1 — the `@dialects(...)` marker already exists; just need to drop the runner's `--dialects` cell fan-out and let xdist + the marker filter own it.

### CC.3 — Runner becomes 1-pytest-per-layer

Once CC.1 + CC.2 land, the runner can stop iterating `VariantSpec`s entirely. Each layer's command becomes one pytest invocation; pytest collects, the auto-fuzz hook parametrizes, xdist fans out workers, dialect markers filter.

## Backward-compat boundary

Until CC.3 finishes:
- The runner still sets `RECON_GEN_TEST_L2_INSTANCE` for each cell. The `l2_instance` fixture's env-var fallback path honors this — so a test that takes `l2_instance` without an `@l2(...)` mark gets a single instance per cell.
- Tests that DECLARE `@l2(L2.SP, L2.SQ, L2.FUZZ)` (e.g., `test_l2_seed_contract.py`) fan out via parametrize and ignore the env var.
- The auto-fuzz hook's `len(declared) <= 1` short-circuit guarantees single-form tests don't double-parametrize.

After CC.3, the env-var path retires and the fixture body becomes:
```python
@pytest.fixture
def l2_instance(request: pytest.FixtureRequest) -> L2Instance:
    return _load_l2_by_name(request.param)
```

## Open questions

1. **Session-scope `l2` shim lifetime** — kill it after CC.3 lands and rename all e2e callsites, OR keep as a permanent session-cache alias? Lean **kill it** post-CC.3 for hygiene; the rename is mechanical.
2. **Fuzz seed determinism** — currently `FUZZ_SEED` comes from `RECON_GEN_FUZZ_SEED` env (set by runner per cell). Post-CC.3 the runner only runs pytest once per layer; how does fuzz multi-seed coverage land? Options: `--scenarios=fuzz:N` becomes a pytest custom CLI option, OR fuzz cell expansion lives in the auto-fuzz hook. Defer to CC.1 implementation.
3. **Cross-tier consistency** — the runner-era pattern was "cell N uses L2 X" — same X across db/app2/qs_browser layers for that cell. Post-CC.3, each layer's pytest invocation is independent. A test that bridges tiers (e.g., audit chain producers in db tier + consumers in qs_browser tier) needs the same L2 in both. Currently this would mean both layers' tests carry `@l2(...)` markers with the same forms; the auto-fuzz hook produces the same parametrize set deterministically.

## Risks

- **Test count explosion.** If many tests currently rely on the runner's single-cell env-var path and don't carry `@l2()` marks, the auto-fuzz hook will start parametrizing them. The `len(declared) <= 1` gate prevents this for tests with no marker, but anything that picks up the hook by accident multiplies.
- **Session-scope dependencies in e2e.** Multiple session-autouse fixtures take `l2` as a param. If the shim breaks (or function-scope `l2_instance` gets injected accidentally), `ScopeMismatch` like the CB.7-followup one. The shim's `scope="session"` declaration is the guardrail.
- **Locked-seed test invalidation.** `tests/data/_locked_seeds/<instance>.<dialect>.sql` is parametrized over `L2_INSTANCES` indirectly. The migration must preserve the fan-out cardinality exactly — a missed yaml means a missed seed gate.

## Reference points

- Auto-fuzz hook stub: `tests/conftest.py:558-605`
- `@l2(...)` marker: `tests/_marks.py:138-149`
- `L2_INSTANCES` source: `tests/data/test_l2_seed_contract.py:122-129`
- e2e `l2` fixture: `tests/e2e/conftest.py:316-332`
- Runner per-cell L2 env: `src/recon_gen/_dev/runner.py` (search `RECON_GEN_TEST_L2_INSTANCE`)
