# AV.0 — `daily_balances.limits` → `daily_balances.metadata` migration audit

**Status:** AV.0 complete (audit + spike) — 2026-05-23.
**Branch:** `av-limits-to-metadata`.
**Output of:** the AV.0 inventory pass per PLAN.md Phase AV.

---

## TL;DR

**`daily_balances.limits` is dormant real estate.** The column is declared
but no production code reads from it; no generator writes a non-NULL
value; the matviews that *talk* about limits (`limit_breach`,
`declared_limits`) all read from elsewhere (the L2 yaml via
`<prefix>_config` per Phase AW, or the in-process `L2Instance` Python
object). The locked-seed INSERT statements name the column in their
column lists but pass `NULL` for every row.

This makes Phase AV materially simpler than the original plan
suggested. There is no live JSON-path reader to migrate; AV.2's
"update every matview/dataset SQL" leaf is effectively empty.

**Revised AV scope** (smaller than the spec-branch shape suggested):

1. **AV.1 schema** — rename the column declaration + CHECK constraint
   + ETL-shape comment block in `common/l2/schema.py`. Per-dialect.
2. **AV.2 readers** — *empty* by current inventory; reserved as a
   sentinel for any reader added before AV ships. (If something lands
   that reads `limits` before AV merges, surface it here.)
3. **AV.3 writers + tests + locked seeds** — `_emit_helpers.DB_COLS`
   comment + 3 locked-seed `.sql` files' INSERT column lists
   (mechanical s/`limits`/`metadata`/ in 2,376 INSERTs × 3 dialects)
   + 2 schema tests' literal expectations.
4. **AV.4 release notes + version bump** — patch-level (additive on a
   dormant column) but flag migration intent for AV.5.
5. **AV.5 (post-AV)** — Promote `ScenarioContext` from the spike. With
   `metadata` on both base tables, per-row scenario tagging replaces
   the spike's `<prefix>_scenario_claims` sidecar entirely (the user's
   stated motivation per 2026-05-23: *"this should also remove the
   sidecar table we've gained for the scenario planting tagging"*).
   The sidecar is currently on the (deleted) `scenario-context-spike`
   branch only — main is clean of it. AV.5 is the unblocked work.

**Bonus finding:** AV opens new design space for *time-varying* per-day
limit overrides (`metadata.limits = {rail: cap}`) — flagged in
SPEC.md:1031 as "await a real integrator requirement". Out of AV's
scope but reserved as the natural follow-on.

---

## Inventory by surface

### 1. Schema declaration

| Path | Lines | Role |
|---|---|---|
| `src/recon_gen/common/l2/schema.py` | 230–231 (DB_COLS doc), 1340–1343 (template kwargs), 1541–1545 (header comment), 1567 (column decl), 1571 (CHECK constraint) | The column's source of truth. Per-dialect rendering via `json_text` / `json_check("limits", dialect)`. |
| `src/recon_gen/common/l2/schema.py` | 30 | Module docstring mentions "L2's Limits — projected into the `daily_balances.limits` Map column". |

**Migration:** `s/limits/metadata/` in the template; `json_check("limits", ...)` → `json_check("metadata", ...)`. Update the header comment block to describe the open metadata shape (mirrors `transactions.metadata`).

### 2. Spine helper

