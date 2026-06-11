# BX.6 + BX.11 — Editor Role Reframe

> **Cell brief.** Operator collapsed BX.6 (home re-order + completeness +
> singleton promotion) and BX.11 (Account-vs-AccountTemplate 1:1-vs-1:N
> distinction) into one unified design move: **Roles are the
> user-facing organizing principle in the editor.** Account is a 1:1
> Role; AccountTemplate is a 1:N Role. Same YAML, same primitives, same
> validator — different editor framing.
>
> The prior BX.6 Direction A locked the chrome (dependency-order
> numbering, completeness checkmarks, singletons promoted out of the
> accordion stack). This reframe layers on top of that chrome: under
> the dependency-numbered "Building blocks" band, **one Roles section
> replaces the prior Accounts + Account templates pair**, and a single
> `+ Add Role` button funnels through a 1:1-vs-1:N kind picker.

## Constraint summary (operator locks)

The six locks this cell must honor:

1. **Editor view only.** No YAML schema change. `Account` and
   `AccountTemplate` dataclasses in `common/l2/primitives.py` stay
   exactly as-is; `_HOME_SECTIONS` keeps `"account"` and
   `"account_template"` as distinct `EntityKind`s under the hood.
   Validator + audit PDF + dashboards untouched.
2. **Roles are the user-facing organizing principle.** Account = 1:1
   Role; AccountTemplate = 1:N Role. The word "Role" leads in the home
   section header, in the list page chrome, in the breadcrumb, in the
   read-card title prefix.
3. **Single `+ Add Role` affordance.** One button. Click → modal asks
   "Does this role have **one ledger row** or **many runtime
   instances**?" → picks `1:1 (Account)` or `1:N (Template)` → lands on
   the existing `/l2_shape/account/new` or
   `/l2_shape/account_template/new` form (unchanged). Same shape as
   `/l2_shape/rail/new` 2-step subtype picker (see
   `_render_rail_subtype_picker` at `_studio_editor_routes.py:3770`).
   **Each direction below MUST wire this shape.**
4. **CPA-readable vocabulary** (`[[project_design_north_stars]]`).
   "Role" lands. "1:1" / "1:N" lands (it's the language CPAs use for
   chart-of-accounts relationships). "Materialization" / "singleton" /
   "kind discriminator" do NOT lead — those stay in the glossary
   `[?]` trigger only.
5. **Test anchors are `data-*` attributes** + ARIA labels, not Tailwind
   utility classes (`[[feedback_browser_drivers_user_facing_locators]]`).
   New anchors invented: `data-role="add-role-button"`,
   `data-role="role-cardinality-modal"`,
   `data-role="role-kind-1-1"`, `data-role="role-kind-1-n"`,
   `data-role="role-card"`, `data-role="role-cardinality-badge"`,
   `data-cardinality="one-to-one" | "one-to-many"`,
   `data-section="roles"`.
6. **Prior BX.6 Direction A chrome survives.** Numbered dependency
   order on the home page (`1. Roles ✓` rather than the old
   `1. Account templates ✓ / 2. Accounts ✓`); completeness checkmarks
   per kind (the Roles section's checkmark sums both Account and
   AccountTemplate completeness); singletons (Instance + Theme) stay
   promoted out of the accordion above the building-blocks band.
   The reframe is additive to the chrome, not a replacement.

## Current state

Pre-flight screenshots (referenced by surface, paths under
`/tmp/bx_6_11_screenshots/`):

- `home_accounts_section.png` — home with Accounts (16) accordion
  expanded by default (the CG.14 lock); Account templates (3) listed
  separately, identical chrome. The two kinds are spatially adjacent
  but visually undifferentiated.
- `home_account_templates_section.png` — Account templates section
  expanded showing 3 cards (CustomerDDA, MerchantDDA, ZBASubAccount).
  Header reads `Account templates (3)`. No cardinality cue.
- `list_account.png` — `/l2_shape/account/` dedicated list page. h1
  reads `Accounts`. 16 cards in a 3-column grid. Card title is
  kebab `id` + display name + role badge.
- `list_account_template.png` — `/l2_shape/account_template/` page.
  h1 reads `Account templates`. 3 cards in the same grid chrome.
  Card title is just the role name.
