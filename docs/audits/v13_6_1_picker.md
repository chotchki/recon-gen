# recon-gen — upstream feedback: Daily Statement account picker silently omits accounts at scale

Companion to **`phase3_review_v13_5_0_UPSTREAM.md`** (sparse-feed findings) and
**`phase3_review_v13_5_0_effective_balances_UPSTREAM.md`** (the carry-day blocker, fixed in
v13.6.1). Same surface family as the sparse findings: the **Daily Statement** assumes a modest,
dense, fully-named account universe, and degrades quietly when a real feed violates that.

Severity: **MEDIUM–HIGH.** The Daily Statement is a headline CL surface, and the failure is
*silent* — the account simply isn't offered, with no "list truncated" / "no match" signal. The
same root cause manifests on **both render targets** (local HTML app and QuickSight), so neither
renderer is a workaround for the other.

Observed on the local HTML app first (operator could not pick / search singleton "control"
accounts — pools, GL-control, sweep, interest — on the Daily Statement, but the statement rendered
fine when *arrived at via a cross-sheet drill*). Tracing it showed the cause is in the shared
picker **source**, not the renderer, so it reproduces on QuickSight too.

---

## Root cause (shared by both renderers)

The Daily Statement Account dropdown's options come from the `DS_L1_DS_ACCOUNTS` companion
(`build_l1_ds_accounts_dataset`, `apps/l1_dashboard/datasets.py`):

```sql
SELECT DISTINCT account_id, account_role, account_name,
       (account_name || ' (' || account_id || ')') AS account_display
FROM <prefix>_current_daily_balances
WHERE ('__l1_all__' = <<$pL1DsRole>> OR account_role = <<$pL1DsRole>>)
```

Two structural properties of this source drive the omissions:

1. **It is a parameterized dataset** (declares `pL1DsRole` for the Role→Account cascade).
2. **`account_display` is a bare concat** `account_name || ' (' || account_id || ')'`, which is
   **NULL whenever `account_name` is NULL** (SQL NULL-propagation, all three dialects).

The picker is *correctly* balance-sourced (every option is guaranteed a balance row — the BO.1
intent). The problem is what happens at the edges: large universes and unnamed accounts.

The accounts that disproportionately fall into those edges are exactly the **singleton "control"
accounts** (concentration pools, GL controls, sweep, interest): low cardinality, frequently
**carry no friendly `account_name`** in a real feed, and — being balance-bearing but
**transaction-light** — they're the ones an operator can *only* reach through this picker (the
operational sheets that offer cross-sheet drills into the Daily Statement are transaction/exception
sheets, which don't surface a row for a quiet control account). So the one path to them is the one
path that breaks.

---

## Leg A — local HTML app (the "App2" render target)

The option list is materialized server-side once per sheet load by the options fetcher
(`common/html/_tree_fetcher.py`, `make_options_fetcher`):

```python
_OPTIONS_CAP = 2000
...
options_sql = (
    f"SELECT DISTINCT {col_ref} AS opt FROM ({base_sql}) opt_src "
    f"WHERE {col_ref} IS NOT NULL ORDER BY 1{limit_clause}"   # LIMIT 2000
)
```

Three independent omission mechanisms, all silent:

- **Hard `LIMIT 2000` cap, ordered by `account_display`.** A feed with > 2000 balance-bearing
  accounts shows only the first 2000 alphabetically; everything in the tail is dropped from the
  `<select>`. The fetcher comment already names this a known gap: *"typeahead / server-side search
  for very large universes is a follow-on."*
- **Search is client-side only.** The dropdown is enhanced with Tom Select, which filters the
  already-materialized `<option>` set in the browser — there is **no server round-trip on
  type-ahead**. So an account dropped by the cap is not merely below the fold; it is *unreachable*,
  because searching for it never re-queries the DB.
- **`WHERE account_display IS NOT NULL` drops unnamed accounts.** Any account whose `account_name`
  is NULL has a NULL `account_display` and is filtered out entirely — independent of the cap.

"Works if I arrive from another page" is the tell: a cross-sheet drill writes the account
parameter (`pL1DsAccount`) **directly**, bypassing the dropdown, so the statement renders for an
account the picker would never have offered.

## Leg B — QuickSight

Same `DS_L1_DS_ACCOUNTS` source ⇒ the NULL-`account_display` omission is identical. The
large-universe behavior differs in mechanism but lands on the same user-visible failure:

