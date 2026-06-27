# Recon Generator

[![CI](https://github.com/chotchki/recon-gen/actions/workflows/ci.yml/badge.svg)](https://github.com/chotchki/recon-gen/actions/workflows/ci.yml)
[![Coverage](https://raw.githubusercontent.com/chotchki/recon-gen/badges/coverage-badge.svg)](https://github.com/chotchki/recon-gen/blob/badges/coverage-report.md)
[![PyPI](https://img.shields.io/pypi/v/recon-gen.svg)](https://pypi.org/project/recon-gen/)

## What it does

Recon Generator is an independent validation tool for midsize financial institutions: it tells you whether your books balance day to day, and when they don't, where to look first.

- Accounting is standard. We call it L1, meaning layer 1 in this tool.
- Your institution is not. We call it L2, meaning layer 2 in this tool.

Recon Generator layers the two: standard double-entry invariants on top of the unique shape you declare (your accounts, your rails, your multi-leg transfer templates, your bundling rules, your aging caps), so every way you actually move money is checked against the rules that govern it.

## Who it's for

No single role sees the whole reconciliation, so the tool carries a surface per role:

- **Integrators** — wiring the institution's shape into the tool (Studio editor, L2 Flow Tracing, Hygiene Exceptions).
- **Trainers** — shaping the demo and seeded scenarios so the dashboards exercise every path before go-live (data-shaping panel, scope knobs, plant overlays).
- **Operators** — driving the L1 invariants daily, walking exceptions back to their cause (L1 Dashboard, Daily Statement, L1 Exceptions).
- **Investigators / Executives** — compliance-AML triage and board-cadence rollups off the same base ledger (Investigation + Executives apps).

Every surface speaks YOUR institution's vocabulary — account names, role labels and persona prose all come from the L2 institution YAML, substituted into the rendered output. Swap the L2, the language follows.

**See it live — two public demos, no install:**

- **[Spec Example](https://recon-gen-spec.hotchkiss.io/)** — the smallest viable bank, dashboards only. The four bundled apps served by the self-hosted HTMX runtime.
- **[Sasquatch Bank Example](https://recon-gen-sasquatch.hotchkiss.io/)** — a fuller community-bank flavor, served through the **Studio** surface in read-only demo mode (L2 editor, unified diagram and data-shaping panel — every mutation locked down).

Both render straight from the bundled L2 YAMLs (`tests/l2/{spec_example,sasquatch_pr}.yaml`): read them to evaluate the tool, fork one to start your own. The full persona-driven handbooks, walkthroughs and per-sheet explainers your operators would see live at **[GitHub Pages](https://chotchki.github.io/recon-gen/)**; the Python API reference (tree primitives, dataset contract, db helpers, runner internals — everything ETL authors and integrators crib from) lives at **[ReadTheDocs](https://recon-gen.readthedocs.io/en/latest/)**.

## Architecture

Everything generates from one L2 YAML — your institution's shape — plus your ETL feed, through one shared core, into three renderers. The core is layered **L1 → L2 → L3**: persona-blind primitives, per-app assembly in domain vocabulary, then your persona / customer flavor.

```mermaid
flowchart LR
    YAML["L2 institution YAML<br/>accounts · rails · templates · chains · limits · theme"]
    FEED[("ETL feed<br/>transactions + daily_balances")]

    subgraph core["one shared core"]
      direction TB
      L1["L1 · persona-blind primitives<br/>common/tree · common/models · common/ids"]
      L2["L2 · per-app assembly, domain vocab<br/>apps: l1_dashboard · l2_flow_tracing · investigation · executives"]
      L3["L3 · persona / customer flavor<br/>apps/*/datasets.py SQL · L2 yaml persona block"]
      L1 --> L2 --> L3
    end

    YAML --> core
    FEED --> core
    core --> QS["AWS QuickSight<br/>JSON resource graph"]
    core --> HTMX["Self-hosted HTMX<br/>Dashboards + Studio"]
    core --> PDF["Auditor-ready PDF<br/>audit report"]
```

Browse the full module tree on [GitHub](https://github.com/chotchki/recon-gen/tree/main/src/recon_gen); every module's API reference lives on [ReadTheDocs](https://recon-gen.readthedocs.io/en/latest/).

## Not an ETL tool

Recon Generator validates data; it doesn't move it. Your transactions and daily-balance feeds land in `<prefix>_transactions` and `<prefix>_daily_balances` (the Data Integration handbook documents the column contract), and Recon Generator reads from there.

We help you implement in two ways:

- **Wiring it in.** Mapping an upstream system into the L1 schema is real work (column mapping, type narrowing, metadata extraction, the supersession contract). The Data Integration handbook documents it column-by-column, and the Studio Deploy-changes pipeline carries an ETL hook so your existing extract plugs in without bolting code onto Recon Generator itself.
- **Synthetic scenarios on your real data.** Once your data is flowing, the test-data generator plants extra scenarios on top (drift events, overdraft breaches, stuck-pending aging, supersession trails, fanout patterns, anomaly spikes) so you can validate every L1 invariant without delaying go-live. Trainer knobs (`scope: full / uncovered_rails / exceptions_only / only_template`, `derive_balances`) shape what gets generated.

## Where it runs

Database backends — **PostgreSQL 17+** and **Oracle 19c+** for the on-prem / cloud-managed production targets, plus **DuckDB** as the zero-install integrator-laptop backend — a pure-Python wheel with an in-process vectorized executor and no server to stand up. The prior SQLite backend was dropped in v13.0.0 since it isn't optimized for analytics.

Multiple runtime environments — pick what your auditors and analysts already trust:

> **QuickSight support is being REMOVED.** AWS now charges a flat **$250 / account / month [infrastructure fee](https://aws.amazon.com/quick/pricing/)** the moment you cross into the Professional tier (on top of $20–40 / user / month), and it keeps pushing the product HARDER — the forced rebrand to "Amazon Quick", the GenAI agent-hour upsell. That recurring, creeping AWS overhead doesn't fit an independent, offline-first validation tool. The QuickSight renderer is now optional (the `[quicksight]` extra) and goes away in an upcoming release. MIGRATE NOW to the self-hosted dashboards — the same four apps, no AWS account.

- **Self-hosted HTMX web app** — the same dashboards with NO AWS dependency, so it runs offline. For sensitive deployments that can't reach external SaaS. The supported path.
- **AWS QuickSight** — managed BI you embed in your own portal; permissions follow the QS user. OPTIONAL (the `[quicksight]` extra; a plain install is QS-free) and BEING REMOVED in an upcoming release as AWS keeps changing their pricing.
- **Auditor-ready PDF audit report** — printable and cryptographically fingerprinted (optionally pyHanko-signed). Same source data as the dashboards, and an end-of-pipeline [4-way agreement test](https://github.com/chotchki/recon-gen/blob/main/tests/e2e/qs_browser/test_audit_invariants_agreement.py) (against the underlying sql) gates that they stay in agreement.

## How it's tested

You can't ship a reconciliation tool on "trust me." This tool ships with:

- **Layered test gates** that run in order — unit → db → app2 → qs_api → qs_browser — so a regression at layer N short-circuits before burning minutes on layer N+1.
- **Strong typing throughout** (Pyright strict on the core, NewType-wrapped identifiers and dataclass invariants), so an entire class of bug becomes a type error at the wiring site instead of a silent zero-row dashboard.
- **Fuzz testing as a property axis** — every test variant runs against random L2 institution shapes (`fuzz:N` for N seeds, pinned via `f<seed>_..` for repro), so the same invariants check against shapes nobody hand-wrote.
- **Deterministic, exhaustive test-data generation** — your L2 institution shape drives positive and negative scenarios that the harness plants automatically: drift, overdraft, limit breach, stuck-pending, stuck-unbundled, supersession audit, fanout, anomaly spikes, money-trail chains. Each scenario is shape-locked per `(L2 instance, dialect)`.
- **Cross-runtime parity** — the same scenario fans out into the QuickSight cell, the self-hosted cell, the audit PDF and the underlying SQL — a 4-way agreement test gates that all four agree on every L1 invariant violation set (the drift the dashboard shows is the drift the PDF prints).

---

The CLI is five artifact groups — `recon-gen schema | data | json | docs | audit` — plus two server commands, `studio` and `dashboards` (below). Each artifact group runs `apply` / `clean` / `test` (audit adds `verify`, which recomputes a generated PDF's provenance fingerprint); anything destructive defaults to emit and needs `--execute` before it touches the DB, AWS or disk. Change the Python (or ask Claude) and re-run `json apply --execute` — you get a new dashboard.

## Demo Docs

- **[L1 Dashboard handbook](https://chotchki.github.io/recon-gen/handbook/l1/)** — 11 sheets covering 5 baseline L1 invariants + 2 aging-watch invariants + supersession audit + per-account-day walk + raw posting ledger. Switch the L2 instance to switch the persona prose without touching dashboard code.
- **[L2 Flow Tracing handbook](https://chotchki.github.io/recon-gen/handbook/l2_flow_tracing/)** — Rails / Chains / Transfer Templates / L2 Hygiene Exceptions for L2 spec verification.
- **[Investigation handbook](https://chotchki.github.io/recon-gen/handbook/investigation/)** — Compliance / Investigation team flow. 4 walkthroughs, one per sheet's question.
- **[Executives handbook](https://chotchki.github.io/recon-gen/handbook/executives/)** — board scorecard: account coverage, transaction volume, money moved.
- **[Data Integration handbook](https://chotchki.github.io/recon-gen/handbook/etl/)** — how the Data Integration Team maps an upstream system into `<prefix>_transactions` + `<prefix>_daily_balances`, validates the load and extends the metadata contract.
- **[Audit Reconciliation Report handbook](https://chotchki.github.io/recon-gen/handbook/audit/)** — auditor-ready PDF generated by `recon-gen audit apply`; covers the L1 invariants, embeds a provenance fingerprint, optionally auto-signs via pyHanko.

Source lives in `src/recon_gen/docs/` (shipped with the wheel — extract with `recon-gen docs export -o ./somewhere/`); rebuild locally with `recon-gen docs serve`.

## Quick start

### Prerequisites

- Python 3.14+
- A PostgreSQL 17+ / Oracle 19c+ / DuckDB database URL for demo mode (PG and Oracle use SQL/JSON path syntax — `JSON_VALUE` / `JSON_QUERY` / `JSON_EXISTS`; DuckDB uses `json_extract_string`)
- ONLY if you opt into the QuickSight target (the optional `[quicksight]` extra, BEING REMOVED — see above; the self-hosted dashboards need no AWS account): an AWS account with QuickSight Enterprise enabled, plus either a pre-existing QuickSight datasource ARN or the demo DB URL above
  - **WARNING:** the Professional and Enterprise tiers carry a flat **$250 / account / month infrastructure fee** (NOT prorated) on top of the $20–40 / user / month seat — see [AWS's own pricing](https://aws.amazon.com/quick/pricing/). This is a big reason QuickSight is being removed.

### Install from PyPI

For consumers — using a pre-existing QuickSight datasource ARN:

```bash
pip install recon-gen
```

For demo mode against PostgreSQL 17+ or Oracle 19c+ (the `prod` extra bundles both drivers — `psycopg[binary,pool]` and `oracledb` thin mode, no Oracle Instant Client install):

```bash
pip install "recon-gen[prod]"
```

For demo mode against DuckDB (no extra install — DuckDB ships as a pure-Python wheel in the base install):

```bash
pip install recon-gen
```

> The package was renamed from `quicksight-gen` to `recon-gen` in v11.0.0. `pip install quicksight-gen` still resolves via a meta-package shim (being retired VERY soon™ — switch to `recon-gen`).

### Setup from source

The repo uses [uv](https://docs.astral.sh/uv/) for env / lock management
(deterministic resolution from `uv.lock`). One command sets up `.venv/`
with every extra:

```bash
uv sync --all-extras
```

Then invoke tools directly via the venv (no `source activate` needed):

```bash
.venv/bin/pytest
.venv/bin/recon-gen --help
```

For a leaner install, swap `--all-extras` for the three real extras (collapsed from eight in BS.6 — one knob per persona):

- `--extra dev` (unit tests + pyright)
- `--extra prod` (everything a production run needs — DB drivers, AWS deploy, the self-hosted server, PDF + docs)
- `--extra e2e` (Playwright + boto3 for the browser / API layers)

If you'd rather stick with pip, the standard PEP-621 path still works:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

### Configure

> **v14.0.0 cfg shape (shipped 2026-06-14).** Replaced the previous flat-field shape with concern-grouped nested blocks (`aws:` / `db:` / `app2:` / `audit:` / `auth:` / `test:`) and `extends:` inheritance for base + per-env overlays. Field accessors are `cfg.aws.account_id` / `cfg.db.url` / `cfg.auth.aws.profile` etc. Full migration map (every v13 key → its v14 path): [`docs/audits/de_0_cfg_redesign.md#migration-v13x--v1400-hard-break`](docs/audits/de_0_cfg_redesign.md#migration-v13x--v1400-hard-break).

See [`config.example.yaml`](https://github.com/chotchki/recon-gen/blob/main/config.example.yaml)

```bash
cp config.example.yaml config.yaml
```

`extends:` lets you compose a base cfg with per-env overlays — child wins, dicts deep-merge:

```yaml
# config.prod.yaml
extends: ./config.base.yaml
aws:
  deployment_name: "recon-prod"
db:
  url: "postgresql://prod-user:pass@prod-host:5432/recon"
```

> Theme is declared inline on the L2 institution YAML's `theme:` block, not on the deploy config. When the L2 instance carries no `theme:` block, AWS QuickSight CLASSIC takes over at deploy.

All values can also be set via `RECON_GEN_`-prefixed environment variables (e.g. `RECON_GEN_AWS_ACCOUNT_ID` / `RECON_GEN_DEMO_DATABASE_URL` / `RECON_GEN_DIALECT`). Env vars override YAML.

### Generate and deploy

```bash
# Generate JSON for all four bundled apps to out/
recon-gen json apply -c config.yaml -o out/

# Same emit, then deploy to AWS (delete-then-create, idempotent)
recon-gen json apply -c config.yaml -o out/ --execute

# Override the L2 instance (defaults to bundled spec_example)
recon-gen json apply -c config.yaml -o out/ --l2 run/sasquatch_pr.yaml --execute
```

`json apply --execute` polls async resources (analyses, dashboards) until they reach a terminal state. Resources with the `ManagedBy: recon-gen` tag that aren't in the current output aren't touched — clean those up explicitly:

```bash
recon-gen json clean              # dry-run: list stale tagged resources
recon-gen json clean --execute    # delete them
```

## Demo mode

A deterministic demo generator seeds the four apps so you can see them work without wiring up real data. Every app feeds two per-prefix base tables — `<db_table_prefix>_transactions` (every money-movement leg) and `<db_table_prefix>_daily_balances` (per-account end-of-day snapshots), where `<db_table_prefix>` is `cfg.db.table_prefix` (required).

```bash
# Apply schema + seed to your demo database, then generate QuickSight JSON.
# Requires: db.url + db.dialect in config.yaml and the `[prod]` extra
# installed (bundles psycopg + oracledb; DuckDB needs no extra).
# Per-prefix DDL + seed are emitted at apply time using cfg.db.table_prefix.
recon-gen schema apply -c config.yaml --execute   # tables + matviews
recon-gen data apply   -c config.yaml --execute   # 90-day baseline + plants
recon-gen data refresh -c config.yaml --execute   # populate matviews
recon-gen json apply   -c config.yaml -o out/ --execute  # JSON + AWS deploy
recon-gen audit apply  -c config.yaml --execute -o report.pdf  # auditor-ready PDF (optional)
```

Datasets are all Direct Query (no SPICE), so seed changes show up immediately after a fresh `data apply --execute` + `data refresh --execute` — no QuickSight-side refresh needed.

### Demo scenarios

Two L2 institution YAMLs ship in `tests/l2/`:

- **`spec_example.yaml`** — the persona-neutral default fixture. Generic accounts/rails/chains exercising every L2 primitive without naming a specific institution.
- **`sasquatch_pr.yaml`** — a flavored Sasquatch National Bank persona block carrying the curated demo narrative: SNB control accounts, templated merchant DDAs, Investigation anchor (Juniper Ridge LLC) with three converging scenarios (12-sender fanout cluster, a Cascadia Trust Bank Operations → Juniper anomaly spike, 4-hop layering chain through shell entities).

Pass `--l2 tests/l2/sasquatch_pr.yaml` (or your own) to switch the rendered handbook + demo data narrative without touching dashboard code.

## Self-hosted: Dashboards and Studio

The four apps render off the same L2 instance two ways. **AWS QuickSight** is one — `json apply --execute` pushes the JSON resource graph (above). The other is the self-hosted stack: an HTMX + d3 server that reads the database directly, no AWS account in the loop. It comes at two depths.

**Dashboards** is the lean read-only mount — one process serves all four apps plus the handbook at `/docs`:

```bash
pip install 'recon-gen[prod]'
recon-gen dashboards -c config.yaml                # one process, all 4 apps + the handbook at /docs
# → http://127.0.0.1:8765/dashboards
```

It speaks all three SQL dialects (PostgreSQL / Oracle / DuckDB); point `db.url` at any of them. The schema + seed have to already be applied (`schema apply --execute`, `data apply --execute`, `data refresh --execute`) — Dashboards only reads. It's stateless: every GET re-runs the query, filter state round-trips as `?param_X=…` query params (so the URL is the cache key), no auth/sessions — put it behind your own auth front on a network. All browser-side assets (htmx, d3, the filter widgets) ship inside the wheel — it runs offline.

**Studio** (`recon-gen studio`) is everything Dashboards mounts plus the implementation surface we hand integrators, trainers and ETL engineers — the L2-YAML editor, the unified diagram (your accounts / rails / chains / templates as a graph you edit in place), the data-shaping panel (trainer knobs + scenario plants) and Deploy-changes orchestration with an ETL hook. The YAML on disk stays the source of truth; every save is an atomic write. This is the offline-iteration loop — edit the shape and refresh the page, no deploy cycle and no AWS round-trip.

The self-hosted stack isn't a lesser copy of QuickSight. A 4-way agreement test (`scenario plants ⊆ direct matview SELECT == QuickSight == Dashboards`, `== audit PDF` where it applies) gates the release, so it matches QuickSight on every L1 invariant violation set — enforced, not just claimed. And with QuickSight on a deprecation path (AWS pricing, see above), this is where the tool is going. Full reference — what ships in the wheel, the maintainer recipes for bumping a vendored asset — in the handbook's [Self-hosting the dashboards](https://chotchki.github.io/recon-gen/reference/self-host/) page.

## Theming

Theme is declared inline on the L2 institution YAML's `theme:` block. When the L2 instance carries no `theme:` block, `build_theme` returns `None` and AWS QuickSight CLASSIC takes over at deploy (silent-fallback contract). The single `DEFAULT_PRESET` in `common/theme.py` is the in-canvas-accent fallback for apps when their L2 instance declares no theme — no registry, no CLI flag.

To customize the demo persona's brand, launch studio and use its built-in editor!

## Tests

```bash
./run_tests.sh up_to=unit                                  # ~20s, no DB / no AWS
./run_tests.sh up_to=db                                    # db layer, xdist-parallel
./run_tests.sh up_to=db --only test_drift                  # narrow within a layer (pytest -k)
./run_tests.sh up_to=qs_browser                            # full chain through Playwright
./run_tests.sh up_to=qs_api                                # API layer (boto3, live QS)
./run_tests.sh sweep --yes                                 # cleanup orphan AWS resources
```

The runner enforces ordering — invoking layer N runs layers 1..N-1 first. Layers: `unit → db → app2 → qs_api → qs_browser` (the standalone `deploy` layer was retired in Phase DI; QS deploy now fires inside pytest via the session-scoped `qs_deployed` autouse fixture in `tests/e2e/conftest.py` whenever the layer crosses into AWS-touching territory). See `CLAUDE.md::Test sequencing` for the full guide.

**Triage a specific failing test:** `./run_tests.sh triage <pytest_nodeid>` spawns the appropriate DB container, lets the `qs_deployed` fixture handle QS deploy idempotently, and drops into a screen-attached pdb at the failure line. Multi-client — both operator and assistant can drive pdb via `screen -x recon-gen-triage` / `screen -S recon-gen-triage -X stuff $'<cmd>\n'`. Teardown: `./run_tests.sh triage-down --yes` (kills the screen session, sweeps QS resources, stops the triage container). Full runbook: `CLAUDE.md::Triage workflow` section.

Coverage:

- **Unit / integration**: models, tags, config, CLI, demo determinism + scenario coverage (per-instance SHA256 seed-hash locks), tree primitives + validators, dataset builders, visual builders, filter groups, cross-reference validation (dataset ARNs, filter bindings, visual ID uniqueness, sheet scoping), explanation coverage, schema + seed SQL structure for both Postgres + Oracle.
- **E2E**: two layers — qs_api and qs_browser — that collect by default (the standalone `RECON_GEN_E2E=1` gate was retired in DJ.1); the QS legs skip when `RECON_E2E_USER_ARN` is unavailable (auto-derived from `cfg.auth.aws.profile`).
  - *API layer (boto3)* — resource existence, status, dashboard structure (per-sheet visual counts, parameter / filter-group source-of-truth checks), dataset import health.
  - *Browser layer (Playwright WebKit, headless)* — dashboard loads via pre-authenticated embed URL, sheet tabs, per-sheet visual counts + spot-checked titles, drill-downs, mutual-filter reconciliation tables, date-range filter narrowing, Show-Only-X toggles, Investigation slider + dropdown filters.

E2E tunables (env vars): `RECON_E2E_PAGE_TIMEOUT`, `RECON_E2E_VISUAL_TIMEOUT`, `RECON_E2E_USER_ARN`, `RECON_E2E_IDENTITY_REGION`. `RECON_E2E_USER_ARN` is auto-derived from `cfg.auth.aws.profile` (via STS + `quicksight:ListUsers`) when unset, so the operator-side cfg block is the canonical wiring. Failure screenshots land in `tests/e2e/screenshots/<app>/` (gitignored).