- `create_account.png` — `/l2_shape/account/new`. h1: `Create new
  account`. Fields: ID, Display name, Description, Scope, Role,
  Parent role, Expected EOD balance, Business-day offset, Balance
  cadence. The `Reference` accordion at top carries the CG.6 blurb.
- `create_account_template.png` — `/l2_shape/account_template/new`.
  h1: `Create new account template`. Fields: Role, Description,
  Scope, Parent role, Expected EOD balance, Business-day offset,
  Balance cadence, Instance ID template, Instance name template.
- `edit_account.png` / `edit_account_template.png` — edit pages.
  Both carry a "Where this sits" mini-diagram band (BX.8 wired) and
  the back-breadcrumb to the kind's list page (`← back to Accounts`
  / `← back to Account templates`). Neither surface says "Role" at
  the chrome level.

**What's broken from the role-reframe perspective:**

- The word "Role" appears as a field label inside both forms but
  **never as the parent organizing concept**. A consultant reads
  "Accounts" + "Account templates" as two unrelated kinds rather
  than two variants of the same Role concept.
- The cardinality decision is implicit (`/l2_shape/account/new` vs
  `/l2_shape/account_template/new`) — the operator must already
  know which form to open BEFORE the editor can teach the
  distinction.
- Two `+ Add` buttons on the home page, two `+ Add` buttons on the
  list pages, two h1 strings. Every duplication is one more
  surface the consultant has to mentally collapse.

## Directions

Three directions, ordered from minimum-touch (D1) to most-ambitious
(D3).

### D1 — "Wrapper section, two sub-buckets, single add affordance"

**Thesis.** Lightest-touch reframe. The home `Accounts` +
`Account templates` accordion pair collapses into ONE outer accordion
labeled **`1. Roles (19)`** that, when open, renders **two
sub-buckets** inside: `1:1 — Accounts (16)` and
`1:N — Templates (3)`. The kebab-id grid + role-name grid render
unchanged below their respective sub-headers. The `+ Add Role` button
sits on the outer Roles accordion header (one button); clicking it
opens a small modal asking "1:1 or 1:N?" with two big buttons. The
two existing list pages stay at their current URLs (no route renames);
the modal navigates to `/l2_shape/account/new` or
`/l2_shape/account_template/new` unchanged.

**Mockup:**

```
┌─────────────────────────────────────────────────────────────────┐
│ START HERE — institution-wide configuration                     │
│ ┌─────────────────────┐  ┌─────────────────────┐                │
│ │ Instance settings ✓ │  │ Theme ✓             │                │
│ └─────────────────────┘  └─────────────────────┘                │
├─────────────────────────────────────────────────────────────────┤
│ BUILDING BLOCKS — in dependency order                           │
│ ▾ 1. Roles (19 declared) ✓     [Search...]  [+ Add Role] [↗]    │
│   How roles work — every rail, chain, and limit references a    │
│   role; some roles are one ledger row, others fan out into many.│
│   [?] Glossary: 1:1 vs 1:N                                      │
│                                                                 │
│   ┌── 1:1 — Singleton accounts (16) ────────────────────────┐   │
│   │  GL line items + control accounts — one ledger row each │   │
│   │  ┌────────────────────┐  ┌────────────────────┐         │   │
│   │  │ gl-1010-cash-…  ✎  │  │ gl-2010-ach-orig…  │         │   │
│   │  │ Cash & Due From FRB│  │ ACH Origination… ✎ │         │   │
│   │  │ [CashDueFRB]       │  │ [ACHOrigSettlement]│         │   │
│   │  └────────────────────┘  └────────────────────┘         │   │
│   │  ... 14 more ...                                        │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│   ┌── 1:N — Templated roles (3 patterns → ~4 156 instances)┐    │
│   │  Patterns fanned out by ETL — one declaration, many    │    │
│   │  runtime rows per declaration                          │    │
│   │  ┌────────────────────┐  ┌────────────────────┐        │    │
│   │  │ CustomerDDA     ✎  │  │ MerchantDDA     ✎  │        │    │
│   │  │ → ~4 016 inst.     │  │ → ~140 inst.       │        │    │
│   │  └────────────────────┘  └────────────────────┘        │    │
│   └────────────────────────────────────────────────────────┘    │
│                                                                 │
│ ▸ 2. Rails (30) ✓             [Search] [+ Add Rail] [↗]         │
│ ▸ 3. Transfer templates (3) ✓ [Search] [+ Add] [↗]              │
│ ▸ 4. Chains (5) ✓             [Search] [+ Add] [↗]              │
│ ▸ 5. Limit schedules (7) ✓    [Search] [+ Add] [↗]              │
└─────────────────────────────────────────────────────────────────┘
```

