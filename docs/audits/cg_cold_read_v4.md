# CG cold-read v4 — 2026-06-05

## Scope

Cold-read of the L2 Editor at http://127.0.0.1:8765 against the live
Studio process (DuckDB + sasquatch_pr fixture, `qsgen-duckdb`
deployment). Repo at `56ca53bd` (tip of `ca-0-duckdb-spike`); CG.11
through CG.15 land on disk at that SHA. Surfaces crawled: `/`,
`/l2_shape/{account,account_template,rail,transfer_template,chain,limit_schedule}/`
+ each kind's `?embed=1`, search (`?q=ach` / `?q=zzzzz`), pager
(`?page_offset=25` / `=999` / `=-5`), single-entity reads + body-only
fragments, `…/new`, `…/edit`, `/diagram`, `/etl/`, `/training/`,
`/l2_shape/{theme,instance,persona}/`, glossary side-panel fragment.

## Methodology

Reviewer is a senior product designer; persona is the CPA-trained
reconciliation specialist who reads accounts by Fed-statement name,
not GL kebab, and may be either first-session or returning. Findings
derived from raw HTML over `curl` — structure, copy, affordances,
HTMX wire targets. No browser, no JS execution; behavioral
hypotheses flagged where stated. Did not cover: live form-submission
validators, the htmx delete round-trip, the training trainer apply
loop, or the dashboards under `/dashboards/`.

## What CG.11–15 got right (and what didn't reach the live process)

The intent behind CG.11–15 is sound: name-next-to-kebab on account
titles (CG.11), drop the unreadable composite from chain titles
(CG.12), strip underscored `EntityKind` strings from a11y / tooltip
surfaces (CG.13), tighten the home-section open-on-load contract
(CG.14), and rewrite the home intro from "saves cascade" reassurance
to first-visit guidance (CG.15). On disk all five are present at
`56ca53bd`. **In the live Studio process I crawled, CG.11 / CG.12 /
CG.13 / CG.15 are not yet visible** — the running `recon-gen studio`
process (pid 42571, started ~7:23pm) is serving pre-CG.11 code that
hasn't been reloaded since the commits landed. CG.14 (account-section
open by default) IS effective live, but only because the prior code
already opened account by default; CG.14 is a docs-only lock, not a
behavior change. This is the single most important finding in this
audit. See P0 below.

Out of the genuinely-shipped surface work in the CG.* batch — CG.5
(uniform chevron + lazy-load across all six card kinds), CG.6
(trainer-style page header on dedicated kind pages), CG.7 (shared
top-nav on new / edit / structured-form pages), CG.8 (in-place
empty-state with Clear-search button), CG.9 (chevron on section
summary), and CG.10 (diagram-page header exemption documented) — all
six ARE visible live and they're solid. The empty-search response in
particular ("No rails match `zzzzz`. Clear search or check spelling.
[Clear search]") is exactly the right shape for a search-first
surface, and it nails the CF.4 cold-read v3 P1 cleanly. The shared
top-nav on `/l2_shape/account/new` and `/l2_shape/account/<id>/edit`
restores orientation across the editor flow. CG.5's chevron-plus-
lazy-load is now uniform across all six list kinds — no more
asymmetric "rail collapses but chain unrolls" behavior. The
embed-mode pager target (`#home-section-body-rail`) and the
duplicate `embed=1` query parameter from CF.4 cold-read v3 P0 are
both fixed cleanly.

## Findings

### Severity P0 (blocking)

- **Live Studio process is serving pre-CG.11 code; four of the five
  CG.* batch items don't reach the operator.** Process started
  before CG.11/12/13/15 commits landed; no hot-reload. The home page
  blurb still says *"…the per-section affordance below; saves
  cascade across sections automatically"* (the old CF.4-vintage
  copy) instead of the CG.15 *"Each section below is a kind of
  building block in this institution's L2 shape…"*. Account card
  titles render `<h3>gl-1010-cash-due-frb</h3>` with no `Cash & Due
  From Federal Reserve` next to it. Chain titles still render
  `MerchantSettlementCycle::MerchantPayoutACH,MerchantPayoutCheck,
  MerchantPayoutWire,MerchantWeeklyPayoutBatch` (CG.12 absent).
  Section header `aria-label="Search account_templates"` and `title=
  "Create a new transfer_template"` still leak underscores (CG.13
  absent). The fix is operational, not code: restart the process
  (`kill 42571 && .venv/bin/recon-gen studio -c run/config.duckdb.yaml
  --l2 run/sasquatch_pr.yaml --port 8765`). This finding sits at the
  top because for a returning operator dogfooding right now, the
  intent of the whole CG.11-15 batch is invisible. Consider whether
  Studio should refuse to start when its code is older than `git
  log -1 --format=%H` on disk, or auto-reload on src/ changes — at
  minimum surface a banner.

