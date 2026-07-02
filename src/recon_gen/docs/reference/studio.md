# Studio — the L2 editor + data-shaping workbench

Studio is the four bundled dashboards PLUS a set of implementation tools on top: a unified topology diagram, an in-browser L2 editor, a data-shaping panel, a live violation trainer and a Deploy-changes button that re-materializes the demo database from your edits. It's the offline authoring loop — change the institution's shape or reshape its data, click Deploy, watch the dashboards re-render — all against a local database, no AWS.

It builds on [Dashboards](self-host.md) — the same Starlette process and dialect-aware SQL, and it inherits the whole [wheel-bundled offline story](self-host.md#what-gets-bundled-in-the-wheel-offline-by-design) (vendored browser libs, nothing fetched at runtime). The difference is write access — `recon-gen dashboards` only reads; `recon-gen studio` edits the L2 YAML and rewrites the demo data.

Scope: Studio shapes a *demo* database so you can see your L2 rendered before you point it at real feeds. It is NOT a production deploy tool — there's no dry-run on the Deploy button (it always writes) and the seed generator it runs is synthetic. Real customer data arrives through the [ETL hook](../handbook/etl-hook.md), never through Studio's generator.

## Running it

```bash
pip install 'recon-gen[prod]'                        # same extra as Dashboards
recon-gen studio -c config.yaml --l2 run/my_l2.yaml
# → http://127.0.0.1:8765/   (Studio owns the root; dashboards live under /dashboards)
```

`--l2` is required (Studio refuses to start without one — it's the shape you're editing). `-c/--config` points at the database + dialect the same way the rest of the pipeline does; the schema has to already exist (`recon-gen schema apply --execute`) or the trainer can't build its overlay. The `[prod]` extra is what ships the server — see [what each extra unlocks](install.md#what-each-extra-unlocks).

Useful flags:

| Flag | Default | Effect |
|---|---|---|
| `--host` / `--port` | `127.0.0.1` / `8765` | Bind address + port. `--host 0.0.0.0` to expose on a network (put your own auth in front). |
| `--app <slug>` | `all` | Mount one dashboard instead of four (faster startup); doesn't touch the Studio tools. |
| `--docs` / `--no-docs` | `--docs` | Build the handbook against your `--l2` on launch and serve it at `/docs`. `--no-docs` drops the build + the nav link. |
| `--docs-dir <path>` | — | Serve a PRE-built docs site instead of building on launch (overrides `--no-docs`). |
| `--dev-log` | off | Forward browser console + HTMX events to the server log and disable response caching — for debugging a render, not for normal use. |
| `--tls-cert` / `--tls-key` | — | Serve HTTPS. All-or-nothing (setting one without the other is an error). |

## The unified diagram

The diagram (`/diagram`, also the link in the top nav) is a Graphviz render of your L2's topology — it's how the accounts, rails, templates and chains actually connect. It's built as DOT server-side then rendered to SVG in your browser by a WebAssembly Graphviz (no system `dot` binary, nothing fetched at runtime — same offline posture as everything else).

Reading it:

- **Roles (accounts)** — a cylinder is an internal ledger account, a note-shape is an external counterparty (whose balance isn't ours to track), a folder is a templated role that stamps out many instances.
- **Rails** are chevrons with the direction baked into the silhouette; **templates** are tables of their member legs.
- **Limit schedules are NOT their own node** — a role that carries caps shows a `($ caps)` label on the edge to its control parent. There's no click-through to a limit schedule from the diagram (edit it from the [editor](#the-l2-editor) instead).

The view is layered so it doesn't dump everything at once. A bare `/diagram` opens on **layer 1 — roles + control hierarchy only**; step up to `+ Rails` then `+ Chains & Templates` with the layer pills. Don't be surprised the first view has no rails — that's the default, not a broken render.

Edit affordances:

- **Left-click a node** to focus it — the server re-lays-out just that node plus its one-hop neighbors.
- **Hover a rail or template** and an **Edit** badge appears top-right, jumping straight to that entity's editor page. Roles get no badge (a role can back several accounts, so the target's ambiguous) — **right-click** any node instead for a menu that lists every editable carrier.
- **Overlays** (checkboxes): *Coverage* colors each node by live ETL row-count and greys out anything your feed hasn't populated (only offered when a database is wired); *Trainer* draws an amber outline on every node the live trainer has planted a violation into.

State model: `?layer`, `?focus` and the category toggles round-trip through the URL, so a focused view is bookmarkable — but the role/edge-label/overlay checkboxes are client-side only and RESET to defaults (roles shown, overlays off) on a reload. Set them each session.

## The L2 editor

The editor is the Studio landing page and the `/l2_shape/…` routes behind it. You edit the institution's shape one entity at a time — accounts, account templates, rails, transfer templates, chains, limit schedules — plus three singletons: the theme, the instance description (institution name + acronym) and the footer attribution. (There's no separate "persona" editor; those fields moved onto the instance singleton.)

Every save runs the same pipeline: coerce the form → apply the change → validate the whole L2 → persist. What you get back:

- **Validation errors surface as a banner at the top of the form** (not per-field), preserving what you typed. A leading `[CODE]` splits into a mono code chip with a `[?]` that opens the glossary explanation for that rule.
- **Renames cascade.** Change a rail or template name (or an account's role) and every downstream reference rewrites with it.
- **Deletes are guarded.** Delete opens a 5-second signed countdown, and the server re-checks that nothing still references the entity before it lets the delete through — you can't strip a rail that a template still points at (it'll refuse with the structural-break reason). Restarting Studio mid-countdown invalidates the pending confirm (the signing secret is per-process); just click Delete again.