**`+ Add Role` modal:**

```
┌─────────────────────────────────────────────┐
│ Add Role                              [×]   │
│ ─────────────────────────────────────────── │
│ Does this role exist as ONE ledger row,     │
│ or as MANY runtime instances?               │
│                                             │
│ ┌─────────────────────────────────────────┐ │
│ │ 1:1 — One ledger row                  → │ │
│ │ The role IS the account. One id, one    │ │
│ │ row on the trial balance.               │ │
│ │ E.g. CashDueFRB, ACHOrigSettlement.     │ │
│ └─────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────┐ │
│ │ 1:N — Many runtime instances          → │ │
│ │ One declaration; ETL materializes N     │ │
│ │ rows (one per customer, merchant, etc.).│ │
│ │ E.g. CustomerDDA, MerchantDDA.          │ │
│ └─────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

Modal implementation: HTMX-driven inline overlay (no new JS lib).
`<dialog>` element with `data-role="role-cardinality-modal"`; the two
buttons are `<a href="/l2_shape/account/new" data-role="role-kind-1-1">`
and `<a href="/l2_shape/account_template/new"
data-role="role-kind-1-n">`. Plain navigation — no HTMX swap on click
because the destination is a full form page. Backdrop click + `Esc`
close.

**List pages.** Stay at separate URLs (`/l2_shape/account/` +
`/l2_shape/account_template/`). h1s rebrand from "Accounts" /
"Account templates" to **"Roles — 1:1 (Singleton accounts)"** and
**"Roles — 1:N (Templated)"**. Back-breadcrumb on every edit page
points to the same kind-prefixed list (no cross-link beyond a
sibling-list `→ See 1:N templated roles` link below the h1).

**Read card chrome.** Card title gets a `data-cardinality` badge
in the same slot as the rail subtype badge:
- Account: `gl-1010-cash-due-frb [Cash & Due From Federal Reserve]
  [CashDueFRB · 1:1]`
- AccountTemplate: `CustomerDDA [1:N · ~4 016 instances]`

**Edit page header.** h1 reframed:
- `Edit Role · gl-1010-cash-due-frb (1:1 — Singleton account)`
- `Edit Role · CustomerDDA (1:N — Templated)`

Breadcrumb reads `← back to Roles · 1:1` / `← back to Roles · 1:N`.

| Axis | Score | Notes |
|---|---|---|
| Effort | **Low-Medium** (4-6h) | Wrapper section needs custom layout outside `_HOME_SECTIONS` (a 7th synthetic entry replaces the prior 2). Modal is ~30 LOC HTML + ~20 LOC CSS. List h1s + edit h1s + breadcrumb labels are copy-only. Completeness rollup sums two kinds' checkmarks. |
| Risk | **Low** | YAML untouched. Two existing URLs + forms unchanged — only chrome strings + the new modal step. Easy to A/B (flag the wrapper render path off → original two-section layout returns). |
| Mental-model fit | **High** | "Role" leads at every entry point. The 1:1 / 1:N badge sticks across home → list → read card → edit. Single Add button removes the "wrong-form risk" the cold-read flagged. |
| Migration cost (operator vocab) | **Low-Medium** | Existing operators learn one new word (Role-as-parent) but the URLs they have bookmarked still work. Docs / SPEC stay AccountTemplate vocabulary; only the editor surface speaks Role. |
| Cross-renderer parity | **N/A** | Studio-only. Dashboards never list L2 entities. |

---

### D2 — "Unified `/l2_shape/role/` list with cardinality filter"

**Thesis.** Go further: introduce a NEW route
`/l2_shape/role/` that renders ALL Account + AccountTemplate cards in
one grid with a top-of-page filter `[All · 1:1 · 1:N]` segmented
control. Existing `/l2_shape/account/` + `/l2_shape/account_template/`
URLs 301-redirect to `/l2_shape/role/?cardinality=1-1` and
`/l2_shape/role/?cardinality=1-n` so bookmarks self-heal. The home
page's Roles section embeds this same list (no sub-buckets — just one
grid with the filter applied; default `All`). The `+ Add Role` modal
matches D1.

**Mockup (home Roles section, expanded):**

```
▾ 1. Roles (19 declared) ✓             [+ Add Role] [↗]
   How roles work — every rail/chain/limit references a role.
   Roles split: 1:1 = one ledger row; 1:N = ETL-materialized.

   [ All (19) · 1:1 (16) · 1:N (3) ]     [Search roles...]

   ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐
   │ CashDueFRB    1:1  │  │ ACHOrigSettlement  │  │ CustomerDDA   1:N  │
   │ gl-1010-cash-due-… │  │ gl-2010-ach-orig…  │  │ → ~4 016 instances │
   │ Cash & Due From… ✎ │  │ ACH Origination… ✎ │  │ Customer DDA   ✎   │
   └────────────────────┘  └────────────────────┘  └────────────────────┘
   ... 16 more cards (mixed 1:1 + 1:N, sortable by role) ...
