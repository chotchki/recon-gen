# CG cold-read v5 — 2026-06-05

Commit: `bee60664` (`CG.22 — parent_role dropdown grouped by eligibility`).

## Methodology

Cold-read against the live Studio process at `http://127.0.0.1:8765/`
(DuckDB + `sasquatch_pr` fixture, `qsgen-duckdb` deployment), driven
entirely over `curl -s` — rendered HTML is the source of truth. Same
shape as v4: senior-PD lens, CPA-reconciliation persona, no JS
execution, no form submits, behavioral hypotheses flagged where
stated. Studio was restarted fresh against `bee60664`, so what's
served IS what the code says — the v4 P0 "process serving stale code"
class is structurally gone. Surfaces crawled: `/`,
`/l2_shape/{account,account_template,rail,transfer_template,chain,limit_schedule,theme,persona}/`
+ a representative entity per kind (read card + edit form),
`/l2_shape/account/new` (form), pager edges (`?page_offset=999` on
both a 16-row and a 30-row list, `?page_offset=-5`, `?page_offset=80`),
empty-search (`?q=zzzzz` on rail), `/diagram`, `/etl/`, `/etl/probe`,
`/etl/run`, `/etl/triage`, `/training/`. Did not cover: form-submission
validators, the `hx-delete` round-trip behavior, training trainer
apply loop, dashboards under `/dashboards/`.

## CG.16–22 verification

- **CG.16 — chain id / hx-target are comma-free**. Lands. The
  four comma-bearing multi-child chains now substitute `,` → `_C_` in
  both the article `id` AND the matching delete `hx-target`. Smoking
  gun from the MerchantSettlementCycle card:

  ```html
  id="entity-chain-MerchantSettlementCycle__MerchantPayoutACH_C_MerchantPayoutCheck_C_MerchantPayoutWire_C_MerchantWeeklyPayoutBatch"
  hx-delete="/l2_shape/chain/MerchantSettlementCycle::MerchantPayoutACH,MerchantPayoutCheck,MerchantPayoutWire,MerchantWeeklyPayoutBatch"
  hx-target="#entity-chain-MerchantSettlementCycle__MerchantPayoutACH_C_MerchantPayoutCheck_C_MerchantPayoutWire_C_MerchantWeeklyPayoutBatch"
  ```

  URL still carries `,` (kept — see CG.16 carryover P2 below); the
  selector / target pair is internally consistent and CSS-safe.
  `data-entity-id` still echoes raw `::` and `,` but is a data
  attribute, not a selector — fine.

- **CG.17 — pager clamps on the high end**. Lands. Two
  representative probes:
  - `/l2_shape/account/?page_offset=999` (16-row list, page_size=25,
    a single page) → `Showing 1–16 of 16`, no impossible-range copy.
    The v4 P0 "Showing 1000–16 of 16" footgun is fixed.
  - `/l2_shape/rail/?page_offset=999` (30-row list, page_size=25) →
    `Showing 26–30 of 30` (last page); same for
    `?page_offset=80`. `?page_offset=-5` correctly clamps to first
    page (`Showing 1–25 of 30`). Symmetric handling now in place.

- **CG.18 — limit_schedule card title is `{role} → {rail}` plus
  direction badge**. Lands. From `/l2_shape/limit_schedule/`:

  ```html
  <h3 ...>DDAControl → CustomerOutboundACH <span ... data-role="card-direction-badge">Outbound</span></h3>
  ```

  Seven cards, seven `data-role="card-direction-badge"` spans. The
  v4 P1 "limit_schedule has same composite-key smell CG.12 just
  solved for chain" is closed on the list-card surface. (Edit form
  h1 still uses the composite — see new findings P1.)

