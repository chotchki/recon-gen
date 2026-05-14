# X.4.j — Studio + Deploy operator spike

Live-drive walkthrough for the Studio's Deploy-changes pipeline.
Companion to `tests/e2e/test_studio_deploy_browser.py` (browser e2e)
and `tests/e2e/test_deploy_pipeline_pg_to_sqlite.py` (API e2e). The
script `scripts/studio-with-pg-source.sh` is the one-line invocation
that operators reach for when they want to spot-check the pipeline
after editing `common/l2/deploy_pipeline.py` or `_studio_routes.py`.

## What it proves

The cross-dialect pipeline works end-to-end against a real postgres
ETL source:

```
postgres-in-docker (etl source)
     │
     │  ↓ etl_hook (`quicksight-gen data apply --execute`
     │             — re-seeds the postgres)
     │
     ▼
┌────────────────────────────┐
│ Studio /deploy orchestrates │
│   1. etl_hook gate          │
│   2. wipe sqlite tables     │
│   3. pull postgres → sqlite │
│   4. generator (scope=full) │
│   5. matview refresh        │
│   6. data_generation_id++   │
└────────────────────────────┘
     │
     │  ↓ open dashboards reload via the poller (3s)
     │
     ▼
4 dashboards re-render against fresh sqlite data
```

The unit + API e2e cover the orchestration logic; this spike is the
"can the operator actually click Deploy and watch the dashboards
refresh?" credibility check.

## Running it

Prereqs:

- Docker daemon up.
- `uv sync --extra dev --extra audit` (any extras combo that pulls
  `aiosqlite`, `psycopg`, `starlette`, `uvicorn`).
- An L2 yaml on disk. The default is `run/sasquatch_pr.yaml`
  (gitignored — operator config).

Invocation:

```bash
scripts/studio-with-pg-source.sh                    # default L2
scripts/studio-with-pg-source.sh path/to/your.yaml  # explicit
```

The script:

1. Spins `postgres:17-alpine` on a free port.
2. Waits for `pg_isready`.
3. Writes a per-run `pg_etl_cfg.yaml` + `etl_hook.sh` + `studio_cfg.yaml`
   in a tempdir (kept on exit for triage).
4. Applies schema to BOTH the postgres source AND the sqlite
   destination (so the studio + dashboards have tables to render
   against on the initial open).
5. Execs `quicksight-gen studio -c <studio-cfg> --l2 <yaml>` in the
   foreground.
6. On Ctrl+C, the EXIT trap tears down the postgres container.

Studio binds on a random port and prints the URL. Open in a browser.

## What to click

1. The Studio home page renders the topology diagram + chrome bar
   with a "Deploy changes" button.
2. Open one or two dashboards in separate tabs (e.g. L1 / L2FT) so
   you can watch the auto-reload behavior.
3. Back on the Studio home page, click **Deploy changes**.
4. The status indicator transitions:
   - `Deploying…` (yellow) — the pipeline is running. ~30-60s on
     `sasquatch_pr` because the etl_hook re-runs `data apply`
     against postgres.
   - `Deployed (gen N, M tx)` (green) — pipeline succeeded; gen N
     is the new `data_generation_id`, M tx is the post-step-3
     transaction count.
5. Switch back to your dashboard tabs. Within ~3-6s (poller
   interval + reload + re-render), each tab re-renders against the
   fresh sqlite data — without manual F5.
6. Edit the L2 yaml (e.g. add a new Rail), Deploy again, watch
   dashboards reflect the new data.

## Failure modes worth noticing

If the etl_hook fails (e.g. you pass an invalid L2 yaml that
rejects validation):

- The status flips to `Halted: <reason>` (red).
- The sqlite tables stay untouched — wipe never ran.
- The 4 dashboards still show whatever data was there before the
  failed deploy (no torn-down data layer).

This is the halt-on-failed-hook contract from
`X.4.g.4`. If you see "Deployed" reported on a hook that should
have failed, that's a regression — file it against `X.4.g.4`
(`step_1_etl_hook` orchestration in `common/l2/deploy_pipeline.py`).

## Why a spike doc, not a customer walkthrough

Studio is in active development (X.4 phase, slated for v10.0.0
release at X.4.k). The customer-facing walkthroughs at
`src/quicksight_gen/docs/walkthroughs/` cover production-ready
flows. This spike doc captures the operator-iteration story for the
in-development pipeline; once Studio releases, the contents fold
into a `customization/how-do-i-deploy-changes.md` walkthrough.