```

**Mockup (dedicated `/l2_shape/role/` page):**

```
┌─────────────────────────────────────────────────────────────────┐
│ Roles                                                           │
│ Every rail / chain / limit-schedule references a role. Each     │
│ role is either a singleton account (1:1) or a templated         │
│ pattern (1:N). [?] Glossary: 1:1 vs 1:N                         │
├─────────────────────────────────────────────────────────────────┤
│ [ All (19) · 1:1 — 16 accounts · 1:N — 3 patterns ]             │
│ [Search roles...]                          Sort: [Default ▼]    │
│ ─────────────────────────────────────────────────────────────── │
│ ┌────────────────────┐  ┌────────────────────┐  ...             │
│ │ (cards as above)   │  │                    │                  │
│ └────────────────────┘  └────────────────────┘                  │
│                                            Page 1 / 1   [+ Add]│
└─────────────────────────────────────────────────────────────────┘
```

Filter state lives in `?cardinality=all|1-1|1-n`; the toolbar Q1A/B
URL-state machinery extends to carry it (`role_q`, `role_cardinality`,
`role_sort_column`, `role_page_offset` on the home embed; bare on the
dedicated page). `data-section="roles"`,
`data-cardinality-filter="all|1-1|1-n"` anchors.

**Internal-routing punt.** Behind the new route, the server still
fetches `instance.accounts` + `instance.account_templates` separately
and renders cards via the existing per-kind read-card helpers. The
sort axis is the role string (CG.18 already supports lex sort on
both); the toolbar's `kind` field carries a synthetic `"role"` value
that the toolbar primitive accepts (one new entry in
`SORT_AXES_BY_KIND` keyed on `"role"`). **No new `EntityKind` enum
value** — `"role"` is a route-only synthetic that fans out to two
real kinds inside the rendering loop.

**Edit page.** Edit URLs stay `/l2_shape/account/<id>/edit` +
`/l2_shape/account_template/<role>/edit` (no rename — those are the
opaque addressing keys; renaming the URL breaks the validator error
back-link contract). Back-breadcrumb on every edit page points to
`/l2_shape/role/?cardinality=<cardinality>` so the operator lands
back on the same filtered view they came from.

| Axis | Score | Notes |
|---|---|---|
| Effort | **Medium-High** (10-14h) | New route + the synthetic `"role"` toolbar kind + the 301-redirect on the legacy URLs + extending `_render_summary_search_input` to accept a synthetic kind. The completeness rollup is the same as D1 but the home rendering loop has to special-case `"role"` to fetch from two instance accessors. |
| Risk | **Medium-High** | The "synthetic kind" pattern is new — the toolbar primitive's `ListToolbarState.__post_init__` rejects unknown kinds; either it learns a new pseudo-kind (cross-cutting change with implications for the CF.4 chain) or the home embeds the filter outside the toolbar (inconsistent UX). The 301 redirects also cross-couple two existing routes that have separate tests; ~5-8 e2e tests touch the changed URLs. |
| Mental-model fit | **Highest** | One list, one URL, one mental model. The cardinality filter IS the disambiguation surface. CPAs read this exactly the way they read a chart of accounts (one ordered list of roles, each tagged 1:1 / 1:N). |
| Migration cost (operator vocab) | **Medium** | Existing bookmarks to `/l2_shape/account/` redirect (transparent); existing bookmarks to `/l2_shape/account/<id>/edit` survive unchanged. SPEC + audit PDF stay AccountTemplate-vocabulary — only the editor speaks Role. Operators with muscle memory for "go to Accounts" relearn "go to Roles." |
| Cross-renderer parity | **N/A** | Studio-only. |

---

### D3 — "Hybrid: D1's home wrapper + D2's dedicated `/l2_shape/role/` page"

**Thesis.** Take D1's home-page treatment (wrapper section with two
labeled sub-buckets, single `+ Add Role` modal) AND add D2's
dedicated `/l2_shape/role/` page reachable from the home's `[↗ open]`
link, BUT keep `/l2_shape/account/` and `/l2_shape/account_template/`
as separate, deep-linkable pages. The Roles page is the
**default landing for the kind**; the per-cardinality pages remain
for sharing / scripting / focused triage.

**Mockup notes.**

- Home Roles section: identical to D1's mockup (two labeled
  sub-buckets, single Add button + modal).
- `[↗ open]` from the home section points to `/l2_shape/role/` (D2's
  dedicated page).
- `/l2_shape/role/` has a banner at top: `Looking for just the 1:1s?
  [→ Singleton accounts] · Just the 1:N? [→ Templated roles]`.
  Those two links point to `/l2_shape/account/` +
  `/l2_shape/account_template/`, which render with their existing
  shape + an `← back to all Roles` breadcrumb above the h1.
- Edit pages: same as D1 (h1 reframed, breadcrumb reframed,
  cardinality badge).

**Why hybrid:** D1's home gives the at-a-glance comparison
(sub-buckets, side-by-side counts), which IS the cold-read's "show
me the distinction" finding. D2's `/l2_shape/role/` gives the
unified search/sort/filter surface that scales when the institution
has 200+ roles. Keeping the per-cardinality pages avoids the
`SortToolbarState` complication of D2 (each existing route's
toolbar stays addressing one real `EntityKind`).

| Axis | Score | Notes |
|---|---|---|
| Effort | **Medium-High** (8-12h) | D1's home work + a new `/l2_shape/role/` route. The new route is the simpler of D2's two routes (no 301-redirect chain on existing URLs, no synthetic `EntityKind` in the toolbar). |
| Risk | **Medium** | Three surfaces to keep in vocabulary-sync (home + Role page + per-cardinality pages). Test coverage doubles for the read-card chrome (every test asserting "Account list shows N cards" needs a sibling assertion on the Roles page). |
| Mental-model fit | **High** | Home + dedicated Roles page both lead with "Role"; per-cardinality pages are escape hatches with explicit "← back to all Roles" framing. |
| Migration cost (operator vocab) | **Low** | Existing URLs unchanged; existing bookmarks work; the Roles page is purely additive. Operators learn it organically by following the home page's `[↗ open]` link. |
| Cross-renderer parity | **N/A** | Studio-only. |

---

## Recommendation

**Pick D1.**
- Comment: Agreed

Reasoning:

1. **It honors all six locks at the smallest blast radius.** No new
   routes, no synthetic `EntityKind`, no 301-redirect chain, no
   toolbar primitive extension. The reframe is chrome-string +
   one modal + one wrapper accordion. The existing test set (e2e
   harness, the 5 unit assertions on `_HOME_SECTIONS` ordering)
   needs targeted updates, not rewrites.
2. **The modal-led Add flow IS the cardinality teaching surface
   the cold-read asked for.** The "wrong form risk" — operator
   clicks `+ Add account` when they meant `+ Add account template`
   — disappears the moment there's one button + a forced choice
   with explicit prose for each option. This is the exact pattern
   the rail subtype picker uses, and it works (cold-read v3
   didn't flag rail-subtype confusion).
3. **D2's `/l2_shape/role/` route is the right end-state but the
   wrong NOW.** A new route means a new `ListToolbarState`
   variant, which means a new `EntityKind` (or a synthetic
   kind that ripples through the CF.4 toolbar chain). The
   payoff — search/sort/filter unified — is real only at scale
   (200+ roles). At Sasquatch's 19 roles, the home's wrapper
   accordion already does the unified-view work.
4. **D3 is D2 with a softer migration but is still cost-heavier
   than D1 for the same mental-model win.** The dedicated
   `/l2_shape/role/` page mostly duplicates the home wrapper
   section's content. Operator dogfood will tell us whether
   the extra page earns its weight; ship D1 first and let
   real usage drive the D3 follow-up if needed.
5. **Future-flexibility.** If the operator later wants D2's unified
   route OR D3's hybrid, D1's wrapper accordion is the natural
   prototype + the cardinality modal + the role-cardinality
   badge are reusable wholesale. No work is thrown away by
   shipping D1 first.

**On the prior BX.6 Direction A inheritance:** the chrome (numbered
dependency order, completeness checkmarks per kind, singletons above
the building-blocks band) survives unchanged. The reframe replaces
the prior `1. Account templates ✓ / 2. Accounts ✓` pair at the top of
the dependency order with a single `1. Roles ✓` — and the dependency
order shifts down by one (Rails becomes `2.`, etc.). The
completeness rollup for Roles is `set` iff
`compute_home_completeness(instance)[account] == "set" AND
compute_home_completeness(instance)[account_template] == "set"`;
`partial` if either is `partial`; `empty` if both are `empty`.

## Open questions

For the operator to weigh in beyond direction pick:

1. **Dependency order — is Roles really `1.`?** Direction A's prior
   recommendation put Account templates first (it was a separate
   debate; operator said accounts and account templates are equal,
   then proposed this reframe). With Roles as one wrapper, the
   first-step question is "is Roles before Rails?" — yes (rails
   reference roles, never the other way) — but the sub-bucket
   ordering inside Roles (1:1 first vs 1:N first) is the new
   question. Cold-read framing favors 1:1 first ("the simpler
   case teaches the concept; templated is the extension"). Confirm.

- Comment: Agreed

2. **Where does the 1:1 / 1:N glossary `[?]` trigger live?** Three
   options: (a) on the outer Roles accordion header (one trigger,
   teaches the concept once); (b) on each sub-bucket header (two
   triggers, each pointing at its own cardinality definition);
   (c) inside the `+ Add Role` modal next to the question prompt
   (zero on the home; available exactly when needed). Recommend
   **(a) + (c)** — outer header for ambient learning,
   modal-inline for in-flow guidance. Skip (b) — duplicate
   triggers on sibling sub-buckets is noise.

- Comment: Agreed

3. **Should the read-card cardinality badge carry the instance count
   for templates?** D1's mockup shows `[1:N · ~4 016 instances]`;
   D2/D3 carry the same. BX.11's prior Direction D2 already
   defined the `instance_count_by_role` helper (BX.11 §D2 open
   question 1). Cheapest is **(b) — live query on
   `<prefix>_daily_balances` for COUNT(DISTINCT account_id) WHERE
   account_role = '<role>' AND balance_date = MAX(...)`** with
   fallback to `[1:N · awaiting first ETL]` when count=0 and
   `[1:N]` alone when no DB. Same call BX.11 D5 made. Confirm
   carry-forward.