- **CG.19 — edit form gains Delete, back-link, display name in
  h1**. Lands for account; partial elsewhere:
  - `/l2_shape/account/gl-1010-cash-due-frb/edit`:
    `<h1>Edit account — Cash & Due From Federal Reserve: gl-1010-cash-due-frb</h1>`,
    visible "← back to Accounts" link, and
    `hx-delete=".../gl-1010-cash-due-frb?from=edit"` with the same
    confirm copy the card uses. All three CG.19 contracts present.
  - Rail / account_template / transfer_template / chain /
    limit_schedule edit forms all gain the back-link + `?from=edit`
    Delete (the framework parts of CG.19). Their h1 falls back to
    `Edit <kind>: <id>` since those kinds have no display-name
    field on the entity. **Chain and limit_schedule edit h1's
    inherit the raw composite-key for `<id>`** — see new findings
    P1.

- **CG.20 — unknown-kind URLs wear full studio chrome**. Lands.
  `/l2_shape/persona/` now returns a 7159-byte page with the full
  app top-nav (Diagram / L2 Editor / ETL Support / Training /
  Dashboards / Docs), an `<h1>Page not found</h1>`, the
  `<code>persona</code> isn't an editable kind…` explanation, and
  a body section with recovery anchors to `/` and `/diagram`. The
  v4 P1 dead-end is gone.

- **CG.21 — `<title>` shapes unified at
  `Recon-Gen · Studio · <surface>[ · <detail>]`**. Lands across
  every full-page surface I crawled. Sample (every entry uses
  middle-dot only — no em-dash leaks):
  - `/` → `Recon-Gen · Studio · qsgen-duckdb` (deployment-name on
    home only — per the lock)
  - `/l2_shape/account/` → `Recon-Gen · Studio · Editor · Accounts`
  - `/l2_shape/account/gl-1010-cash-due-frb/edit` →
    `Recon-Gen · Studio · Editor · Edit account · gl-1010-cash-due-frb`
  - `/l2_shape/account/new` → `Recon-Gen · Studio · Editor · Create account`
  - `/l2_shape/rail/CustomerInboundACH/edit` →
    `Recon-Gen · Studio · Editor · Edit rail · CustomerInboundACH`
  - `/l2_shape/persona/` → `Recon-Gen · Studio · 404`
  - `/l2_shape/theme/` → `Recon-Gen · Studio · Editor · Theme`
  - `/diagram` → `Recon-Gen · Studio · Diagram`
  - `/etl/` → `Recon-Gen · Studio · ETL`
  - `/etl/probe` → `Recon-Gen · Studio · ETL · Probe`
  - `/etl/run` → `Recon-Gen · Studio · ETL · Refresh Data`
  - `/etl/triage` → `Recon-Gen · Studio · ETL · Triage`
  - `/training/` → `Recon-Gen · Studio · Training`

  Single-card read fragment at `/l2_shape/account/gl-1010-cash-due-frb`
  returns a bare `<article>` with no `<title>` — that's the
  documented CG.21.a deferral (HTMX fragment, not a full page).

- **CG.22 — `parent_role` `<select>` partitioned into
  `<optgroup>`**. Lands. From `/l2_shape/account/new`:

  ```html
  <select id="field-parent_role" name="parent_role" …>
    <option value="" selected>— none —</option>
    <optgroup label="Singleton parents (eligible)">
      <option value="ACHOrigSettlement">ACHOrigSettlement</option>
      …(12 singleton roles total)…
    </optgroup>
    <optgroup label="Template roles (not eligible)">
      <option value="CustomerDDA">CustomerDDA</option>
      <option value="MerchantDDA">MerchantDDA</option>
      <option value="ZBASubAccount">ZBASubAccount</option>
    </optgroup>
  </select>
  ```

  The third "Stale (review)" group documented in the CG.22 spec
  is absent — but `sasquatch_pr` has zero stale roles, so the
  branch is correctly empty. The v4 P1 "raw 15-role alphabetical
  blob" is closed.

## Carryover from v4 that's still open

These v4 entries weren't in CG.16–22's scope and remain open by
design:

- **v4 P1 — new-form required markers disagree with the Reference
  prose**. `/l2_shape/account/new` Reference still reads
  *"Required: `id`. Strongly recommended: `role`, `name`"* while
  the form renders `ID *` AND `Scope *` with the red `*` marker —
  `scope` isn't in the prose, and the two strongly-recommendeds
  carry no visual signal. CG.16–22 didn't touch this. Same fix
  shape as v4: lift `scope` into a "Recommended" group or
  reconcile the prose. Carries forward.
