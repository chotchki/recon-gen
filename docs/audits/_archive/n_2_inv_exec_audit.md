# N.2 — Investigation + Executives reshape audit

**Status.** Decided 2026-04-29 as part of Phase N.

**Decision.** Both apps are **reshaped onto L1/L2 primitives**, fed from the
same L2 YAML that already drives L1 Dashboard + L2 Flow Tracing. Neither
gets its own YAML.

---

## Architectural reframe — one L2 YAML drives N apps

Pre-N.2 framing: "the L2 YAML is the L2 spec for L1 Dashboard + L2FT."
Post-N.2 framing: **the L2 YAML describes the institution** — its
accounts, rails, transfer templates, chains, limit schedules, brand
theme, and (eventually) seed scenarios — and **the four apps each
read what they need from it**. There is one YAML per institution, not
one YAML per app.

This is a documentation + naming concern as much as a code concern.
SPEC.md and the `L2Instance` docstring still treat the YAML as a "set
of L2 primitives." The reframing for N.3 / N.4 / N.5 is:

- **Institutional model** = Accounts + AccountTemplates + Rails +
  TransferTemplates + Chains + LimitSchedules. (Today's primitives;
  unchanged.)
- **App configuration** = Theme (already added in N.1.b), and any
  future per-app knobs that need to flow per-instance.
- **Seed scenarios** (forward-looking, not in N.3 / N.4): planted
  demo data primitives. May warrant being a sibling YAML alongside
  the institution spec, since the spec describes "what's possible"
  while the scenarios describe "what's planted in the demo." See
  "Open issue: spec vs scenario YAML split" below.

**Concrete deliverable for N.3 / N.4 docs sweep:** rename the
top-level concept from "L2 instance" to **"institution YAML"** in
prose; keep the typed code identifier `L2Instance` since "the loaded
institution model" is still essentially L2-shaped.

---

## Investigation — RESHAPE

### What it is today

Five sheets:
- Getting Started (description-driven prose).
- Recipient Fanout (custom-SQL dataset joining inflow legs × outflow
  legs by `transfer_id`; ranks by COUNT_DISTINCT(sender_id)).
- Volume Anomalies (matview `inv_pair_rolling_anomalies` —
  rolling 2-day SUM per (sender, recipient) pair, z-score against
  population stats, 5-band bucket).
- Money Trail (matview `inv_money_trail_edges` — `WITH RECURSIVE`
  walk over `parent_transfer_id`, one row per multi-leg edge).
- Account Network (same matview as Money Trail, anchored on a
  selected account; inbound + outbound Sankey + walk-the-flow drill).

Reads only the shared `transactions` base table (no Investigation-
specific schema). Two matviews live in `schema.sql` with global names.

### What unique value does it provide?