- Comment: Agreed

4. **What happens to the per-cardinality list pages
   (`/l2_shape/account/` + `/l2_shape/account_template/`)?** D1
   keeps them with rebranded h1s; D2 301-redirects them; D3 keeps
   them as escape hatches. If D1 is the pick, the operator can
   choose between (a) rebrand-only ("Roles — 1:1" h1 on
   `/l2_shape/account/`) or (b) leave the URLs but keep the old
   h1 (no rebrand) so the link-shared-in-Slack scenario reads as
   `Accounts`. Recommend (a) — vocabulary consistency.

- Comment: Agreed

5. **Cardinality vocabulary on the badge — `1:1` / `1:N` vs
   `Singleton` / `Pattern`?** The BX.11 prior recommendation
   used `Singleton` / `Pattern`. The role reframe pushes toward
   the math notation `1:1` / `1:N` because (a) it's the explicit
   answer to the modal's question; (b) it reads CPA-natural
   (chart-of-accounts relationships are written that way); (c)
   it's terse on the card grid (two characters vs eight). Recommend
   **`1:1` / `1:N`** on the badge; `Singleton account` /
   `Templated role` as the secondary-fg sub-line inside the
   read card body and in the modal prose. Glossary entry
   updated to lead with the math notation + cross-reference
   `singleton` for the prior term.