- **v4 P2 — `(N)` vs `(set)` count format split**. Section labels
  still mix `Accounts (16)` / `Account templates (3)` / `Rails
  (30)` etc. with `Theme (set)` / `Instance settings (set)`.
  Untouched by CG.16–22, still minor.
- **v4 P2 — pager Prev/Next disabled state is `opacity-50` on a
  `bg-link-tint` (pale-green) background**. Still barely visible —
  the only opacity-50 grep hit on the rail list. Untouched.
- **v4 P2 — glossary tooltip is still `title="Glossary (?)"`** on
  every page that includes the top-nav. Untouched.
- **v4 P2 — chain URL keeps raw `::` and `,` in the path**. The
  `_C_` substitution is selector/id-side only; the URL still reads
  `/l2_shape/chain/MerchantSettlementCycle::MerchantPayoutACH,MerchantPayoutCheck,MerchantPayoutWire,MerchantWeeklyPayoutBatch`.
  Same for limit_schedule paths (`::`). Selector safety is fixed
  (CG.16); copy-paste UX is not. Lower-priority on its own.
- **v4 P2 — home blurb says "Expand one to browse its entries"
  but Accounts is open by default**. CG.15 prose landed verbatim;
  the open-by-default lock (CG.14) is also still in effect.
  Tension noted, not addressed.
- **v4 P2 — Training tab uses Unicode-glyph button prefixes**
  (`▶ Session Start (re-fetch)`, `↻ Force rebuild from base`,
  `🗑 Cleanup`) vs the editor's `+ Add` / `▸` / `↗` family. Out
  of CG.* batch scope, still asymmetric.
- **CG.21.a — single-card read fragment has no `<title>`** is
  documented as deferred. `/l2_shape/account/gl-1010-cash-due-frb`
  returns an HTMX `<article>` fragment, no `<head>`. Behaves
  correctly as an inline-swap target; the only friction is that
  operators who land on the URL directly (browser bookmark, copy/
  paste from a chat) see whatever the previous tab title was. Not
  a regression; called out only because the deferral is explicit.

## New findings (P0 / P1 / P2)

### Severity P0

Nothing here. The three v4 P0s (live-process-staleness,
pager-overflow-range, chain-id-with-commas) are all closed.

### Severity P1

- **Edit-form h1 still leaks the raw `::`-and-`,` composite key
  on `chain` and `limit_schedule`**. CG.12 and CG.18 cleaned the
  list-card titles but the edit page h1's didn't follow:
  - `/l2_shape/chain/MerchantSettlementCycle::MerchantPayoutACH,MerchantPayoutCheck,MerchantPayoutWire,MerchantWeeklyPayoutBatch/edit`
    renders
    `<h1>Edit chain: MerchantSettlementCycle::MerchantPayoutACH,MerchantPayoutCheck,MerchantPayoutWire,MerchantWeeklyPayoutBatch</h1>`.
    The same h1 is then bolted into `<title>` via CG.21's detail
    slot, producing a tab title 100+ characters wide that gets
    truncated to "Recon-Gen · Studio · Editor · Edit ch…" in
    every real browser-tab strip.
  - `/l2_shape/limit_schedule/DDAControl::CustomerOutboundACH::Outbound/edit`
    renders `<h1>Edit limit schedule: DDAControl::CustomerOutboundACH::Outbound</h1>`.

  Same fix shape as CG.12 / CG.18 applied to the edit-page h1: for
  `chain`, use the head segment + a small "(N children)" subtitle
  or `<small>` row; for `limit_schedule`, render the same
  `{role} → {rail} <Outbound badge>` form the list card already
  uses. The `<title>` detail slot should track whatever readable
  form the h1 picks.

