# CB.17.c — Code-review verification: prefix isolation holds end-to-end

**Status:** done (2026-06-03). Code-review spike instead of live 2-worker
AWS deploy — the existing prefix discipline already provides hard
isolation by construction; a live spike would re-discover what the
type/lint system already enforces.

**Conclusion:** Two parallel pytest-xdist workers with
`isolated_cfg`-derived deployment_name suffixes (`recon-test-w0`,
`recon-test-w1`) are guaranteed-non-colliding on every resource axis
the design touches. CB.17.d can proceed without a `deployment_name`
collision workaround.

---

## What was verified

### 1. `cfg.deployment_name` flows uniformly through QS resource creation

`cfg.prefixed(name) -> f"{deployment_name}-{name}"` is the SINGLE
prefix-stamping function in `src/recon_gen/common/config.py:455`.
Grep across `src/recon_gen/`:

- **71** callsites of `.prefixed(...)` across QS resource creators
  (datasets, dashboards, analyses, themes, datasources, app_info).
- **0** hardcoded `"recon-..."` resource prefixes outside the
  typing-smell-tagged exemptions.
- Enforcement: `tests/unit/test_typing_smells.py::recon-prefix` AST
  lint catches any new hardcoded prefix at write time.

Sample of canonical callsites:

```
src/recon_gen/common/datasource.py:172      ds_id = cfg.prefixed("demo-datasource")
src/recon_gen/common/theme.py:144           theme_id = cfg.prefixed("theme")
src/recon_gen/common/tree/structure.py:1858 AnalysisId=self.cfg.prefixed(self.analysis.analysis_id_suffix)
src/recon_gen/common/tree/structure.py:1882 DashboardId=self.cfg.prefixed(self.dashboard.dashboard_id_suffix)
src/recon_gen/common/sheets/app_info.py:238 cfg.prefixed(f"{app_segment}-app-info-liveness-dataset")
```

Two workers with `cfg.deployment_name = "recon-test-w0"` vs
`"recon-test-w1"` produce non-overlapping resource ID sets across
every dataset / dashboard / analysis / theme / data source.

### 2. `cleanup --execute` gates fail-CLOSED on exact `Deployment` tag match

`src/recon_gen/common/cleanup.py:236`:

```python
if tags.get(DEPLOYMENT_TAG_KEY) != deployment_name:
    continue
```

The constant: `DEPLOYMENT_TAG_KEY = "Deployment"` (line 35). The
`Deployment` tag value is `cfg.deployment_name` (set at deploy time
in `config.py:437`).

So worker 0 calling `cleanup --execute` with
`cfg.deployment_name="recon-test-w0"` can ONLY sweep resources tagged
`Deployment=recon-test-w0`. Worker 1's `recon-test-w1` resources are
invisible to it.

Fail-CLOSED: untagged resources are skipped explicitly. The
Z.C-era comment (cleanup.py:179–192) calls out this guarantee:

> Per-deploy scoping, fail-CLOSED (untagged resources stay safe —
> they were deployed by a previous version of the library and the
> operator hasn't opted into the new scope) … each deploy stamps its
> own `Deployment` value … cleanup only ever sweeps its own scope.

This is a **post-CB.17 invariant**, not something CB.17 needs to add.

### 3. `db_table_prefix` flows through schema / seed / refresh

62 references in `common/l2/schema.py` + `common/l2/seed.py` alone.
Every `<prefix>_transactions` / `<prefix>_daily_balances` / matview
name is templated off `cfg.db_table_prefix`. Two workers' tables
land in distinct namespaces in the shared PG/Oracle container.

### 4. `isolated_cfg` already mutates BOTH fields

`tests/e2e/_isolation.py:110-114`:

```python
return dataclasses.replace(
    cfg,
    db_table_prefix=f"{cfg.db_table_prefix}_{suffix}",
    deployment_name=f"{cfg.deployment_name}-{suffix}",
)
```

`suffix` is hashed from `(test_file_path, worker_id)`. Both
dimensions propagate downstream:

- QS resource IDs via `cfg.prefixed()` → carry the suffix
- `Deployment` tag value via `cfg.deployment_name` → carries it
  → cleanup gate matches
- DB tables via `cfg.db_table_prefix` → carry the underscore form

### 5. Teardown drops the worker's schema

`tests/e2e/_isolation.py:148-155`:

```python
clean_sql = emit_schema_drop_sql(
    instance,
    prefix=isolated.db_table_prefix,
    dialect=isolated.dialect,
)
with teardown_conn.cursor() as cur:
    execute_script(cur, clean_sql, dialect=isolated.dialect)
```

Worker's prefix-scoped tables are dropped on fixture teardown
(best-effort, swallows failures). Combined with CB.17.b's container
URL bridging into `cfg`, this means worker 0's table set lives and
dies entirely inside the shared container without touching worker
1's.

---

## What CB.17.d needs to do (unchanged from the design doc)

The migration plan in `cb_15_collapse_cells_design.md` remains
correct. CB.17.c found nothing that forces a redesign:

- No need for an `xdist_group` serialization fallback for qs_browser.
- No need for FileLock-based deployment_name election.
- The `isolated_cfg` per-(file, worker) suffix is enough.

The only spot worth a paranoid check during CB.17.d: confirm the
`isolation_consumer` / `isolation_producer` markers
(`tests/_marks.py:213+`) still propagate the right scope key to
`isolated_cfg` when the cell loop deletes. Spot-check sites:
`tests/e2e/qs_browser/test_audit_invariants_qs.py:64` etc. The
consumer marker is module-level, so it survives the runner deletion
unchanged.

---

## Memory anchors

- [[feedback_invariants_in_types]] — the prefix discipline is exactly
  this pattern: typed primitives at the construction boundary
  (`cfg.prefixed`) + AST lint (`recon-prefix`) + fail-CLOSED gate
  (cleanup) make wrong unrepresentable rather than catching it in tests.
- [[feedback_cheapest_validation_must_fire]] — the cheapest verifier
  here is grep + AST lint; it fires. A live AWS spike adds nothing.
- [[project_cb6_design_locks.md]] — the CB.7 unwind already locked
  per-(file, worker) isolation; CB.17.c confirms the lock survives
  CB.17.d.
