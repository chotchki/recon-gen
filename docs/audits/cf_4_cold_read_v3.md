# CF.4 cold-read v3 — 2026-06-05

## Background

Cold-read review of the L2 Editor at http://127.0.0.1:8765 against the live
Studio (DuckDB + sasquatch_pr fixture, `qsgen-duckdb` deployment). Reviewer
arrived with zero CF.4 context — only the public surface description, no
PLAN.md, no CF.4 commit history. Surfaces touched: `/` (home, default tab),
`/l2_shape/{account,rail,chain,transfer_template,account_template,limit_schedule}/`
dedicated pages, `/l2_shape/{theme,instance,persona}/` structured forms,
`/l2_shape/account/new`, `/l2_shape/account/<id>/edit`, `/training/`,
`/diagram`, `/etl/`. Findings derived from raw HTML over curl
(structure, copy, affordances, htmx wire targets) — no browser, no JS
execution. Behavioral hypotheses are flagged "may be broken — operator
verify".

## Findings

### Severity P0 (must-fix before next demo)

- **Persona "Edit" link 404s.** Home (`/`) lists Persona section with `<a href="/l2_shape/persona/">Edit</a>`, and `(not set)` count suffix. Following the link returns `404 — persona is not an editable entity kind (yet).` The 404 page has no top-nav either, so the operator is stranded. Either (a) ship the persona structured form, (b) remove the section from home until it ships, or (c) replace the `<a>` with a disabled-looking element + tooltip "Coming soon". File: home renderer that emits `<details data-kind="persona">` + the persona route handler. The current shape — list it, link it, 404 — is the worst of three options.

- **Pager Next/Prev in embedded mode targets `#entity-list` but home wraps section bodies in `#home-section-body-<kind>`.** In the `/l2_shape/rail/?embed=1` body, the Next link's `hx-target="#entity-list"` (line 164 of rail_page.html shape, reused in embed) — but `#entity-list` doesn't exist on `/`; the home page wraps the rail section body in `<div id="home-section-body-rail">`. Hypothesis: clicking Next on a 30-rail section from home page will silently fail to swap, or HTMX will fall back to the global swap-into-body. Verify in browser. Either way the embed body needs to emit a target-aware pager (use `#home-section-body-<kind>` when `embed=1`).

- **Pager URLs double the `embed=1` query param.** Both Next and Prev in embed mode emit `hx-get="/l2_shape/rail/?embed=1&page_offset=25&page_size=25&embed=1"` (note the trailing `&embed=1`). Doesn't break the request — query parsers take the last value — but it's a smell flagging a query-builder that appends `embed=1` twice. Will bite the first time someone adds a third param. Fix where the pager renders the embed query string.

### Severity P1 (worth fixing in the next CF cycle)

- **Card-level expand affordance is inconsistent across kinds.** `account` and `rail` cards use the new `▸` chevron + `<details hx-trigger="toggle once">` lazy-load pattern. `chain`, `transfer_template`, `account_template`, `limit_schedule` render fully-expanded inline cards with no chevron, no lazy-load. Scrolling through 30 collapsed rails vs 5 unrolled chains (each with a multi-paragraph description and Children list) feels like two different products. Pick one: either all kinds get the chevron + lazy-load (preferred — keeps the at-scale promise), or none do. Surfaces: every embed body under `/l2_shape/<kind>/?embed=1`.

- **Section header (the `<details summary>` carrying search + Add) has no chevron** — it relies on the browser-default `<details>` triangle. Cards INSIDE the section DO have the explicit `▸ → 90°` chevron. Two different open/close affordances on the same page break the visual contract from CF.4 changelog item 3. Add the same `▸` chevron to the section summary so the operator learns the gesture once.

- **Dedicated kind pages (`/l2_shape/<kind>/`) have no page header.** Compare to `/` (h1 "L2 Editor" + blurb on white strip), `/training/` (h1 "Training" + blurb), `/etl/` (h1 "ETL Support" + blurb) — all three use the documented CF.4 shape. But `/l2_shape/rail/` jumps from top-nav straight to a stand-alone search bar, no h1, no context. The user landing from the home page's `↗` "Open in dedicated page" loses orientation. Add the same `<header class="px-8 py-4 border-b border-surface-border bg-white">` shape with h1 + blurb.

- **Theme / Instance settings / new-form / edit-form pages have NO shared top-nav.** They keep the OLD `<header>` with "← back to Studio" link only. So opening `/l2_shape/theme/` or any `/l2_shape/<kind>/new` drops the operator out of the L2 Editor tab visually — no way to jump to Diagram or a dashboard without backing out. CF.4 changelog item 6 says "Shared top-nav across all Studio surfaces" — these are Studio surfaces. Verify with operator whether structured-form pages were intentionally excluded.

