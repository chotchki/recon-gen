# BL.0 — Construction-time state, process-level keys: the shared-state smell

**Status**: Identified 2026-05-27 after debugging the
`test_inv_filters[app2]` → `psycopg.errors.UndefinedTable: ...iagree...`
cascade. Filed for a future architectural pass; the immediate cascade
is closed by a snapshot/restore band-aid in
`tests/e2e/test_inv_dashboard_agreement.py::isolated_inv_app` (commit
183c9c3b). Tracking here so the band-aid doesn't get mistaken for a
fix.

## The anti-pattern

> "Construction-time state captured in a process-level global, keyed by
> something that does not disambiguate which construction wrote it."

When two construction sites use the same key (a constant, a stable
identifier, a path) to write into a shared global, the **last writer
wins**, and any reader downstream of the loser silently reads the
winner's state. Within a single process, two cfgs that build the same
app surface this immediately.

## Two known instances

### Instance 1 — dataset SQL registry (caught)

`common/dataset_contract.py` carries three module-level dicts:

- `_SQL_REGISTRY: dict[visual_identifier → sql]`
- `_DSP_REGISTRY: dict[visual_identifier → DatasetParameter[]]`
- `_CONTRACT_REGISTRY: dict[visual_identifier → DatasetContract]`

`build_dataset(cfg, ..., visual_identifier=DS_INV_VOLUME_ANOMALIES,
...)` writes to all three. The dataset SQL has `cfg.db_table_prefix`
baked in (the SQL string contains literal `FROM
<prefix>_inv_pair_rolling_anomalies`), so each cfg writes a
*different* SQL value. But the **key** is `DS_INV_VOLUME_ANOMALIES` (a
CONSTANT, shared across cfgs).

The tree fetcher reads via `get_sql(dataset.identifier)`. The tree
`Dataset.identifier` for the Investigation Volume Anomalies dataset is
also `DS_INV_VOLUME_ANOMALIES` (set at tree-construction time on
`Dataset(identifier=DS_INV_VOLUME_ANOMALIES, arn=...)`). Same key →
the fetcher reads whatever cfg's build wrote last.

The agreement test demonstrates the bleed:

1. Session-scope `inv_app` builds with canonical cfg →
   `_SQL_REGISTRY[DS_INV_VOLUME_ANOMALIES] = "...FROM
   qsgen_sp_pg_aw_inv_pair_rolling_anomalies..."`.
2. Module-scope `isolated_inv_app` builds with isolated cfg
   (`db_table_prefix=qsgen_sp_pg_aw_iagree`) → overwrites with
   `"...FROM qsgen_sp_pg_aw_iagree_inv_pair_rolling_anomalies..."`.
3. Module teardown DROP-CASCADEs the iagree schema.
4. Downstream `test_inv_filters[app2]` / `test_parameter_anchored_sheets[app2]`
   read the still-polluted entry → query iagree → 500.

### Instance 2 — L2 config kv table (latent)

The `<prefix>_config_kv` rows are written at `schema apply` time from
the L2 YAML. The deployed App2 reads its L2 shape from these rows
(BC.7 / BC.8) — NOT from the YAML on disk. The on-disk YAML is the
source of truth at *deploy* time; the kv rows are the runtime cache.

Same shape:

- Source of truth: a file (YAML).
- Captured into: a kv-table keyed by `(prefix, key)`.
- Key is per-prefix, but the *contents* of the value can diverge from
  the on-disk YAML if `data apply --execute` was last run against a
  different YAML for the same prefix. (Or worse: against the SAME
  YAML at a different revision.)
- Readers downstream (App2 server, audit PDF) trust the kv rows.

Surface haven't bitten us yet because we don't routinely point two
distinct YAMLs at the same prefix. But the discipline mirrors the SQL
registry: writers don't disambiguate by source-of-construction; readers
don't verify which source they're reading from.

## Why the shape recurs

Both instances were originally cheap optimizations: avoid threading
state through every caller, look it up by a stable name. That works
until a second writer shows up. The codebase has at least two such
writers now:

- For SQL: `inv_app` + `isolated_inv_app` + (when AT5 / AY5 lands)
  per-scenario apps under `tests/`.
- For config_kv: dev `data apply` + CI deploy + (eventually) Studio's
  edit-and-redeploy loop.

Every new "second writer" turns the shared global into a race.

## Proposed fixes

Two families, pick per-instance.

### Family A — Make the state self-contained on the constructed object

Move the captured state onto whatever was being constructed. For SQL:
add a `sql` field to the tree `Dataset` node; the fetcher reads
`dataset.sql` directly. No registry, no key.

```python
@dataclass(frozen=True)
class Dataset:
    identifier: str
    arn: str
    sql: str  # new — was in _SQL_REGISTRY[identifier]
    dataset_params: list[DatasetParameter]  # new
    contract: DatasetContract  # new
```

Pro: no global state, no key collisions possible. Each tree `Dataset`
is a complete description.
Con: requires plumbing all `build_dataset` returns to construct the
tree `Dataset` from the AWS DataSet's components. ~50 call sites in
the apps. One sweep phase.

For config_kv: read L2 YAML at runtime via a session-scoped fixture
that loads the yaml file the deploy used (capture the path in cfg).
The kv table becomes purely an audit trail (what was deployed),
never the source-of-truth read path.

### Family B — Scope the key by the constructor's identity

Add cfg context to the key:

```python
_SQL_REGISTRY: dict[tuple[str, str], str]  # (visual_identifier, cfg.db_table_prefix)
def register_sql(visual_identifier, sql, *, cfg_prefix): ...
def get_sql(visual_identifier, *, cfg_prefix): ...
```

Pro: minimal disruption. Existing callers add a `cfg_prefix=` kwarg.
Con: every reader now needs to know cfg. The tree fetcher already
threads cfg through, so this is fine. But it doesn't eliminate the
global, just makes it cfg-aware.

For config_kv: same shape — read keyed by `(prefix, l2_yaml_sha)` so
two YAMLs against the same prefix don't collide.

### Recommendation

Family A for SQL. The tree `Dataset` is the natural home for SQL +
dataset params + contract — they're per-Dataset properties, not
process-global facts. The refactor is bounded (one sweep through
`build_dataset` call sites) and removes a class of bug we'll keep
hitting (every isolation pattern repeats the bleed).

Family B for config_kv. The yaml-vs-kv tension is fundamental to the
deploy-vs-runtime split; can't dissolve it. Just key it so two
deploys against one prefix can't collide silently. Doc the
"<prefix>_config_kv reflects whatever the LAST `data apply` wrote;
re-apply to refresh" contract loudly.

## Today's band-aid

`isolated_inv_app` (commit 183c9c3b) snapshots `_SQL_REGISTRY` +
`_DSP_REGISTRY` + `_CONTRACT_REGISTRY` on setup, restores on
teardown. Surgical, scoped to the one fixture that triggered the
cascade. Does **not** prevent future "second writer" bleeds in
other tests / apps — flag this if a new isolation pattern shows up.

## When to address

When a release isn't pending. The user explicit guidance 2026-05-27:
"I want a clean release for now but we'll address eventually (or
sooner since every piece of tech eventually seems to bit us)" — so
queue this as a real phase (BL.x?) post-release. The band-aid keeps
the agreement test from polluting downstream tests; the architectural
fix removes the smell.
