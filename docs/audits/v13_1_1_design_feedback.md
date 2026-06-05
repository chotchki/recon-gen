# recon-gen v13.1.1 — Visual / Design Review (upstream)

Design-quality review of the studio (app2) + dashboards Tailwind surface, v13.1.1. Method: axe-core + a bounding-box overlap probe over 31 routes (hard ratios/selectors), plus a multi-agent visual review of the screenshot bundle (100 adversarially-verified findings). Color *token values* are a downstream theme concern and omitted here; everything below is recon-gen template/component/layout structure.

## Top-line
The studio editor renders every entity as a fully-expanded card with no list hierarchy; the training surface stacks contradictory status banners and has a real layout overlap. Dashboards are functionally rich but visually noisy: prose-first KPI/landing layouts, missing-selection empty states that read as broken, and illegible high-cardinality charts.

## Color usage (token-agnostic, but a renderer concern)
- recon-gen uses the theme `accent` color as **small body text, links, table cells, active-tab text, AND KPI big-numbers**. That forces any brand accent to clear 4.5:1 on white — most mid-tone brand blues won't. **Suggest:** derive a separate AA-safe `accent-text`/`link` token (auto-darkened from the brand accent), and set **KPI values to near-black** (`text-slate-900`) rather than accent — this also fixes a hierarchy inversion where the primary value reads as secondary/clickable.
- **Amber is overloaded** with three meanings in one product: external-counterparty node category (diagrams), neutral descriptive copy (ETL), and caution (buttons/warnings). Reserve warning-amber for caution; move the node-category hue elsewhere. And **amber wants dark text on it, not white** (white-on-amber and amber-on-white both fail contrast; dark-on-amber passes).

## Studio — High
- **Long entity lists are unscannable walls.** The rail list is ~40,000px (65 full ~14-row cards), templates ~11,000px, in a non-stretched grid with no sticky toolbar, no search/filter, no live count, no grouping. **Introduce a shared list primitive:** sticky toolbar (search + category filter + count), group headers, and collapse each card to a one-line summary (name + key attributes) that expands on demand. Biggest single studio UX problem; also an a11y page-height problem.

## Studio — Med
- **Apply renders a green "done" banner stacked over a red "N plant(s) failed" banner** — contradictory state. Use a single-region, **worst-outcome-wins** banner.
- **The per-section Apply action bar renders on top of the final plant card** (post-session-start), hiding its header and orphaning inputs — a real stacking/layout break. Render the bar block-level below the last card with margin; each plant card should wrap header+body in its own padded container.
- **Per-card Edit/Delete/+Add are bare text links, not buttons; Delete has no destructive color** (same as Edit). Promote to ghost/outline buttons; give Delete a danger treatment.
- **No shared control vocabulary:** solid-primary, white-outline, solid-amber, bare text-links, and literal bracketed-text shortcuts (`[Select all]`/`[None]`) coexist; **no shared badge component** (state/type/id badges render in 3 idioms). Define one each.
- **Three side-by-side buttons in three fill languages with severity contradicting risk** — the genuinely destructive "rebuild from base" looks secondary while a benign action is solid-amber.
- **Diagram layer pills invert active/inactive emphasis** (active pill is the dim greyed one). Treat as a stepper: active = solid, upcoming = outline; never the disabled-grey look for an included step.
- **"Apply" button never shows a disabled state** even with no pending delta (dead clicks).
- **Section/accordion headers are under-differentiated** from the leaf content they control.
- **Value column too narrow** → underscore identifiers wrap mid-token; widen it and render key lists as wrapping chips.
- **Editor home has no `<h1>`/purpose/primary action** — content hidden in footer-like accordions.
- **L2 diagram is a hairball by default** (all categories on, ~140 edges, ~6px labels, 52 self-loops). Default to roles + control-hierarchy; opt into rails/templates/chains/self-loops progressively; add click-to-focus (the focus filter already exists). [A separate detailed diagram proposal is available.]

## Studio — Low
Ragged masonry rows (no equal-height); unset `—` placeholders styled as real values; content stranded at the top of full-height pages; numerics not `tabular-nums`; description prose merges into the key/value block; gated links rendered as prose not disabled anchors; transient success banners persist and pile up; an empty-state shown twice with mismatched wording; the failing-card "error planting" badge looks like a status label, not the hoverable error affordance the banner references; new-rail choice cards lack interactive affordance; back-link mixed into the tab strip; one workflow numbered as both "5 steps" and "3" in two components.

## Dashboards — High
- **High-cardinality stacked charts are illegible** — 20-25 categorical series in one mono-blue ramp, truncated legends, clipped axis labels. **Cap to top-N by magnitude + an "Other" bucket** at the query layer (one chart already demonstrates this rollup) and use a differentiated categorical palette.

## Dashboards — Med
- **Missing-selection empty states read as broken** — when a required selector is unset, the sheet shows the generic "No … match the current filters" instead of a prompt. Build one **empty-state primitive** that branches on selection ("Pick an account to begin").
- **Dense ledger tables are undifferentiated dumps** — no zebra, no header-row background, money columns not right-aligned/`tabular-nums`. Standardize one table component.
- **Prose-first layouts** — getting-started sheets open with ~250-word, ~1500px-wide paragraphs; KPI tiles put a multi-line description above the value (value orphaned at the bottom). Lead with a one-line lede + value-first KPI cards.
- **Default Sankey is a known-broken hairball used as the hero visual** — overlapping labels, near-invisible ribbons, clipped labels. Don't render the all-up tangle by default.
- **Self-undermining copy** — a sheet literally instructs users to "trust the data, not the control text" (a known dropdown-sync lag baked into permanent copy). Fix the sync; delete the apology.

## Dashboards — Low
Inconsistent KPI cards within a row (mixed severity glyphs); a slider shows its value twice (bubble + number box); single-category charts read as render errors; forced equal-height marooning a tiny KPI beside a dense table; the active nav item is quieter than the group labels.

## Accessibility (axe-core)
**No landmarks / `<main>`** (content outside landmarks app-wide), **missing `<h1>`** on several studio pages, **`nested-interactive`** (training controls), **`aria-allowed-role`** (entity lists, ~140 nodes), **unlabeled pickers** (`aria-input-field-name`), `empty-table-header`, one `select-name`. Plus 67 bounding-box overlaps (worst: a training control bar over list items + an input; editor card-description paragraphs over each other).

## Highest-leverage (systemic single fixes)
1. Single-region **worst-outcome-wins banner** (kills the green-over-red contradiction).
2. Fix the **Apply-bar / plant-card overlap** (a real layout break).
3. **Shared list primitive** (collapse-to-summary + toolbar) — fixes the giant walls + page-height a11y.
4. **Empty-state primitive** (the "reads as broken" blanks).
5. **Table component** (zebra + tabular money) + **top-N chart rollup**.
6. **Derive an AA-safe accent-text token + near-black KPI values**; reserve amber for caution (dark text on it).
7. Add **landmarks + `<h1>`s**.