- **Pager renders nonsensical range when `page_offset` exceeds
  `total`.** `/l2_shape/account/?embed=1&page_offset=999` returns
  `Showing 1000–16 of 16` with an empty card grid. Operator who
  navigates away and comes back via a stale URL (or who edits the
  URL bar) sees mathematically impossible copy. Server should clamp
  `page_offset` to `total - page_size` (or to 0 if total is empty),
  not return the offset back unchanged. Negative offsets ARE clamped
  to 0 correctly — symmetric handling is missing only on the high
  end.

- **Chain card `id` attribute contains literal commas, making the
  `#entity-chain-X,Y,Z` HTMX target a malformed CSS selector.**
  `id="entity-chain-MerchantSettlementCycle__MerchantPayoutACH,
  MerchantPayoutCheck,MerchantPayoutWire,MerchantWeeklyPayoutBatch"`
  with matching `hx-target="#entity-chain-..."`. `querySelector`
  parses `#a,#b` as a selector list — the comma is interpreted as
  the start of a sibling selector, not as a literal id character.
  Delete on a multi-child chain card will either fail silently or
  swap the wrong DOM node. URL-side already uses `__` to substitute
  `::`; extend the substitution to `,` (e.g. `_C_` or `-`) so the id
  is CSS-safe. The two-child chain (`ACHOriginationDailySweep__
  ConcentrationToFRBSweep`) is selector-safe; the comma-bearing four
  chain cards are the ones at risk. Hypothesis only — verify in
  browser. If confirmed, this is a "deleted the wrong card" data-
  loss footgun.

### Severity P1 (worth fixing this CG cycle)

- **CG.12 fixed chain titles but `limit_schedule` has the same
  unreadable composite-key shape and was not touched.** The
  dedicated limit-schedule page renders `<h3>DDAControl::
  CustomerOutboundACH::Outbound</h3>` — three-segment `Role::Rail::
  Direction` composite. Same readability problem CG.12 just solved
  for chain. Pattern: title slot gets the leading segment (the
  account `role` — `DDAControl`), the body dl already shows the
  Rail + Direction as `<dt>` rows. One-line patch in the same
  helper. With 7 limit_schedules today this isn't urgent at
  customer scale, but the institution's actual limit count scales
  with the account_template population, so the at-scale shape will
  hit fast.

- **The new form's required-field markers don't match its inline
  reference text.** `/l2_shape/account/new` Reference panel reads:
  *"Required: `id`. Strongly recommended: `role` (without it the
  account isn't reachable by any rail) and `name` (what shows up in
  dashboards)."* The form marks both `ID *` and `Scope *` as
  required (`<span class="text-danger"> *</span>`) — but `scope`
  isn't called out in the reference text, and `role` / `name` (the
  two flagged as "strongly recommended") have no visual signal at
  all. Pick one: either lift `scope` and the two strongly-
  recommendeds into a "Recommended" group with a softer marker
  (e.g. greyed `(recommended)` instead of red `*`), or reconcile
  the reference text to match the form. Right now the form contract
  and the prose contract disagree.

- **`/l2_shape/persona/` returns a 404 page with no top-nav.** The
  home page no longer lists Persona (good, CF.4 v3 P0 cleaned up),
  but `/l2_shape/persona/` still resolves to `<h1>404</h1><p>persona
  is not an editable entity kind (yet).</p>` — bare, no chrome, no
  way back. Operator with a bookmark, browser history entry, or an
  inbound link from an old runbook lands here and is stranded. Same
  template as the home page (top-nav + an h1 + the same message)
  would cost nothing and remove the dead-end.

- **Edit-form page header has no contextual back-link or display
  name.** `Edit account: gl-1010-cash-due-frb` — the kebab is in the
  h1 but the display name (`Cash & Due From Federal Reserve`) isn't,
  and there's no "← back to Accounts" link. The top-nav L2 Editor
  link bounces to home; from there the operator has to re-open the
  Accounts section and scroll. Append the display name to the h1
  (`Edit account: gl-1010-cash-due-frb — Cash & Due From Federal
  Reserve`) and add a small back-link below it pointing to
  `/l2_shape/account/`.

- **Edit form has no Delete affordance.** Save and Cancel only. To
  delete an account the operator has to: navigate back to the list,
  find the card, hit the Delete button in the card header. For an
  edit-then-realize-this-is-wrong-actually flow that's three extra
  steps. A grey Delete button on the right of the form, with the
  same `hx-confirm` text the card uses, would close the loop.

