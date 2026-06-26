# DT.3 handbook fan-out — operator review queue

Uncertain staleness + judgment calls the fan-out surfaced, for chotchki's red-pen.
Clear-cut fixes are applied in the commits; THIS file is the "your call" list +
found bugs. Per-batch, appended as the autonomous run proceeds. Everything is
staged on `feature/dt-doc-voice` (main untouched).

## DT.3 followup — bug fixed + flags researched/resolved (2026-06-25 night)

Operator: "fix the bug and finish the research … v14 is out, everything is nested now config wise."

- **BUG FIXED** (`f71e05b7`): `recon-gen docs test` ran `pyright … main.py` (no `main.py` at repo root) → now `src/recon_gen/main.py`; `docs test: OK`.
- **v14 config nesting FIXED** (`f71e05b7`): `signing:` → `audit.signing:` in audit.md + install.md (`config.py:556` maps it). DT.3-docs flat-config sweep was otherwise clean.
- **Conceptual flags RESEARCHED + FIXED against the code** (5 pages):
  - etl-engineer: the 6 check_types are a SUBSET (7 more roll into L1 Exceptions) → reframed; "Stuck Pending/Unbundled" → the actual sheets "Pending Aging" / "Unbundled Aging".
  - l1: "UNION across all 5 invariants" was WRONG (the `l1_exceptions` matview UNIONs 12 check_types) → named the categories. ("5 views + 2 aging" at line 24 IS correct — registry has 7.)
  - l2_flow_tracing: "Balanced/Imbalanced" was stale → the real column is `completion_status` (Complete/Imbalanced/Orphaned); disambiguated filter-vs-column.
  - limit-schedule: "singleton caps name their own role" was FALSE (the matview joins on `parent_role`; a `parent_role=None` singleton can never breach) → corrected; noted inbound (AML) caps exist.
  - double-entry: added a concise note that Conservation is `Σ(legs)=expected_net` (nonzero for bundles), keeping the simple example.
- **NEW code-staleness (DT.6 / code fix):** `apps/l2_flow_tracing/app.py:184` `_TRANSFER_TEMPLATES_DESCRIPTION` still says "net status (Balanced / Imbalanced)" but the sheet column is `completion_status` — the rendered sheet description is stale.

## Live-demo cross-reference (recon-gen-sasquatch, 2026-06-25)

Curled the live Studio demo (renders from the deployed sasquatch fixture) to
resolve flags empirically. Routes: `/dashboards/<app>/sheets/<sheet-id>` (shell)
→ `/visuals/<id>/data` (HTMX data fragments) + the `/l2_shape/*` editor.

**RESOLVED (facts confirmed — the editorial call is still yours):**
- **Limit Breach is genuinely OUTBOUND — but inbound caps exist for AML.** The L1
  Limit Breach sheet (live title: "Outbound Transfer Limit Breaches") tracks
  outbound-debit-over-cap, so the open-vs-closed-loop gloss is ACCURATE for that
  sheet. AND `/l2_shape/limit_schedule/` declares **6 Inbound + 24 Outbound**
  schedules — the inbound ones enforce AML / structuring thresholds that surface in
  Investigation, not the L1 Limit Breach sheet. So: the concept page is fine on
  Limit Breach; `limit-schedule.md` could note inbound (AML) schedules exist.
- **etl-engineer's "6 check_types" is a subset.** The live L1 Exceptions sheet
  renders ~10+ kinds (drift, overdraft, pending, unbundled, limit-breach,
  supersession, **chain, xor, ledger, cadence**). Add "among others", or keep the
  six as the headline classes?
- **Program Health confirmed verbatim** — the live sheet reads "green when zero,
  amber on any violation, red at the 20-violation systemic mark" over a 30-day
  window. The exec page's tripwire description matches exactly. (The separate
  QS-bookmarks sub-claim is QS-specific, not in the HTMX demo — stays unverified.)

