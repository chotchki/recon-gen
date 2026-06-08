# BC.12.3 — `<prefix>_config_kv` spike

**Date:** 2026-05-24
**Trigger:** CI `integration-oracle` job red since the Oracle cell landed. Schema apply against Oracle 23 (and per the original BC.12 brief, Oracle 19c too) fails at stmt #91 with `ORA-32368: cannot create JSON materialized view without relational table` on the `<prefix>_stuck_pending` matview DDL.

The matview LEFT JOINs `JSON_TABLE((SELECT l2_yaml FROM <prefix>_config)::json, '$.rails[*]' COLUMNS (value json PATH '$')) rail`. PG 17 + SQLite are happy with this; Oracle's matview engine refuses a JSON-source matview built on a CLOB.

## Direction (locked per BC.12 brief)

Hybrid EAV + typed-projection views. Replace the 3-column `<prefix>_config(as_of, cfg_yaml, l2_yaml)` table with a `<prefix>_config_kv(node_id, parent_id, key, value)` flattened tree. Matviews JOIN typed views (`<prefix>_v_config_rails`, `<prefix>_v_config_limit_schedules`) — never the EAV directly. The typed views project relational columns from the kv via self-joins on `parent_id`; the matview engine sees plain JOINs over relational tables, no JSON_TABLE-from-CLOB anywhere.

## Spike results (against the running Oracle 23 container at port 60643)

### Spike 1 — `VARCHAR2(4000)` value column

`/tmp/spike_kv.py` — kv table with `value VARCHAR2(4000)`, typed view (`MAX(CASE WHEN k='name'…)` aggregation grouped by parent), matview that JOINs the view. **Result: PASS.** Matview built; LEFT JOIN returns correct cap per rail.

### Spike 2 — `CLOB` value column

`/tmp/spike_kv2.py` — same shape but `value CLOB` (so the `l2_yaml_raw` provenance row fits — sasquatch_pr's full L2 JSON is ~37 KB).

First attempt: `MAX(CASE WHEN k='name' THEN value END)` failed with **ORA-22849: Type CLOB is not supported for this function or operator.** Oracle's MAX doesn't accept CLOB.

Fix: coerce CLOB → VARCHAR2 inside the CASE: `MAX(CASE WHEN k='name' THEN DBMS_LOB.SUBSTR(value, 100, 1) END)`. **PASS.** Matview built; CLOB round-trip works for the long provenance row.

## Decisions (locked)

| Question | Decision | Why |
|---|---|---|
| `value` column type | **CLOB on Oracle, TEXT on PG, TEXT on SQLite** (use existing `text_type` helper) | sasquatch_pr's `l2_yaml_raw` row is ~37 KB — VARCHAR2(4000) would force a split-row provenance encoding, throwing away the "one row, opaque" simplicity. CLOB costs a coerce-via-`DBMS_LOB.SUBSTR` inside the typed views; PG/SQLite are zero-cost. |
| Typed-view CLOB coercion | New `lob_substr(expr, n, dialect)` dialect helper: Oracle `DBMS_LOB.SUBSTR(expr, n, 1)`, PG `SUBSTRING(expr FROM 1 FOR n)`, SQLite `SUBSTR(expr, 1, n)` | Same name across dialects, callers stay portable. Used inside `MAX(CASE WHEN k='X' THEN lob_substr(value,255,dialect) END)`. |
| `node_id` allocation | **Python-side counter at populate time.** Single monotonic counter as the tree-walker visits nodes. | DB sequence would couple table cleanup + sequence reset (and Oracle requires explicit DROP SEQUENCE); a Python counter keeps populate truly atomic via `INSERT … VALUES (…)`. |
| `parent_id` shape | `NUMBER(19)` / `BIGINT` (matches `node_id`), `NULL` for roots. No FK declared — kv is internal-only and the walker writes parents before children, so the constraint adds no value over correctness-by-construction. | Avoid a self-FK that Oracle would force into a deferred constraint anyway for batch inserts. |
| Indexes | `(parent_id, key)` composite. No index on `value` (CLOB-incompatible anyway on Oracle without function-based indexes). | Typed views all filter `parent_id = X AND key = 'Y'` — covering index. |
| `key` column type | `VARCHAR2(255)` / `VARCHAR(255)` / `TEXT` | Bounded — JSON keys are always short identifier-shaped strings; matches existing `varchar_type(255)` convention. |
| Recursive CTE? | **Not needed for the two ship-day typed views.** Both walk a known fixed depth: `rails[*].{name, max_pending_age_seconds, max_unbundled_age_seconds}` is depth-2, `limit_schedules[*].{parent_role, rail, direction, cap}` is depth-2. Three-table self-join suffices. | Recursive CTEs in views feeding matviews work on Oracle (confirmed via existing `inv_money_trail_edges` matview pattern) but a plain self-join is simpler and the kv structure is shallow at the matview-consumed leaves. Recursive CTE remains an option for future deeper-walk needs. |

## Operational lifecycle (post-BC.12)

1. **Once** — `schema apply --execute` creates `<prefix>_config_kv` (DDL bootstrap).
2. **Every deploy** (L2 changes) — `schema apply --execute` re-creates all views + matviews AND re-populates `<prefix>_config_kv` from `--l2`. Heavy but idempotent.
3. **Daily** (post-ETL) — `data refresh --execute` REFRESHes matviews only.

The `<prefix>_config_kv` table is wiped + re-populated atomically at schema-apply time. No DDL migration is ever needed when L2 grows.

## Trigger to delete this layer

If/when Oracle 19c falls off our support floor AND every supported Oracle is 21c+ (native JSON column type), the EAV + typed views can be deleted in favor of the original `JSON_TABLE(l2_yaml, '$.rails[*]')` shape. Document the trigger in `docs/reference/oracle-19c-constraints.md`.

## Backout

If the kv design surfaces a runtime regression, revert the BC.12 commits — the v11.18.0 e2e gate was already red against Oracle, so revert restores the pre-BC.12 state with no functional regression beyond what's already broken.