- **Page-title `<title>` shapes are still inconsistent across
  surfaces** (CF.4 cold-read v3 P2 noted this; CG.* didn't sweep
  it). Audit:
  - `/` → `Studio — qsgen-duckdb`
  - `/l2_shape/account/` → `Studio editor — account`
  - `/l2_shape/account/gl-1010-cash-due-frb` → *(empty `<title>`)*
  - `/l2_shape/account/.../edit` → `Edit account: ... — Studio`
  - `/l2_shape/account/new` → `Create new account — Studio`
  - `/diagram` → `Studio diagram — qsgen-duckdb`
  - `/etl/` → `Studio · ETL Support — qsgen-duckdb`
  - `/training/` → `Studio · Training · Dual-prefix v`
  - `/l2_shape/theme/` → `Theme — Studio`

  Em-dash vs middle-dot, deployment-name sometimes included, Studio
  sometimes a prefix and sometimes a suffix, and one surface has
  literally no title at all. Pick one shape — suggest
  `Recon-Gen · Studio · <surface>` as the canonical prefix and
  bolt deployment-name only into the home title. Single-card view
  needs ANY title (`Recon-Gen · Studio · Account — gl-1010-cash-due-
  frb`).

- **The new-account form's `parent_role` `<select>` shows 15 raw
  role enums sorted alphabetically with no grouping or guidance.**
  ACHOrigSettlement / CardAcquiringSettlement / CashDueFRB /
  ConcentrationMaster / CustomerDDA / DDAControl / … / WireSettlement
  Suspense / ZBASubAccount. A first-time operator has no idea which
  roles ARE parent-eligible (only roles that are themselves
  singleton-parents) versus which are leaves; the field's small
  text *"When this is a subledger account, names its singleton
  parent's Role"* assumes the operator already knows what's a
  singleton-parent. Either filter the dropdown to only
  parent-eligible roles, or add a `<optgroup>` split — singletons on
  top, the rest greyed-out (or hidden behind a "Show all" toggle).

### Severity P2 (nice to have)

- **Card section count format is mixed.** `Accounts (16)`,
  `Theme (set)`, `Instance settings (set)`. Persona dropped from the
  list so the `(not set)` shape is gone, but the singleton-vs-
  cardinal split is still in the UI. Minor — consistent now, would
  break if a new singleton ever showed `(empty)`.

- **Pager Prev/Next disabled-state opacity is barely visible.**
  Disabled state is `opacity-50` on a `bg-link-tint` background
  (already a pale green) — operator has to actually try the button
  to know it's disabled. Bump to 30% opacity or swap to neutral
  grey background.

- **Side-panel `[?]` button tooltip `title="Glossary (?)"`** still
  reads as "glossary is unclear" rather than "press ? to open
  glossary". Use `title="Help & glossary — press ?"` or drop the
  parenthetical. (CF.4 cold-read v3 P2; survived this cycle.)

- **Chain URL keeps raw `::` and `,` in the path.**
  `/l2_shape/chain/MerchantSettlementCycle::MerchantPayoutACH,
  MerchantPayoutCheck,MerchantPayoutWire,MerchantWeeklyPayoutBatch`
  works (HTTP 200) but renders as an awkward, copy-paste-hostile
  URL in the operator's address bar. The CSS-id situation (P0
  above) needs the substitution anyway; using the same `__` and `_C_`
  in the URL path would clean both up.

- **The home page intro text — once CG.15 actually deploys — is
  pleasant prose but the load contract still puts ALL focus on
  Accounts (the only open section).** CG.15 calls out *"Expand one
  to browse its entries"* — implying the operator's first move is
  to expand something. But Accounts is already open. The copy and
  the default state disagree slightly. Either say "Accounts is open
  by default — expand the rest to browse," or close all sections
  by default. (Cold-read v3 flagged the same hierarchy question.
  CG.14 documented the lock but didn't reconsider it.)

- **The training page uses a different visual idiom for its top
  buttons** (`▶ Session Start (re-fetch)`, `↻ Force rebuild from
  base`, `🗑 Cleanup`) — Unicode glyphs as button prefixes — whereas
  the L2 editor uses `+ Add` / `↗` / `▸` for the same scale of
  affordance. Not wrong, but the visual idiom shifts between tabs.
  Either bring `▶` / `↻` / `🗑` into the editor's chevron / arrow
  family, or rationalize Training's button vocabulary to the
  editor's.

## Overall read

Most of the surface is in solid shape — CG.5 (uniform chevron +
lazy), CG.6 (kind-page headers), CG.7 (shared nav on forms), CG.8
(in-place empty-state), and the CF.4 v3 P0s (embed pager target,
duplicate query param, persona section in home) all reached the
operator and read clean. The blocker is operational: the live
Studio process is serving pre-CG.11 code, so the four most recent
copy + readability fixes are silently invisible. Once the process
restarts to pick up `56ca53bd`, the audit's remaining P0s narrow
to two: the pager overflow-range bug, and the chain `id`-with-
commas selector-safety smell (which needs a browser test to
confirm whether HTMX swallows it). The surface is ready for a
returning-operator dogfood as soon as the process restart happens
— for a first-session operator, the pager-clamp + parent_role
dropdown grouping are the next pieces of friction to land.