- **Empty search returns a blank card grid + a tiny "No matches" by the pager.** Try `/l2_shape/account/?embed=1&q=zzzz` — the cards `<div>` is just `<div ... data-kind="account"></div>` (empty), and "No matches" lives down in the pager strip. No "Nothing matches 'zzzz' — clear search or check spelling" in the result area itself. For a search-driven surface designed for 100+ entities, the empty state is the most-hit failure mode and deserves an actual message in the card area.

- **Diagram page (`/diagram`) has no h1 / page header strip.** Matches the old QS-style chrome-free pattern. Inconsistent with the CF.4 white-strip shape now used by home, training, ETL. Either: (a) add the strip above the canvas wrapper, (b) document why diagram is exempt (the floating sidebar covers the same surface). Worth picking deliberately.

- **Card title is plain text — but for accounts the title is just the kebab ID.** `<h3>gl-1010-cash-due-frb</h3>` with `break-all`. The human-readable label "Cash & Due From Federal Reserve" exists in the body and only renders after expand. CF.4 changelog item 2 removed the `/diagram` link from the title — fine — but didn't surface the `display_name` next to the ID. For an operator who knows the institution by Fed-statement names, not GL kebabs, the at-a-glance scan is broken. Rails show a `[two-leg]`/`[single-leg]` badge in the title; accounts should show their `display_name` (greyed, smaller, after the ID).

- **Chain card titles are `Parent::ChildA,ChildB,ChildC` — entirely unreadable at width.** E.g. `MerchantSettlementCycle::MerchantPayoutACH,MerchantPayoutCheck,MerchantPayoutWire,MerchantWeeklyPayoutBatch`. Title wraps with `break-all`. Title slot should be Parent only; children belong in the body (and already are — `Children` is a `<dt>`). Otherwise the chain section is unscannable.

- **`aria-label` and `title` tooltips leak raw kind names.** `aria-label="Search account_templates"`, `aria-label="Search transfer_templates"`, `title="Create a new account_template"`, `title="Create a new limit_schedule"` — underscores intact. Screen-reader output and tooltip hover both read "account underscore templates". Use the visible label ("account templates", "transfer templates", etc.).

- **Account section is the only one open on home page load.** All other sections (`account_template`, `rail`, `transfer_template`, `chain`, `limit_schedule`, `theme`, `persona`, `instance`) collapsed. Implicit message: "accounts matter most, rest is secondary." Probably not the intended hierarchy for a reconciliation tool where rails are the verb. Either open the top three (accounts + rails + something), open none and persist last-open state, or document the choice. As-is, every operator's first click after page load is "expand rails".

- **The home page blurb says "saves cascade across sections automatically" — true, but it's the wrong onboarding sentence.** From a cold-read first session, the salient question is "where do I start?" not "what happens when I save?". The cascade behavior is reassurance for a 5th-session user. Suggested rewrite: *"Every section collapses to its header — click a header to open, then use the inline search to find entities by ID, the + Add link to create one, or ↗ to open the section in a dedicated page. Saves cascade automatically; no need to refresh other sections."*

### Severity P2 (nice to have)

- **`+ Add` and `↗` links in the section header lose pointer-cursor feedback on the surrounding details summary.** The summary is `cursor-pointer`, the links inherit, but `onclick="event.stopPropagation()"` means clicking them doesn't toggle the section. That's fine, but the lack of visual differentiation (no icon, no border, no underline-by-default) makes them blend into the count `(16)` text. For a primary "create" action, the `+ Add` should look more like a button.

- **Section count format is mixed.** Most kinds use `(16)`, `(30)` etc. Theme/Instance use `(set)`. Persona uses `(not set)`. Consistent shape would be: numeric kinds always show count; singleton kinds always show `(set)` / `(not set)`. Currently fine, but flag if the Persona section becomes editable — `(empty)` reads better than `(not set)` once the surface exists.

- **Pager footer shows "Showing 1–25 of 30" — fine — but Prev/Next styling is identical when disabled vs enabled.** Both states get `bg-link-tint text-accent border border-surface-border`. The disabled state adds `opacity-50 cursor-not-allowed pointer-events-none` — opacity-50 on link-tint is barely visible (link-tint is already a low-contrast pale green). On a 30-row rail list, the "Prev →" disabled looks clickable; operator needs to actually try it to know. Bump disabled opacity to 30% or replace with neutral grey.