- **Parameterized source breaks incremental search.** Because the LinkedValues source is a
  parameterized dataset (`pL1DsRole`), QuickSight's `GetUniqueAttributeValuesSyncForAnalysis`
  endpoint — the one that backs type-ahead in the option control — returns **400 before any SQL
  runs**. This is already documented in your own
  `docs/reference/quicksight-quirks.md` §"`GetUniqueAttributeValuesSyncForAnalysis` 400s on
  parameterized datasets". So for any universe large enough that the target account isn't in the
  initially-materialized option page, server-side search can't reach it.
- **Option-count DOM/ceiling split.** Per the same quirks doc, QS renders the control with
  different DOM (and option ceilings) above a count threshold (the `MuiAutocomplete` variant). A
  fleet-scale account list lands in the regime where the un-searchable, capped listbox is exactly
  what the operator faces.

Net: on QuickSight the *search box is broken outright* for this control rather than client-capped,
but the outcome — a balance-bearing control account that cannot be selected from the Daily
Statement picker — is the same.

---

## Reproduce on the bundled fixture (`spec_example`)

**NULL-name omission (both renderers), deterministic:**
```sql
-- pick any account that currently appears in the Daily Statement picker, null its name:
UPDATE <prefix>_daily_balances
   SET account_name = NULL
 WHERE account_id = '<some-account-with-balances>';
-- refresh, reopen the Daily Statement Account dropdown:
--   the account is gone from the option list (account_display collapsed to NULL),
--   yet a cross-sheet drill to that account still renders its statement.
```

**Cap + client-only-search (local app), structural:** with `_OPTIONS_CAP = 2000` and no
server-side typeahead, any deployment whose balance feed exceeds 2000 distinct accounts cannot
reach the tail of the picker by searching. (Verifiable by generating > 2000 accounts, or by
inspection of the fetcher SQL + the client-side Tom Select wiring.)

Self-check for any instance:
```sql
SELECT count(*)                                            AS null_display_accounts
FROM <prefix>_current_daily_balances
WHERE (account_name || ' (' || account_id || ')') IS NULL; -- > 0 ⇒ Leg-A/B NULL omission bites
SELECT count(DISTINCT account_id) AS distinct_accounts
FROM <prefix>_current_daily_balances;                      -- > 2000 ⇒ local-app cap bites
```

---

## Scope — this is the shared options path, not one picker

The cap + client-only-search live in the **shared** options fetcher (`make_options_fetcher`), and
the NULL-display drop comes from the **shared** `name || ' (' || id || ')'` label convention. So
the three failure modes recur across nearly every dataset-sourced (`LinkedValues`) picker. Audited
all four apps:

| Picker (sheet) | Source dataset | Bound column | Cap (≫2000?) | NULL-display | QS-400 (param source) |
|---|---|---|---|---|---|
| Account ×7 — Drift, Overdraft, Limit Breach, Pending, Unbundled, L1 Exceptions, Transactions | `DS_L1_ACCOUNTS` | `account_display` | yes (account-scale) | **yes** | no (de-param'd in BR.x) |
| **Transfer** (Transactions) | `DS_L1_TX_IDS` | `transfer_id` | **yes — guaranteed** (transfer-scale ≫ accounts) | no (raw id) | no |
| Daily Statement Account | `DS_L1_DS_ACCOUNTS` | `account_display` | yes | **yes** | **yes** (`pL1DsRole`) |
| Daily Statement Role | `DS_L1_DS_ROLES` | `account_role` | no (tiny) | no | no |
| Status / Origin (Transactions) | `DS_L1_TX_FACETS` | open-set | unlikely (small) | no | — |
| Chain root transfer (Money Trail) | `DS_INV_MONEY_TRAIL_ROOTS` | `root_transfer_id` | yes (chain-scale) | no | no |
| Anchor account (Account Network) | `DS_INV_ANETWORK_ACCOUNTS` | `source_display` | possible (network-scale) | **yes** | no |
| L2 Flow Tracing — Template / Completion / etc. | — | — `StaticValues` — | **no** | no | no |
| Enum dropdowns everywhere — Role / Transfer Type / Rail / Check Type / Supersedes | — | — `StaticValues` — | **no** | no | no |

**Caveat on the `StaticValues` rows:** "no" means immune to *these three* modes (2000-cap /
NULL-display / QS-400) — **not** "unbounded." `StaticValues` carries its own separate QuickSight
ceiling (see fixes §3); it's a *third* silent limit, not a safe harbor.

Three conclusions:

1. **The NULL-display omission is dashboard-wide for account pickers** — all 7 wide L1 account
   pickers, the Daily Statement account picker, and the Investigation Anchor-account picker bind a
   NULL-able `*_display` concat. A single NULL-safe label change at the shared label sites fixes
   every one at once.
2. **The cap's clearest victim is the Transfer picker, not the account picker.** `transfer_id` over
   the full ledger essentially *always* exceeds 2000 on a real feed (transfers ≫ accounts), so that
   picker — and the Investigation "Chain root transfer" picker — are already truncated-and-
   unsearchable at production scale today, independent of account count.
3. **The QS-400 search-break is unique to the Daily Statement Account picker** — it's the only
   picker whose `LinkedValues` source kept a parameterized cascade (`pL1DsRole`). The others were
   de-parameterized in BR.x precisely to dodge that endpoint, which is why their QS search still
   works. So Leg B is Daily-Statement-only; Leg-A (cap) and the NULL-display leg generalize.

**Why "just bake it static" isn't the answer:** L2 Flow Tracing and every enum dropdown use
`StaticValues` and dodge all three modes above — but *only because those sets are tiny today*.
`StaticValues` has its **own** QuickSight ceiling (a deployer reports the practical limit at ~a
couple dozen values; AWS doesn't prominently document an exact number, and recon-gen emits the list
**unguarded** — `StaticValues.emit()` is just `{"Values": [...]}` with no length check). So there
is no baked path that scales: `LinkedValues` ceilings at 2000 (with broken search past it),
`StaticValues` ceilings far lower. The fix is to pick the option source *by cardinality* (fixes §3),
not to move everything into one bucket.

---

## Suggested fixes (ranked; (1)+(2) together close both legs)

1. **NULL-safe display labels (dashboard-wide).** `COALESCE(account_name, account_id) || ' (' ||
   account_id || ')'` (or fall back to the id alone when the name is NULL), applied at the shared
   `*_display` label sites — both `account_display` builders, `_account_display_clause`, and the
   Investigation `source_display`/`target_display`. One convention change fixes the unnamed-account
   omission across *all* account pickers on *both* renderers. Highest-value, lowest-risk — unnamed
   control accounts are the reported symptom.
2. **Daily Statement: drop the Role cascade, add a control-account reference list
   (operator-recommended).** The Role picker exists *only* to narrow the Account picker — but the
   cascade is the very thing that parameterizes `DS_L1_DS_ACCOUNTS` and trips QuickSight's
   unique-values 400, and on QuickSight the narrowing never actually worked (the divergence note: QS
   shows the full universe regardless of Role). So **removing the Role picker de-parameterizes the
   source, which restores QuickSight's native server-side type-ahead** — strictly *more* findable,
   not less. Pair it with a small **reference list of the singleton / control accounts** (a text box
   or compact table pinned at the sheet bottom, listing their `account_display` strings) so an
   operator can read off a specific 1:1 account and search it directly. This closes Leg B *and*
   gives the reported-symptom accounts a one-glance path; it's the recommended concrete redesign for
   the sheet.
3. **Right-size every other picker by cardinality — there is no single safe default.** Both baked
   paths have a ceiling (`LinkedValues` → 2000 + client-only search; `StaticValues` → its own much
   lower, unguarded QS ceiling), so choose the option source by how big the set can get:
   - **≤ a couple dozen, fixed at build** → `StaticValues` (today's enum dropdowns — fine as-is).
   - **bounded / declared but larger** (template names, rails, roles in a big deployment) →
     `LinkedValues` off a *cheap declared-universe source* — the `<prefix>_config_kv` projection (or
     a dedicated view), **not** a `DISTINCT` over the full matview and **not** static. Bounded by
     declarations ⇒ under the 2000 cap, client-side search is fine, and keep it unparameterized so
     QS search works.
   - **genuinely unbounded** (accounts, transfers at fleet scale) → **server-side type-ahead** keyed
     on the typed prefix (the fetcher's own flagged follow-on). The **Transfer picker needs this
     today.**
   And **guard the ceilings**: validate `StaticValues` length and `count(distinct)` vs
   `_OPTIONS_CAP` at build time — fail/warn loudly rather than silently truncating.
4. **Never truncate silently.** If a cap remains, render a visible "showing first N of M — refine
   by Role or type to search" affordance instead of dropping the tail with no signal. Silent
   omission on a reconciliation surface reads as "this account has no data," which is the opposite
   of the truth.

Fixes (1), (2), and (4) are each independently shippable and each removes a real failure mode on
its own. (2) is the operator-recommended redesign for the Daily Statement specifically; (1) and the
ceiling-guard in (3) are the dashboard-wide hardening.
