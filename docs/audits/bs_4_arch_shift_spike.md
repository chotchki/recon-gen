# BS.4 — Deploy-model shift spike

> **Status:** SPIKE COMPLETE 2026-05-29. Inventory + decisions for the
> D4.arch lock: drop the intermediate upstream→demo_db copy.
> Trace: `SPEC.md::D4.arch`, `PLAN.md::BS.4`.

## TL;DR

The "upstream-pull" path (`step_2_pull` + `EtlDatasourceConfig`) is
**already dormant** — today's deploy pipeline supports both:

- `cfg.etl_datasource=None` → step 2 wipes + skips pull → etl_hook
  is expected to write directly to demo_db. **This is the BS.4 target shape.**
- `cfg.etl_datasource=<set>` → step 2 wipes + copies upstream → demo_db.
  **This is the path BS.4 deletes.**

The "spike before implementing" gate (Lock 4) was hedging against
hidden coupling. Survey finds none — the etl_datasource branch is a
self-contained ~250-LOC block (one function + dialect helpers + cfg
field + dataclass + loader + tests). Deletion is mechanical.

## Touch points (inventory)

### Production code (~280 LOC to delete)

| File | Symbol | LOC | Action |
| ---- | ------ | --- | ------ |
| `src/recon_gen/common/config.py` | `EtlDatasourceConfig` dataclass | ~10 | Delete |
| `src/recon_gen/common/config.py` | `Config.etl_datasource` field | 1 | Delete |
| `src/recon_gen/common/config.py` | `_CONFIG_ALLOWED_KEYS::"etl_datasource"` | 1 | Delete |
| `src/recon_gen/common/config.py` | loader block for `etl_datasource` (lines 774-805) | ~30 | Delete |
| `src/recon_gen/common/l2/deploy_pipeline.py` | `step_2_pull` function | ~90 | Delete |
| `src/recon_gen/common/l2/deploy_pipeline.py` | `_PULL_BATCH_SIZE`, `_connect_etl_source`, `_pull_table`, `_dialect_from_url` helpers | ~100 | Delete |
| `src/recon_gen/common/l2/deploy_pipeline.py` | `run_deploy_pipeline`: remove `step_2_pull` call (line 1047) | 1 | Edit |
| `src/recon_gen/common/l2/deploy_pipeline.py` | `DeploySummary.step2_pull_transactions_pulled` + `step2_pull_daily_balances_pulled` | 2 | Delete |

### Test code (~50 LOC to delete/edit)

| File | References | Action |
| ---- | ---------- | ------ |
| `tests/unit/test_config_loader.py` | 17 — loader validation tests for `etl_datasource` block | Delete the etl_datasource-specific tests; the other Config fields stay covered |
| `tests/unit/test_deploy_pipeline.py` | 27 — covers `step_2_pull` execution paths | Delete pull-specific tests; keep wipe + etl_hook + generator + matview + reload coverage |
| `tests/unit/test_tg_cache.py` | mentions `etl_hook_enabled` only (NOT etl_datasource) | Untouched — etl_hook stays |
| `tests/e2e/_studio_deploy_helpers.py` | 6 — fixture builders | Drop etl_datasource setup |
| `tests/e2e/test_studio_deploy_browser.py` | 3 — references in browser assertions | Drop pull-event assertions |
| `tests/e2e/test_deploy_pipeline_pg_to_sqlite.py` | 4 — cross-dialect pull test | **Probable delete** — the whole point was cross-dialect upstream copy. Without `step_2_pull`, the test has nothing to prove. |

### Docs (light touch)

| File | Action |
| ---- | ------ |
| `src/recon_gen/common/etl_examples.py` | Already says "etl_hook converts dollars → cents" — no etl_datasource mention. Untouched. |
| `src/recon_gen/docs/Schema_v6.md` | Same — etl_hook references stay valid. Untouched. |
| `src/recon_gen/docs/walkthroughs/customization/how-do-i-run-my-first-deploy.md` | Mentions `common/deploy.py` step pipeline. Re-check for etl_datasource references; trim if present. |

### Studio routes (no change needed)

The Studio `POST /deploy` endpoint calls `run_deploy_pipeline(cfg,
instance)` — the orchestrator's signature is unchanged. The DeploySummary
fields drop is the only edge; the timeline UI's "pulled N rows from
upstream" rows disappear naturally when the data isn't there.

## Decision: delete (not keep-as-opt-in)

Per `[[feedback_no_compat_shims]]` (pre-stable default: drop the escape
hatch). The user is the only operator; there's no external integrator
to consider. Keeping `etl_datasource` as opt-in would mean carrying
dead code paths through BT/BU/BV without ever exercising them.

Per SPEC.md::D4.arch: "Defer to implementation; both viable." This
spike resolves to **delete**.

## Decision: etl_hook contract clarification

Today's `etl_hook` is a string command (subprocess). It runs in step
1, BEFORE step 2's wipe. Two implicit modes:

- **upstream mode (legacy)**: etl_hook writes to the upstream DB
  (configured via `etl_datasource`); step 2's pull copies the rows.
- **direct mode (BS.4)**: etl_hook writes directly to demo_db.

Post-BS.4 only direct mode exists. The contract clarification:

- Step 1 etl_hook stays where it is (BEFORE step 2's wipe). But the
  semantics change: it now races with step 2's wipe.
- **Reorder needed:** step 2's wipe must run BEFORE step 1's etl_hook
  so etl_hook writes against a clean slate. The current order
  (`step_1 → step_2_wipe → step_2_pull`) was right for upstream mode
  (etl_hook populates upstream; we then wipe demo + pull). Direct
  mode needs `step_2_wipe → step_1_etl_hook` (wipe demo first, then
  etl_hook writes directly into it).

**Implementation impact:** swap step ordering in `run_deploy_pipeline`.
Existing `step_1_etl_hook` and `step_2_wipe` functions stay; only the
orchestrator call sequence changes. Step numbering in events
(`deploy:step1:start`, `deploy:step2:wipe:start`) stays for log
continuity — the numbers don't imply execution order.

Wait — the SPEC says `truncate(demo_db) → ETL hook (writes to demo_db
directly) → matview refresh`. That's wipe-first. Confirms the reorder.

## Implementation plan (after spike sign-off)

1. **Reorder in `run_deploy_pipeline`:** call step_2_wipe before
   step_1_etl_hook.
2. **Delete `step_2_pull`** + its helpers (`_connect_etl_source`,
   `_pull_table`, `_PULL_BATCH_SIZE`, `_dialect_from_url`).
3. **Delete `EtlDatasourceConfig`** + the `Config.etl_datasource`
   field + the loader block + the `_CONFIG_ALLOWED_KEYS` entry.
4. **Update `DeploySummary`:** drop `step2_pull_*` fields.
5. **Update tests:** delete pull-specific tests, drop etl_datasource
   fixtures, retire `tests/e2e/test_deploy_pipeline_pg_to_sqlite.py`.
6. **Verify CLAUDE.md / SPEC.md references** to `etl_datasource` and
   strike them.
7. **Verify the etl-example handbook page** doesn't reference
   `etl_datasource`.
8. **Run full suite** + commit.

Estimated implementation time: **45-60 min** (the spike reduced the
estimate from BS.0's "2-4h" because the upstream-pull branch turned
out to be already-isolated; no scattered helpers, no hidden coupling).

## Out of scope (BT/BU territory)

- New ETL Support UI surfaces (D4.surface 1/2/3) — those are BT.
- Per-column-pair contract derivation (D4.sub-asks) — BT.5.
- Test-data-generator-as-etl-hook dogfood claim — BV.2.