- Comment: Agree on recommendation

6. **Does the modal carry an "advanced" link to "Add directly via
   YAML"?** Cold-read v3 has not flagged operator demand for a
   YAML-only escape hatch. Skip the link in v1; if the dogfood
   shows operators bypassing the modal repeatedly, add a
   `[Author YAML directly →]` link in v2.

- Comment: No direct yaml, edit the file if you love yaml

## Carries forward from prior BX.6 Direction A

What **survives unchanged** in this reframe:

- **Numbered dependency order on the building-blocks band.** Roles
  is `1.`; Rails `2.`; Transfer templates `3.`; Chains `4.`;
  Limit schedules `5.` (the prior `1. Account templates / 2.
  Accounts / 3. Rails / ...` ordering collapses by one as the two
  account-side kinds merge into Roles).
- **Completeness checkmark glyphs per kind** (`✓` / `⚠` / `✗`).
  The Roles checkmark is a rollup of Account + AccountTemplate
  completeness (the typed `compute_home_completeness` helper
  returns a per-`EntityKind` map; the home renderer combines the
  two for the Roles section header).
- **Singletons (Instance + Theme) promoted out of the accordion
  stack** into a `START HERE` strip above the building-blocks
  band. Two side-by-side tiles with `data-singleton="instance" |
  "theme"` anchors. Tile copy from BX.6 §open-question 4 lands:
  Instance — "Sets institution-wide identity used in every
  dashboard title + the audit PDF footer." Theme — "Drives the
  color palette + dashboard styling. Defaults work — visit only
  when ready to brand." (Operator's earlier comment: theme is
  due for trim; that's a separate cell.)