- **Account edit h1 dash-vs-colon punctuation is mid-sentence
  ambiguous**. `<h1>Edit account — Cash & Due From Federal
  Reserve: gl-1010-cash-due-frb</h1>` — the em-dash separates
  "account" from the display name, then a colon separates the
  display name from the kebab. Read aloud:
  *"Edit account, Cash & Due From Federal Reserve, gl-1010-cash-due-frb"*
  — and it's not obvious whether "Cash & Due From Federal Reserve"
  is a description of "Edit account" or the name of the entity.
  The list card uses the cleaner shape
  `gl-1010-cash-due-frb <Cash & Due From Federal Reserve>` (kebab
  primary, name as a quieter secondary span with
  `data-role="card-display-name"`). The same idiom on the h1 —
  e.g. `<h1>Edit account: gl-1010-cash-due-frb <span class="text-secondary-fg">Cash & Due From Federal Reserve</span></h1>` —
  would match the list and let the addressing key be the primary
  read. Also: CG.21 explicitly banned em-dash from `<title>`; the
  h1 quietly retains it. Worth picking one separator.

### Severity P2

- **`MerchantDDA` is parked under "Template roles (not eligible)"
  in the CG.22 dropdown, but the term "template role" is internal
  vocabulary the operator hasn't necessarily learned from this
  surface yet**. A CPA who lands here, has read the Reference panel
  *"When this is a subledger account, names its singleton parent's
  Role"*, then sees a category labeled "Template roles (not
  eligible)" has to mentally bridge "template role = the role of
  an account_template = a per-customer multi-instance kind = not a
  singleton = not parent-eligible." The label is technically
  correct, but a `<small>` hint under the select — "Singleton
  parents are L2 roles with exactly one account; template roles
  are per-customer roles materialized into many accounts" — would
  earn the operator the vocabulary without forcing a glossary trip.
- **Limit-schedule card direction badge has no contrast**. The
  `data-role="card-direction-badge"` `<span>` uses
  `text-sm font-normal text-secondary-fg` — same colour and weight
  as a plain hint. With seven cards all reading `Outbound`
  (sasquatch_pr's limit_schedules are uniformly Outbound), the
  badge is visually invisible — it just trails the title in grey.
  When an `Inbound` limit_schedule does show up, the operator
  won't notice the difference. Either tint the badge (small
  rounded pill in `bg-link-tint text-accent`, matching the
  "set" / count chips on home), or drop the field entirely from
  the card title since all seven are the same value in this
  fixture — the asymmetry isn't carrying any signal here.
- **Persona 404 body wording is gentle but doesn't tell the
  operator how they got there**. *"It may have been retired, or
  the link you followed is stale"* — fair. But "retired" is a
  small surprise term in this surface (persona was never a
  shipping kind on this branch). Consider trimming to
  *"`persona` isn't an editable kind in the L2 editor — it lives
  in the deployment cfg, not in the L2 yaml."* The current copy
  invites a "wait, was this ever a thing?" question; the trimmed
  copy answers it.
- **Chain edit page's `<title>` detail slot is excessively long
  for any browser tab strip** — partially folded under P1 above,
  but worth calling out separately as a CG.21 sharp edge: when
  the entity ID is itself a 90-character composite, the title
  shape's `<detail>` slot becomes a footgun. CG.21 should grow a
  per-kind "render the detail slot via the same readable-title
  helper the list card uses" rule.

## Sign-off recommendation

**CG phase is ready to close.** All seven CG.16–22 cells landed
visibly and consistently — the v4 P0 set is fully closed (no
stale-process anti-class, no impossible pager range, no
comma-bearing selector targets), and the v4 P1 set is largely
addressed (limit_schedule list-card composite, persona dead-end,
edit-form Delete + back-link + display-name, `<title>` shape, raw
parent_role dropdown). The two new P1's — composite-key leak on
`chain`/`limit_schedule` edit-page h1 (and downstream `<title>`)
and the account-edit h1's dash-vs-colon ambiguity — are surgical
followups that don't block CG closure; they're a natural
"CG.18.b / CG.19.b" pair if you want one more pass, otherwise
queue for the next cold-read cycle. The remaining carryovers
(form-required-vs-Reference prose, training button idiom, glossary
tooltip wording, opacity-50 disabled state, `(N)`-vs-`(set)`
mixing) are all P1/P2 paper-cuts that have survived two audits and
should either get a dedicated CH-batch or be explicitly accepted
as cosmetic.