Investigation answers a categorically different question from L1 +
L2FT. The L1 Dashboard answers "is the system internally consistent?"
(drift, overdraft, limit breach, stuck legs). L2FT answers "is the L2
declaration alive?" (Rails / Chains / TransferTemplates with no
runtime activity). Investigation answers **"are there suspicious
patterns I wouldn't catch from internal-consistency checks alone?"**
— recipient fanout (one account suddenly receiving from many),
volume anomalies (z-score'd pair-level activity), recursive money
trails (provenance walks over chained transfers), account network
graphs (inbound + outbound flow visualization).

L1 + L2FT structurally cannot replicate this. The questions key on
patterns over the ledger, not on consistency invariants of the
ledger. **Compliance / AML triage is the persona; the question shape
is "explore + investigate," not "monitor + drill."**

### Reshape sketch

- **L2 instance feeds the prefix.** Investigation reads from
  `<prefix>_inv_pair_rolling_anomalies` and
  `<prefix>_inv_money_trail_edges` instead of the global names. Same
  storage-isolation contract the L1 invariant matviews already follow.
- **Matviews migrate to `common/l2/schema.py`.** The two existing
  matview definitions in `schema.sql` move into `_emit_inv_views()`
  alongside the L1 invariant view emitters. They become per-instance
  prefixed automatically.
- **Theme already wired** (N.1.f-equivalent): once Inv reads from the
  L2 instance, `resolve_l2_theme(l2_instance)` replaces the
  `cfg.theme_preset` path; the `app_info(theme=None)` fallback gets
  removed (per the cleanup bullet on N.3).
- **No new L2 primitives needed.** Investigation's SQL only consumes
  `transactions` (base layer) and the two matviews (which are derived
  from `transactions`). No Rail / TransferTemplate / Chain inputs.
- **Per-app constants stay.** `apps/investigation/constants.py` keeps
  its `SHEET_INV_*` / `P_INV_*` URL-facing IDs; they're per-app, not
  per-institution.

### What does NOT change

- **Sheet shapes are preserved.** Recipient Fanout, Volume Anomalies,
  Money Trail, Account Network all keep their current visuals,
  drills, and parameters. The reshape is plumbing (prefix routing +
  theme source), not feature redesign.
- **Walk-the-flow drill stays as-is.** Account Network's left-click
  Sankey + right-click table → counterparty parameter overwrite is
  the unique-value drill; it works against the matview shape and
  doesn't need restructuring.

### Ranked open questions (carry into N.3)

1. **Sasquatch demo seed data** — `apps/investigation/demo_data.py`
   plants the Cascadia / Juniper / shells AML scenario. Under N.3,
   does that move to `common/l2/seed.py` as a generic
   "AML-fanout-plant" + "AML-anomaly-plant" + "AML-chain-plant"
   primitive set, or stay app-local? (Adjacent concern; see below.)
2. **L2 demo bundle freshness** — `tests/l2/sasquatch_pr.yaml` doesn't
   have an Investigation-flavored sub-tree (no Cascadia/Juniper
   accounts). Reshape will need either a richer `sasquatch_pr.yaml`
   OR a new institution YAML (`sasquatch_inv.yaml`?) carrying the
   Investigation persona.
3. **Validator looseness** — moot under "one YAML feeds N apps."
   Investigation reads what's in `transactions` regardless of which
   Rails the L2 declares; no validator change needed.

---

## Executives — RESHAPE

### What it is today

Four sheets:
- Getting Started.
- Account Coverage (KPI: active accounts; table: per-account activity).
- Transaction Volume Over Time (line: transfer count + gross dollars
  by date × transfer_type).
- Money Moved (bar: gross + net dollars by transfer_type).

Two custom-SQL datasets (`exec_transaction_summary`,
`exec_account_summary`) aggregating from `transactions` +
`daily_balances`. No matviews.

### What unique value does it provide?

Executives is **throughput-flavored** ("how many transfers landed
this month", "money moved per day", "which accounts are quiet"). L1 is
exception-flavored ("open exceptions today", drill into violations).
Different audience: board / exec scorecard versus operator triage.
Different cadence: month-over-month trends versus today-vs-yesterday.

L1 could in principle answer some of this with new datasets, but the
visual framing would be wrong — KPIs framed as exceptions don't read
as throughput; the L1 dashboard's filter defaults (rolling 7-day) are
operator-tuned, not exec-tuned. Cleaner to keep Executives as a
distinct surface.

### Reshape sketch

- **L2 instance feeds the prefix.** Datasets become
  `<prefix>-exec-transaction-summary-dataset` etc., reading from
  the prefixed `<prefix>_transactions` / `<prefix>_daily_balances`
  base tables.
- **Theme wired through `resolve_l2_theme`.** Same path as L1 / L2FT.
- **No new L2 primitives needed.** Same as Investigation — pure
  base-table consumer.
- **No matview migration.** Executives has none; both datasets are
  inline custom SQL.
- **Per-app constants stay.** `apps/executives/app.py` keeps its
  inlined `SHEET_EXEC_*` IDs (greenfield-app convention; no
  `constants.py` until URL stability forces one).

### What does NOT change

Sheet shapes preserved. Reshape is purely the L2-feeding plumbing.

### Ranked open questions (carry into N.4)

1. **Multi-instance executives** — under "one YAML per institution"
   the Executives dashboard summarizes one institution. If a future
   integrator wants a cross-institution exec view (rare in v6 scope),
   that's a separate feature. Out of scope for N.4.

---

## Open issue (forward-looking, not blocking N.3 / N.4)

### Spec vs scenario YAML split

Today the L2 YAML carries:
- The institutional model (Accounts, Rails, TransferTemplates, etc.).
- The brand theme (added in N.1).
- A `seed_hash` (hash-lock for the auto-generated demo seed SQL).
- `role_business_day_offsets` (per-role business-day timing —
  fuzzer-driven coverage).

Forward-looking: as we add **richer seed scenarios** (the Sasquatch
Investigation persona's Cascadia / Juniper / shells subgraph;
hand-tuned anomaly windows; planted fraud rings), the YAML grows two
distinct concerns:

1. **What the institution looks like** — durable, auditable,
   version-controlled by the integrator.
2. **What's planted in the demo** — synthetic, illustrative, may
   churn often as the team tunes the demo story.

A clean split would be:
- `<inst>.yaml` — the institutional spec (today's primitives + theme).
- `<inst>_seed.yaml` — the demo scenarios (what `quicksight-gen demo
  apply` plants).

The CLI loads both; the institution YAML is a hard requirement, the
seed YAML is optional (production deploys ship just the spec).

**Not part of N.3 / N.4.** This is a Phase O / backlog topic. Captured
here so the conversation has a referent when seed primitives start
demanding more shape than `common/l2/seed.py`'s current
`ScenarioPlant` provides.