| Path | Lines | Role |
|---|---|---|
| `src/recon_gen/common/spine/_emit_helpers.py` | 105–112 (`DB_COLS` + comment) | `DB_COLS` excludes `limits` by design; the comment cites AV.0. The tuple needs no change (the rename doesn't add a column generators emit); the comment updates to reflect post-AV naming. |

**Migration:** comment-only.

### 3. Matviews — VERIFIED non-readers

| Matview | Path | What it actually reads |
|---|---|---|
| `<prefix>_limit_breach` | `src/recon_gen/common/l2/schema.py` 1966–2018 | Reads per-rail caps from `<prefix>_config.l2_yaml` via `json_array_iterate()` + `json_field_extract()` (Phase AW). Does NOT touch `daily_balances.limits`. |
| `<prefix>_inv_money_trail_edges` | `src/recon_gen/common/l2/schema.py` 2639+ | Walks `transactions.transfer_parent_id`. Does NOT touch `daily_balances`. |
| L1 drift / overdraft / expected_eod / stuck_* / supersession | various | Read from `daily_balances` for `money`, `expected_eod_balance`, `business_day_*` — not `limits`. |

**Migration:** none.

### 4. Datasets — VERIFIED non-readers

| Dataset path | What it actually reads |
|---|---|
| `src/recon_gen/apps/l2_flow_tracing/datasets.py` `_declared_limit_schedules_cte` (used by `build_dead_limit_schedules_dataset`, `build_unified_l2_exceptions_dataset`, `build_declared_limits_dataset`) | Builds a hardcoded `UNION ALL VALUES (...)` from the in-process `L2Instance.limit_schedules` Python list. Does NOT query any DB column. |

**Migration:** none.

### 5. Tests

| Path | Lines | Role |
|---|---|---|
| `tests/schema/test_l2_schema.py` | 169–176 | `test_daily_balances_includes_expected_eod_and_limits` — asserts column declaration type. |
| `tests/schema/test_l2_schema.py` | 216–229 | `test_metadata_uses_text_with_is_json_check` — asserts the IS JSON CHECK constraint. |
| `tests/schema/test_l2_schema_sqlite.py` | 153 | Asserts the SQLite-equivalent `json_valid(limits)` CHECK. |

**Migration:** rename the column name in the test literals + the test function names (the latter for grep clarity post-AV). No semantic test change.

### 6. Locked seeds

| Path | Pattern | Count |
|---|---|---|
| `tests/data/_locked_seeds/spec_example.sqlite.sql` | `INSERT INTO spec_example_daily_balances (..., limits, ...)` | 2,376 INSERTs, all populate the column with `NULL` |
| `tests/data/_locked_seeds/spec_example.postgres.sql` | same | 2,376 |
| `tests/data/_locked_seeds/spec_example.oracle.sql` | same | 2,376 |

**Migration:** mechanical `s/, limits, /, metadata, /` in the column list of each INSERT. Re-lock via `recon-gen data lock -c <config> --l2 <yaml>` per dialect (the seed shape is byte-identical post-rename so the lock files swap cleanly).

### 7. Documentation

| Path | Lines | Role |
|---|---|---|
| `src/recon_gen/docs/Schema_v6.md` | 205, 213, 288, 309, 402 | Column docs + "open JSON TEXT" mention + per-day override note. |
| `docs/audits/p_2_dialect_catalog.md` | 46 | Portability constraint listing. |
| `src/recon_gen/common/etl_examples.py` | 338–365 | ETL example block "account-day's `limits` JSON map keyed by `rail_name`". |

**Migration:** rewrite to describe the new shape — `daily_balances.metadata` is symmetric with `transactions.metadata`; the legacy "limits map keyed by rail_name" use case becomes `metadata.limits = {rail_name: cap}` (the JSON-nested form the user proposed 2026-05-23).

### 8. Sidecar (verification of non-presence)

`<prefix>_scenario_claims` does **not** exist on `main`. It lives only
on the (deleted-after-merge) `scenario-context-spike` branch's spike
test. AV does not need to drop a table — there's nothing to drop. AV.5
is the work that *avoids creating* the sidecar when promoting
ScenarioContext.

---

## Locked migration ordering

The original plan assumed many readers; the audit shows almost none.
The simplified order:

1. **AV.1** — schema column rename + CHECK constraint (per-dialect emit).
2. **AV.3** — locked-seed re-lock (mechanical SQL change; the seed
   `emit_seed` Python code follows AV.1 automatically because it pulls
   the column list from `DB_COLS`).
3. **AV.3 cont'd** — `tests/schema/test_l2_schema*.py` literal updates.
4. **AV.4** — version bump + RELEASE_NOTES with migration warning ≥1
   minor version for downstream operators with custom ETL (the
   wire-shape change is theirs to absorb).
5. **AV.5** — ScenarioContext promotion (separate task; unblocked
   immediately by AV.1).

AV.2 ("update every matview/dataset SQL") stays in the PLAN as a
sentinel leaf — empty by current inventory, but reserved in case a
reader lands between AV.0 and AV.1 ship.

## Open question (surfaces to user before AV.1)

**Should the rename ALSO restructure the JSON shape?** The current
column carries `{rail_name: cap}` (a flat map). The user's 2026-05-23
phrasing suggested wrapping under `metadata.limits = {rail_name: cap}`
so the JSON has room for sibling keys (scenario tags, future per-day
metadata). Three options:

- **(A)** Column rename only — `limits` → `metadata`; the JSON inside
  remains `{rail_name: cap}` (same shape; the column name is now
  generic). Simplest; ETL contract identical.
- **(B)** Column rename + JSON restructure — `limits` → `metadata`;
  JSON becomes `{"limits": {rail_name: cap}}` so future siblings
  (`metadata.scenario_id`, `metadata.notes`) have room. Slightly more
  per-write cost; matches the user's stated intent verbatim.
- **(C)** Same as (B) but defer the restructure to a later phase —
  ship (A) now to unblock AV.5, restructure later when a sibling key
  is actually needed.

Recommendation: **(B)** — the user's exact ask. The wire-shape change
is paid for once; siblings are AV.5's deliverable. Holding off on the
restructure means AV.5 either lives in `transactions.metadata` only
(asymmetric) or pays a second wire-shape change.

## What AV.5 unlocks

Per `<scenario-context-spike>` branch, AV.5 promotes:

- `ScenarioContext` Python primitive — holds a scenario_id + claimed
  account set.
- Composition safety — two generators on the same account_id fail
  loud at compose time (the `tests/unit/test_scenario_context_spike.py`
  pinning).
- Per-row scenario tagging — both `transactions.metadata` and
  `daily_balances.metadata` carry `{"scenario_id": "..."}` for every
  plant; cleanup by scenario_id is a simple `WHERE
  JSON_VALUE(metadata, '$.scenario_id') = '<sid>'`.

Without AV.1, this would need the sidecar table the spike used. With
AV.1, per-row tagging works on both tables uniformly — no sidecar.
