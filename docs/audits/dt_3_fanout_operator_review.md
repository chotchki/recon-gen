# DT.3 handbook fan-out — operator review queue

Uncertain staleness + judgment calls the fan-out surfaced, for chotchki's red-pen.
Clear-cut fixes are applied in the commits; THIS file is the "your call" list +
found bugs. Per-batch, appended as the autonomous run proceeds. Everything is
staged on `feature/dt-doc-voice` (main untouched).

## Live-demo cross-reference (recon-gen-sasquatch, 2026-06-25)

Curled the live Studio demo (renders from the deployed sasquatch fixture) to
resolve flags empirically. Routes: `/dashboards/<app>/sheets/<sheet-id>` (shell)
→ `/visuals/<id>/data` (HTMX data fragments) + the `/l2_shape/*` editor.

**RESOLVED (facts confirmed — the editorial call is still yours):**
- **Inbound caps ARE real.** `/l2_shape/limit_schedule/` declares **6 Inbound + 24
  Outbound** schedules. So the open-vs-closed-loop / limit-schedule gloss ("a daily
  outbound-flow cap toward external counterparties") is genuinely too narrow —
  inbound (AML / structuring) caps exist and are used. Broaden the concept page, or
  keep the simplification?
- **etl-engineer's "6 check_types" is a subset.** The live L1 Exceptions sheet
  renders ~10+ kinds (drift, overdraft, pending, unbundled, limit-breach,
  supersession, **chain, xor, ledger, cadence**). Add "among others", or keep the
  six as the headline classes?
- **Program Health renders as described** — the sheet shows green / amber / red +
  "healthy" / "violation" / "systemic". The page's tripwire description is accurate.
  (The separate QS-bookmarks / saved-views sub-claim is QS-specific and not in the
  HTMX demo — stays unverified.)

**Follow-up (needs a browser — flagged, not done):** confirming rendered KPI
values + the inbound-breach row + pulling handbook screenshots needs Playwright
(`uv sync --extra dev`, then an `App2Driver` session against the live URL). Route
map above makes it a quick focused task — best paired with DT.4 (walkthroughs own
the `screenshots/` dir).

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

## Found bugs (not doc content — codebase staleness)

- **`recon-gen docs test`** runs `pyright src/recon_gen/common/handbook/ main.py`,
  but there is no `main.py` at repo root → the verb errors on a stale path. The
  pytest doc-gates (`tests/docs/`, 64) pass; only the pyright sub-step fails.
  Likely meant `src/recon_gen/docs/_macros/main.py`. One-line fix in the docs CLI.
