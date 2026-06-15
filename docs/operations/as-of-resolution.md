# `as_of` resolution — operator runbook

Every dashboard renders against an `as_of` date — the calendar day the
default `[as_of - N, as_of]` window ends on. This page is the
operator-facing reference for where that value comes from, what to
pin when, and how to read the Info-sheet surface.

## The three valid sources

Resolution priority, in order:

1. **`cfg.test.generator.end_date`** — operator pin in yaml. Use when
   you need a fixed reporting period (end-of-month reconciliation
   snapshot, regulator-defined audit window, test-fixture
   determinism). Survives restarts.
2. **`RECON_GEN_AS_OF_ANCHOR` env var** — operator pin via env, e.g.
   `RECON_GEN_AS_OF_ANCHOR=2026-06-15 recon-gen dashboards -c run/config.yaml`.
   Use for chain-determinism (test runs, CI) or a quick override
   without editing yaml. Doesn't survive restarts.
3. **Data-derived** (`<prefix>_data_anchor` matview) — default. When
   neither pin is set, `recon-gen json apply` and `recon-gen audit
   apply` auto-resolve `as_of` from the latest moment your feed has
   data for (`GREATEST(MAX transaction.posting, MAX daily_balance
   .business_day_end)` per DK.0 design). Tracks the feed automatically.

There is no fourth source. The pre-DK `live(wall-clock today)` fallback
was retired in DK.3 — it produced silently-blank dashboards when the
feed lagged wall-clock today. The data-derived path is the prod
default.

## When to pin `end_date`

Pin `cfg.test.generator.end_date` when you need a stable anchor that
WON'T move with the feed:

- **End-of-period reconciliation snapshot.** Auditing month-end? Pin
  `end_date: 2026-05-31` for the duration of the audit. Re-deploy
  when you're done.
- **Test-fixture determinism.** Locked-anchor (`LOCKED_ANCHOR =
  2030-01-01`) drives byte-identical seed generation; tests that
  depend on byte-stable output set this directly.
- **Frozen reporting period.** Quarterly regulator report? Pin
  `end_date: 2026-06-30` and keep that deploy stable until the next
  reporting period closes.

Otherwise, leave `end_date` unset — the data-derived path means
dashboards naturally follow your ETL cadence without re-deploy.

## When to use the env var

Use `RECON_GEN_AS_OF_ANCHOR` for:

- **Chain determinism.** The test runner exports it at chain start
  so deploy + dataset emit + qs_browser tests agree on one calendar
  day across the run, even when the run straddles local midnight.
- **Quick local override.** "I want today's dashboards to render
  against 2026-06-01 for a spike, no yaml edit" → set the env in
  your shell, run the deploy, unset.

Don't use the env var as a long-term pin — operators don't expect
their dashboards to silently shift if someone resets their shell
profile. Yaml pin is the durable form.

## How to read the Info sheet

Every dashboard's last tab is `i` (the App Info sheet). The deploy
stamp text box surfaces both the resolved value AND the source:

```
recon-gen: v14.4.0
git: 5b7c484d
generated: 2026-06-15T17:42:33Z
dialect: postgres
prefix: recon-prod
as_of (at emit): 2026-06-14
as_of source: data-derived (data_anchor matview)
cadence: 12 sparse, 0 explicit_daily
```

Source values you'll see:

- `cfg.test.generator.end_date` — operator pinned in yaml.
- `RECON_GEN_AS_OF_ANCHOR env` — operator pinned via env.
- `data-derived (data_anchor matview)` — auto-resolved by DK.4 from
  the feed.
- `live (wall-clock fallback)` — should never appear in prod
  post-DK. If you see this, the data_anchor matview was empty or
  unreachable at deploy time (cold DB before first ETL load, OR a
  legacy pre-DK.1 deploy without the matview). Run `recon-gen data
  apply --execute` + `recon-gen data refresh --execute`, then
  re-deploy.

The Info sheet also surfaces a "Latest Balance Day" row that
live-queries `<prefix>_data_anchor` per dashboard load. When that
value lags the `as_of (at emit)` bullet, the ETL has aged since
your last `recon-gen json apply` — dashboards are still showing the
deploy-time anchor, not today. Re-deploy to pick up the new anchor.

## Cold-DB warning shape

If neither pin is set AND the data_anchor matview is empty/unreachable
at `json apply` or `audit apply` time, both commands print a loud
warning to stderr and fall through to `live(wall-clock today)`:

```
warning: <prefix>_data_anchor matview is empty or absent
(recon-prod_data_anchor); falling back to live(wall-clock). Run
`recon-gen data apply --execute` + `recon-gen data refresh
--execute` to populate the feed.
```

The fallback is intentional (so first-time installs and pre-DK
upgrades don't hard-fail), but it's the path you DON'T want to be on
in steady-state prod. The warning ends up in CI logs + operator
shell output; if it surfaces in a deploy you didn't expect, your
ETL is broken.

## Schema reference

The DK.1 singleton matview emit (`<prefix>_data_anchor`):

```sql
CREATE MATERIALIZED VIEW <prefix>_data_anchor AS
SELECT
    1 AS row_marker,
    MAX(anchor) AS data_anchor
FROM (
    SELECT MAX(posting) AS anchor FROM <prefix>_current_transactions
    UNION ALL
    SELECT MAX(business_day_end) AS anchor
      FROM <prefix>_current_daily_balances
     WHERE account_scope = 'internal'
) anchors;
```

`row_marker = 1` (constant) gives the matview a UNIQUE-eligible
column so PG `REFRESH MATERIALIZED VIEW CONCURRENTLY` qualifies
per BV.6.

## Related

- Design lock + rejected alternatives: `docs/audits/dk_0_data_anchor_design.md`.
- Resolution-path code: `src/recon_gen/common/config.py::TestGeneratorConfig.as_of_frame`.
- CLI wire: `src/recon_gen/cli/_helpers.py::maybe_export_data_anchor`.
- Info-sheet bullets: `src/recon_gen/common/sheets/app_info.py::_resolve_as_of_at_emit`.