- **`compute_home_completeness(instance) -> Mapping[EntityKind,
  Literal["set", "empty", "partial"]]`** typed helper. The
  reframe layers a derived `roles_state` on top
  (`max(state[account], state[account_template])` under the
  ordering `empty < partial < set`).
- **Header prose links the diagram conservatively** —
  `→ View diagram` lives in the home header prose, not embedded.
  CF.3.l's "diagram-in-home was tried and removed" lock stays
  intact.
- **App2Driver-stable `data-*` anchors throughout** —
  `data-step="1"` for Roles, `data-singleton="instance"`,
  `data-completeness="roles"`, plus the new
  `data-role="role-cardinality-modal" / "role-kind-1-1" /
  "role-kind-1-n" / "role-cardinality-badge"` invented above.

What **changes** under the reframe:

- The `_HOME_SECTIONS` tuple loses `("account", ...)` and
  `("account_template", ...)` as separate entries and gains one
  synthetic `("roles", "Roles", _accessor_returning_both)` entry
  at index 0. Under the hood, `_render_home_page` special-cases
  this entry's renderer to render two sub-bucket grids
  (`_render_role_sub_bucket("account")` +
  `_render_role_sub_bucket("account_template")`), each lazy-loaded
  via the existing `?embed=1` editor route. The `EntityKind` enum
  values stay `"account"` / `"account_template"` — only the
  outer accordion is synthetic.
- The home-section `+ Add` button slot on the Roles entry renders
  the new `+ Add Role` button (opens modal); the per-sub-bucket
  `+ Add` link is suppressed (one Add button per section, as
  before).
- List page h1 + back-breadcrumb labels rebrand to lead with
  "Role" (per OQ4 recommendation (a)).
- Edit page h1 + back-breadcrumb labels rebrand. The `_edit_h1_parts`
  helper at `_studio_editor_routes.py:3161` learns a per-kind
  cardinality-prefix branch for `account` / `account_template`.
- Read card title gains the `[1:1]` / `[1:N · ~N instances]` badge
  in the same slot as the existing rail subtype badge — extends the
  `_render_read_card_summary` badge slot per BX.11 §D2 prior plan.
- Side-panel glossary (`_side_panel.py::GLOSSARY`) gains a `1-1` /
  `1-n` entry (lead with the math notation); the existing
  `singleton` entry stays but cross-references the new entries.
  `+ Add Role` modal inline prose lands the same definitions
  in shorter form.