- **Sticky pager would help.** Pager is fixed at the bottom of the section content; on a 100-rail page the user has to scroll to the bottom to paginate. Either sticky-position the pager OR mirror it above the cards on long sections. Pager-below convention matches dashboards (per CF.4 item 5) — that convention came from the dashboard's typically-small-result-set shape; the editor's at-scale-100+ shape may not fit. Open question for operator (see below).

- **Side-panel `[?]` glossary opens via JS event delegation on click.** Mouseover gives `title="Glossary (?)"`. If the operator scans the right edge of the nav, "Glossary (?)" reads as "the glossary is unclear" rather than "press ? to open glossary". Use `title="Help & glossary — press ?"` or drop the parenthetical.

- **Card delete confirm dialog shows the full chain ID including `::`.** For `chain` cards, the `hx-delete` URL keeps `::` (e.g. `/l2_shape/chain/CustomerInboundACH::CustomerInboundACHReturnNSF,CustomerInboundACHReturnStopPay`) but the entity-card `id` swaps to `__`. The `hx-confirm` text is generic ("Delete this entity?") so this is cosmetic — but when a delete fails on a referenced chain, the error needs to show a friendly chain title, not the colon-separated kebab. Hypothesis only — verify in operator's flow.

- **`<title>` element on dedicated pages reads "Studio editor — rail" / "Studio · ETL Support — qsgen-duckdb" / "Studio diagram — qsgen-duckdb"** — inconsistent shape (em-dash vs middle-dot, deployment name sometimes included). Pick one. Browser tab strip benefits from a `Recon-Gen · <surface>` prefix.

## Pleasant surprises

- **Home page collapsible sections + inline search-on-summary is the right primitive at scale.** Typing into the search input opens the section automatically (`oninput="this.closest('details').open=true"`) — that handles the "I'm searching for X and don't know which kind it is" case gracefully (modulo the search being per-section, not global — see open question below). Keep this.

- **`hx-trigger="toggle once"` lazy-load on cards is exactly right.** Account body markup is `<div data-role="card-body" class="text-xs text-secondary-fg italic mt-1">loading…</div>` and HTMX swaps in the dl on first open, never re-fetches. At 100+ accounts, this is the right knob. Don't let anyone "optimize" it to eager-load on `details open=true` default.

- **L2 Editor tab IS correctly highlighted on sub-pages.** `/l2_shape/rail/` carries `font-semibold text-accent bg-accent/5 border-b-2 border-accent` on the L2 Editor anchor. Same for `/diagram` / `/etl/` / `/training/`. The nav-state-on-subroute pattern was the thing most likely to break in a top-nav-unification push, and it didn't.

- **Section group labels in nav (Studio / Dashboards / Reference) use small caps + accent tint** — visually distinct from the items themselves, scannable without being noisy. Good chrome decision.

- **Empty-state singleton sections (Theme, Persona, Instance) explicitly say "click Edit to view / change it"** instead of rendering an inline summary. Good — singletons don't fit the card-grid shape and pretending otherwise would be worse.

## Open questions for operator

- **Is the per-section search scoping deliberate?** The blurb on home says "Browse and edit the institution's accounts, rails, transfer templates, chains, and limit schedules" — but search is scoped to one kind at a time. A cross-kind search ("where does the name `MerchantSettlement` appear?") needs the operator to query each section separately. At 100+ entities per kind, that's the killer use case. Was a top-level "search anything" considered and rejected, or just not built yet?

- **Are the structured-form pages (Theme / Instance / Persona-when-shipped) intentionally excluded from the shared top-nav?** They use the older `← back to Studio` header. The CF.4 push notes "Shared top-nav across all Studio surfaces" — does "Studio surfaces" deliberately exclude structured forms (because the form is the operator's full focus and chrome would distract), or is this an unfinished sweep?

- **The "+ Add" affordance currently lives in the collapsed section header. For the at-scale 100+ shape, should the operator's first impulse be "find an existing entity" (search) or "create a new one" (Add)?** If finding dominates, "+ Add" should be quieter (smaller / further right / inside the expanded section). If both are common, the current shape is right. Cold-read couldn't tell.

- **Was the asymmetry between chain/template/limit_schedule (eager-expanded) and account/rail (chevron + lazy-load) a deliberate "these small kinds don't need scale-shape" call?** Chains has 5 entries today, fine to expand; but a customer with 50 chains hits the same scrollability wall accounts/rails have already solved. Adding the same chevron pattern to all six kinds is a 1-file change in the card template — was there a reason not to?