**Persistence — the one footgun worth internalizing.** Every save is an atomic write of the WHOLE L2 model back to the file you pointed `--l2` at. The serializer is stable at the model level, NOT byte-for-byte — so it reflows the file and **drops the YAML's freeform `#` comments**. If you edit your canonical L2 through Studio, keep it in version control and don't hand-maintain comments in it. (The bundled demo sidesteps this entirely — its launcher points `--l2` at a throwaway copy that's wiped on restart, so the shipped spec is never touched.)

Your `config.yaml`, by contrast, is NEVER rewritten by Studio. The data-shaping knobs below persist to a sibling `.studio-state.yaml` precisely so your operator-authored cfg comments survive — that comment-preservation is a cfg-file property, not an L2-file one.

## Reshaping the demo data

Studio has two on-screen surfaces for shaping what the dashboards show, and they do different jobs. (A third set of density knobs is CLI-only — see the note at the end.)

### The data-shaping panel (`/data`)

This is the seed generator's control surface — it changes what the next Deploy generates:

- **Plant kinds** — which of the six baseline violation classes (drift, overdraft, limit breach, stuck pending, stuck unbundled, supersession) to seed. Empty selection means all of them.
- **Scenario window + "up to"** — the timeline the data spans, and a scrub head that truncates the simulation at a cutoff date (watch the dashboards fill in as you drag it forward).
- **Seed** — pin the RNG seed for a reproducible dataset, roll a fresh one, or clear it back to the locked default.
- **Scope** — `full` (baseline + plants, byte-identical to `data apply`), `uncovered_rails` (fill coverage gaps only), `exceptions_only` (just the plants) or `only_template` (one template's closure).
- **Derive balances** / **ETL hook** — toggles for the post-seed balance derivation and whether Deploy runs your [ETL hook](../handbook/etl-hook.md).

These knobs persist to `<config-dir>/.studio-state.yaml` and survive a restart — the sidefile that keeps your `config.yaml` comments intact (above). Delete it to reset to the cfg defaults.

### The live trainer (`/training/`)

The trainer is a different beast — it plants violations into a LIVE copy of the data without re-running the whole generator. Check any of its 26 violation kinds (grouped by family), tune the per-plant primitives (how many days ago, the amounts, the row counts), and Apply. Two things make it safe to poke at:

- It works against a `<prefix>_v_*` overlay it builds on **Session Start** — your production `<prefix>_*` tables are NEVER touched. **Cleanup** drops the overlay and you're back to clean.
- Plant selections live in the overlay database itself, so they survive navigation and restart until you Cleanup.

Session Start needs the base schema to already exist — if it doesn't, the trainer tells you to run `recon-gen schema apply --execute` first rather than failing cryptically.

!!! note "The density knobs are CLI-only"
    The `densify` / broken-rail / inv-fanout multipliers that scale the baseline seed are NOT in either Studio panel — they're `recon-gen data apply --seed-density <n>` (a single scalar, `1.0` = the locked seed, `2.0` doubles the plants). A tooltip in the trainer tint may imply otherwise; the panel doesn't expose them.

## Deploy changes

The **Deploy changes** button re-materializes the demo database against whatever the editor and knobs currently hold (the in-memory shape, not the on-disk YAML — so you can deploy an edit before you've even reloaded). It runs an in-process pipeline: wipe the two base tables → run your ETL hook if one's configured → generate the synthetic seed → derive balances → refresh the matviews → bump a reload counter so any open dashboard tab reloads itself.

Two things to know:

- **There's no dry-run.** Unlike the `schema apply` / `data apply` CLI verbs (which emit SQL unless you pass `--execute`), the Deploy button ALWAYS writes to the demo database. It reuses the exact same builders the CLI verbs do, it just skips the emit-only gate.
- **In standalone mode it refuses.** If no ETL hook is configured (or you've toggled it off), Deploy returns a refusal banner instead of running — because a wipe with nothing to refill would drop rows it can't tell apart from real data. The full truncate-vs-refuse rule lives in the [`cfg.app2.etl_hook` ⇄ standalone-mode contract](../handbook/etl-hook.md#the-cfgapp2etl_hook-standalone-mode-contract).

On a never-seeded database the wipe step emits the schema for you, so a first Deploy works without a separate `schema apply`.

## The live dashboards + your ETL

The four apps render under `/dashboards` (one bookmarkable link each: `l1_dashboard`, `l2_flow_tracing`, `investigation`, `executives`) — the same renderer as standalone [Dashboards](self-host.md), just mounted alongside the Studio tools. Every sheet carries a `?` that opens its handbook page in a side panel.

The seam to your real data is the **ETL hook** — a command you set in `config.yaml` under `app2.etl_hook` (it lives in the cfg, NOT the L2 YAML — a common mix-up). Deploy runs it as a subprocess to refill the base tables from your source system; the [ETL Hook reference](../handbook/etl-hook.md) covers the bulk-insert helpers and the dollars-vs-cents coercion footgun, and the [ETL walkthroughs](../walkthroughs/etl/how-do-i-populate-transactions.md) work an example end to end.

Two more ETL surfaces sit next to Deploy:

- **`/etl/run` (Refresh Data)** — re-run just the hook + matview refresh, with a live progress tail, when you don't need a full regenerate.
- **`/etl/triage`** — the Gap-finder: it diffs your L2 declaration against what the live table actually carries and surfaces the divergences (a posted rail with no declared `Rail`, a `(role, rail)` combo with no `LimitSchedule`, a template-required metadata key missing on tagged rows) as decision cards. Full catalogue in [L2 Triage Gaps](../L2_Triage_Gaps.md).