**Browser walk DONE.** Drove the live demo via Playwright **chromium** (this
sandbox can't download the webkit binary playwright 1.59 wants — cached chromium
works; `App2Driver.attached_to(base_url=…)` is the sanctioned driver path, used
here as raw chromium in a scratch script since screenshots don't need the verbs).
Captured **22 full-page screenshots** — all 4 dashboards, every content sheet —
reproducible via `scratchpad/demo_walk.py`. Curating + placing them on the
handbook pages is a DT.4 task (walkthroughs own `screenshots/`).

## for-your-role/ — commit `70e57847`

- **integrator.md** — the limit-schedule gloss says caps per `(parent_role,
  rail_name)`; the key was WIDENED to `(parent_role, rail, direction)` (AB.1,
  `primitives.py:692`). Add `direction`? (Left as-is — canonical owner is
  `concepts/l2/limit-schedule.md`; also the field is `rail`, not `rail_name`.)
- **etl-engineer.md** — lists 6 check_types (Drift / Overdraft / Limit Breach /
  Stuck Pending / Stuck Unbundled / Supersession); the code now carries ~14
  invariant kinds (`ledger_drift`, `expected_eod_balance_breach`,
  `balance_cadence_gap`, `chain_parent_disagreement`, `xor_group_violation`,
  `fan_in_disagreement`, `multi_xor_violation`). The six read as exhaustive but
  are a subset — add "among others" / link `L1_Invariants.md`? Also note "Stuck
  Pending / Unbundled" are the matview KIND names; the analyst-facing SHEETS are
  "Pending Aging" / "Unbundled Aging".
- **executive.md** — "the QS visuals support per-user saved views — bookmark the
  date ranges you use most" is unverified in the registered-user EMBED context,
  and it's a QS-specific claim (QS is on the deprecation path). Generalize or drop?

## concepts/ — commit `<next>`

Real staleness FIXED in-place (not listed here): aging-band cutoffs (5 fictional
bands → the actual distinct Pending/Unbundled sets), `rail_name` → `rail` field +
per-child (not pooled) cap on limit-schedule, several matview-kind → sheet-name
fixes, a "sasquatch" persona leak neutralized. The uncertain calls:

- **double-entry.md** — states L1 Conservation as "net to zero per transfer". The
  general invariant is `Σ(legs) = expected_net` (`primitives.py:426`) — zero for
  classic two-leg transfers, NONZERO for template-materialized bundles, unset for
  single-leg. "Net to zero" is a fine simplification for a background concept page;
  surface the `expected_net` nuance here, or leave it to Schema_v6 / SPEC?
- **open-vs-closed-loop.md** — frames Limit Breach as gating outbound flow toward
  external counterparties. The real detector (`common/spine/limit_breach.py`) is a
  per-rail per-DIRECTION cap that also supports Inbound (AML / structuring) and can
  sit on an internal-only rail. Mention inbound / internal caps in the conceptual
  framing, or keep the simplification?
- **limit-schedule.md** — "Direct (singleton) caps name the singleton's own role":
  couldn't confirm the singleton-self-role convention in code (matview requires
  `account_parent_role IS NOT NULL`). Worth a glance.
- **chain.md** — two headers still carry phase tags: `## Template-as-chain-child
  (AB.2)` and `## Fan-in chains (AB.4): ...`. They're ANCHOR TARGETS —
  `how-do-i-chain-two-templates.md` and `how-do-i-model-batched-payouts.md` link to
  `#template-as-chain-child-ab2` / `#fan-in-chains-ab4-...`. Stripping the tags
  renames the anchors and breaks the link sweep, so they were left as-is. SAME
  class: `rail.md` headers `(AB.5)` / `(AF)` and `transfer-template.md` `(AB.3)` —
  rewriters stripped those, broke the link sweep, and I RESTORED them to land the
  batch green. Fully de-tagging all 5 needs a coordinated header + walkthrough-link
  rename across `concepts/` + `walkthroughs/` (your call — a clean follow-up).

## handbook/ — commit `8c6d34a0`

- **l1.md** — says "5 always-present L1 invariant views", but the card grid shows 4
  non-aging (Drift, Overdraft, Limit Breach, Expected EOD Balance) + 2 aging, and
  the registry rolls several more kinds (balance_cadence_gap, chain/xor/fan-in)
  into L1 Exceptions. Confirm the "5" still matches the registry, or reword.
- **l2_flow_tracing.md** — the Transfer Templates card says "Balanced / Imbalanced"
  (that's the net-status FILTER, `app.py:184`), but the completion_status COLUMN
  projects Complete / Imbalanced / Orphaned (`datasets.py:649`). Disambiguate the
  filter-vs-column? Also "five sheets" omits the Info canary (deployed app has 6).

## reference/ — commit `292d0b7a`

- **install.md + handbook/audit.md** — both document a top-level `signing:` block,
  but `config.py:556` migration maps `signing` → `audit.signing` (v14 nested cfg).
  If the v14 nested shape is live, both docs need the `audit: signing:` form — a
  coordinated two-file update (left consistent with each other for now).
- **quicksight-quirks.md** — entry 3.2 (a sheet named "i" gets hidden by QS) notes
  "verified against `us-east-2`". Region / QS-version specific; may have changed.

## DT.4 walkthroughs — commits `9959cae8` / `ea9cb1bd` / `dfe61024`

Heavy staleness FIXED in-place (config recipes were v13-flat throughout → renested
to v14; `signed_amount`→`amount_money`; `11→12` columns; `emit_schema(prefix=)`;
"Four"→"Five" artifact groups; index↔nav resync added 9 pages). Uncertain calls:

- **Orphan page** — `customization/how-do-i-brand-my-handbook-prose.md` is on disk
  but NOT in `mkdocs.yml` nav (and was absent from the walkthroughs index). Either
  the nav is missing it or the file is dead. Your call (I rewrote it in place either way).
- **CLAUDE.md domain-model is stale** (out of scope, flagging) — its `<prefix>_transactions`
  section still uses `signed_amount`; v6 renamed it to `amount_money` + `amount_direction`.
- **`run-my-first-deploy` sample-output blocks** show drifted per-app dataset counts
  (~27 total; real is ~50). Left verbatim (inside code fences). Refresh the sample
  stream, or leave it illustrative?
- **`populate-daily-balances`** says "8 mandatory columns" but `entry` is auto (a
  reader writes 7). Minor — say 7?
- **`populate-transactions`** has a surviving dangling cite to a Schema_v6 "Example
  1" that no longer exists (pre-existing, outside the diff; re-pointed the main ref).

## Found bugs (not doc content — codebase staleness)

- **`recon-gen docs test`** runs `pyright src/recon_gen/common/handbook/ main.py`,
  but there is no `main.py` at repo root → the verb errors on a stale path. The
  pytest doc-gates (`tests/docs/`, 64) pass; only the pyright sub-step fails.
  Likely meant `src/recon_gen/docs/_macros/main.py`. One-line fix in the docs CLI.
