# AM.0.2 — Semantic-class → Tailwind utility mapping

Reference for AM.1 (editor screens) + AM.2 (diagram chrome + data panel) execution.
Per AM.0 locks **L1** (snap to standard 4px scale), **L2** (raw utilities, no `@apply`),
**L3** (data.css in scope), **L4** (SVG semantic CSS stays), **L5** (preserve theme
inheritance via `--color-*` utilities).

Source files (all in `src/recon_gen/common/html/`):

| File | Lines | Distinct classes | Scope |
|---|---:|---:|---|
| `_studio_assets/editor.css` | 475 | 32 | AM.1 |
| `_studio_assets/diagram.css` | 376 | 23 | AM.2 (chrome subset) |
| `_studio_assets/data.css` | 554 | 33 | AM.2 (data panel) |
| `assets/input.css` | 126 | n/a (`@theme`) | reference |
| `assets/output.css` | 19 KB | 155 utilities compiled | reference |

**Totals:** 88 semantic classes, of which **14 KEEP-AS-IS** (L4 SVG) and 74 migrate.
**Python-helper candidates:** 10. **Needs `@source` expansion:** ~95 distinct utility
strings touch classes not in today's compiled `output.css` (App2's render.py was the
only `@source` until now).

---

## Theme tokens

From `input.css` `@theme`; Tailwind v4 auto-derives `bg-*` / `text-*` / `border-*` /
`fill-*` / `stroke-*` / `ring-*` / `outline-*` per token. Per-L2 runtime override via
`_studio_routes.py::studio_theme_head`.

| `--color-*` | Utility prefix | Default | Notes |
|---|---|---|---|
| `accent` | `accent` | `#0a2740` | Primary brand; L2 override; supports `/N` opacity |
| `accent-fg` | `accent-fg` | `#ffffff` | Text on accent surface |
| `link-tint` | `link-tint` | `#e8eff9` | Pale-accent tint (hovers, anchor row) |
| `surface` | `surface` | `#ffffff` | Card background |
| `surface-bg` | `surface-bg` | `#f6f9fc` | Page / chrome background |
| `surface-border` | `surface-border` | `#e2e8f0` | Card + input border |
| `primary-fg` | `primary-fg` | `#1f2933` | Main text |
| `secondary-fg` | `secondary-fg` | `#4b5563` | Muted text / labels |
| `danger` | `danger` | `#c0392b` | Error / negative delta |
| `success` | `success` | `#218c5b` | Positive delta |
| `warning` | `warning` | `#c08824` | Warning |

**`--studio-*` aliases** (diagram.css 13-20) all bridge to `--color-*` — **DROP entirely**
during migration; raw `bg-accent` / `text-primary-fg` / `bg-surface-bg` utilities replace
them.

---

## editor.css → utilities (32 classes)

| Class (line) | Utility string | Snap notes |
|---|---|---|
| `#entity-list, .entity-list` (6) | `grid gap-4 p-4 [grid-template-columns:repeat(auto-fill,minmax(28rem,1fr))]` | arbitrary; needs @source |
| `body.home-page` (18) | `block h-auto min-h-screen` | `100vh` → `min-h-screen` |
| `.home-diagram` (24) | `bg-white border-b border-surface-border h-[50vh] min-h-96` | `24rem` → `min-h-96` |
| `.home-diagram iframe` (31) | `block w-full h-full border-0` | apply inline |
| `.home-entities` (38) | `px-4 pt-2 pb-8` | exact |
| `.home-section` (42) | `bg-white border border-surface-border rounded-md mb-3 overflow-hidden` | `0.4rem` → `rounded-md` (0.375) |
| `.home-section > summary` (50) | `cursor-pointer select-none px-4 py-2 font-semibold text-accent bg-surface-bg` | `0.6rem` → `py-2` |
| `.home-section > summary:hover` (59) | + `hover:bg-link-tint` | |
| `... .count` (63) | `text-secondary-fg font-normal ml-1` | |
| `... .home-section-link` (69) | `ml-2 text-accent no-underline font-normal text-sm hover:underline` | `0.85rem` → `text-sm` |
| `.home-section-loading` (81) | `p-4 text-secondary-fg italic m-0` | |
| `.entity-card.is-hidden-by-focus` (89) | `hidden` | JS toggle |
| `.focus-filter-indicator` (93) | `text-secondary-fg font-normal text-sm` | |
| `body.create-page` (100) | `block h-auto min-h-screen` | mirror of home-page |
| `.create-page-main` (105) | `grid grid-cols-1 lg:[grid-template-columns:22rem_1fr] gap-5 max-w-4xl mx-auto pt-6 px-4 pb-12` | breakpoint `56rem`→`lg:` (64rem) +8rem drift OK |
| `.create-intro` (118) | `bg-white border border-surface-border rounded-md px-5 py-4 text-sm leading-normal text-primary-fg` | `0.9rem`→`text-sm` |
| `.create-intro p` / `:last-child` (127) | `mb-3 last:mb-0` | |
| `.create-intro code` (133) | `bg-link-tint px-1 py-px rounded-sm text-xs` | `0.2rem`→`rounded-sm` |
| `.create-form-wrap` (139) | `bg-white border border-surface-border rounded-md p-5` | |
| `.create-form .form-actions` (145) | + `mt-4` | overrides base form-actions mt-2 |
| `.rail-subtype-picker` (154) | `grid grid-cols-1 sm:grid-cols-2 gap-3` | `36rem`→`sm:` (40rem) drift OK |
| `.rail-subtype-button` (164) | `flex flex-col gap-2 p-5 border-2 border-surface-border rounded-md bg-white text-primary-fg no-underline text-base cursor-pointer transition-colors` | `0.4rem`→`gap-2` |
| `.rail-subtype-button:hover` (178) | + `hover:border-accent hover:bg-link-tint` | |
| `... strong` (182) | `text-accent text-base font-semibold` | `1.05rem`→`text-base` |
| `... small` (186) | `text-secondary-fg text-sm leading-snug` | |
| `.entity-card-title` (193) | `cursor-pointer select-none hover:underline focus:outline-2 focus:outline-accent focus:outline-offset-2 focus:rounded-sm` | |
| **`.entity-card`** (206) ⭐ helper | `bg-white border border-surface-border rounded-md p-4 text-sm` | 15+ uses; `entity_card_classes()` |
| `.entity-card.editing` (214) | + `border-accent ring-2 ring-accent/15` | replaces rgba shadow with theme-aware ring |
| `.entity-card header` (219) | `flex items-baseline justify-between border-b border-surface-border pb-2 mb-3` | |
| `.entity-card h3` (228) | `m-0 text-base text-accent font-mono` | |
| `.entity-subtype-badge` (239) | `inline-block px-2 py-px text-xs font-medium font-sans text-accent bg-link-tint rounded-sm align-middle tracking-wide` | `0.05rem`→`py-px` |
| `.entity-card .edit-link, .cancel-link` (252) | `text-accent no-underline text-xs cursor-pointer hover:underline` | |
| `.entity-card .delete-link` (264) | `text-danger no-underline text-xs cursor-pointer hover:underline` | drop hardcoded `#c62828` |
| `.entity-card-actions` (270) | `flex gap-3` | |
| `.home-section-add` (276) | `ml-2 text-accent no-underline font-semibold text-sm hover:underline` | |
| `.entity-card dl` (287) | `m-0 grid grid-cols-[9rem_1fr] gap-x-3 gap-y-2` | arbitrary; needs @source |
| `.entity-card dt` (293) | `text-secondary-fg font-medium` | |
| `.entity-card dd` (297) | `m-0 break-words` | |
| **`.field-row`** (302) ⭐ helper | `flex flex-col gap-1 mb-3` | 30+ uses; `field_row_classes()` |
| `.field-row label` (309) | `font-semibold text-xs text-primary-fg` | `0.8rem`→`text-xs` |
| `.field-row label .required` (315) | `text-danger` | drop hardcoded `#c62828` |
| **`.field-row input/select/textarea`** (319) ⭐ helper | `px-2 py-2 border border-surface-border rounded-sm text-sm bg-white focus:outline-2 focus:outline-accent focus:-outline-offset-1 focus:border-accent` | 30+ uses; `field_input_classes()` |
| `.field-row textarea` (338) | + `resize-y min-h-16` | `4rem`→`min-h-16` |
| `.multi-select-group` (347) | `grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-1 px-2 py-2 border border-surface-border rounded-sm bg-white max-h-56 overflow-y-auto` | **user-decision 2 (2026-05-25)** — snap `28rem` UP to `sm:` (40rem); narrow-viewport gets 1-col slightly longer. Defer custom-breakpoint design pass. |
| `.multi-select-item` (363) | `flex items-center gap-2 font-normal text-sm cursor-pointer text-primary-fg` | |
| `.multi-select-groups` (383) | `flex flex-col gap-2` | |
| `.xor-group` (388) | `border border-surface-border rounded-sm px-3 py-2 bg-white` | |
| `.xor-group > legend` (394) | `text-xs font-semibold text-primary-fg px-2` | |
| `.xor-group.new` (400) | `border-dashed bg-surface-alt` | **user-decision 1 (2026-05-25)** — every surface theme-driven per L5. New `--color-surface-alt` token in input.css `@theme`. |
| `.xor-group.new > legend` (404) | `text-secondary-fg font-medium` | |
| `.xor-group > .multi-select-group` (408) | `border-0 px-0 py-1 max-h-36 bg-transparent` | nested override |
| `.multi-select-groups-empty` (414) | `text-sm text-secondary-fg px-2 py-2 border border-dashed border-surface-border rounded-sm bg-surface-alt` | |
| `.xor-group-list` (422) | `m-0 pl-4 text-sm` + `li: my-0.5` | |
| `.field-helper` (431) | `text-xs text-secondary-fg` | |
| `.field-error` (436) | `text-xs text-danger bg-red-50 px-2 py-1 rounded-sm` | error states theme-blind red |
| `.form-global-error` (444) | `bg-red-50 text-danger border border-red-200 px-3 py-2 rounded-sm mb-3 text-sm` | |
| `.form-actions` (454) | `flex items-center gap-3 mt-2` | |
| **`.form-actions button`** (461) ⭐ helper | `bg-accent text-accent-fg border border-accent px-4 py-2 rounded-sm cursor-pointer text-sm hover:opacity-85` | 6 uses; shared `primary_button_classes()` |

---

## diagram.css → utilities (23 classes; 14 KEEP-AS-IS SVG)

### Pure-deletion candidates

| Block (line) | Action |
|---|---|
| `:root { --studio-* }` (13-20) | **DELETE** — aliases for old names; raw `--color-*` utilities replace |
| `* { box-sizing: border-box }` (22-24) | **DELETE** — Tailwind preflight covers |

### Migrating chrome classes

| Class (line) | Utility string | Snap notes |
|---|---|---|
| `body` (26) | `m-0 font-sans bg-surface-bg text-primary-fg flex flex-col h-screen` | apply via `<body>` in render |
| `.studio-header` (37) | `flex items-center gap-4 px-4 py-2 border-b border-surface-border bg-white shrink-0` | |
| `.studio-header h1` (47) | `text-base m-0 font-semibold text-accent` | |
| `.studio-header .instance` (54) | `text-secondary-fg font-mono text-sm` | |
| `.studio-header .nav-link` (60) | `text-accent no-underline text-sm hover:underline` | |
| **`.studio-header .deploy-btn`** (71) ⭐ helper | `ml-auto px-3 py-2 text-sm font-medium bg-accent text-white border-0 rounded cursor-pointer hover:opacity-90 disabled:opacity-60 disabled:cursor-not-allowed` | `brightness(1.1)` → `hover:opacity-90`; shared `primary_button_classes()` |
| `.studio-header .deploy-status` (92) | `text-sm font-mono min-w-0` | base |
| `... --running` (98) | `text-secondary-fg` | |
| `... --ok` (102) | `text-success` | `#2f7a3a` → theme token |
| `... --halted / --error` (106) | `text-danger` | `#b1231f` → theme token |
| `.diagram-chrome` (111) | `flex items-center flex-wrap gap-x-5 gap-y-3 px-4 py-2 border-b border-surface-border bg-white shrink-0` | |
| `.diagram-chrome label` (122) | `inline-flex items-center gap-1.5 cursor-pointer text-sm select-none` | `0.35rem` → `gap-1.5` (0.375; close) |
| `.diagram-chrome .count` (131) | `text-secondary-fg text-xs` | |
| **`.diagram-chrome button`** (136) ⭐ helper | `bg-link-tint text-accent border border-surface-border px-3 py-1 rounded-sm cursor-pointer text-sm hover:bg-accent hover:text-white` | 5+ uses; `chrome_button_classes()` |
| `.diagram-chrome .status` (151) | `ml-auto text-secondary-fg text-xs font-mono` | |
| `.diagram-chrome .knob` (158) | `text-xs text-secondary-fg` | |
| `.diagram-chrome .knob a` (163) | `text-accent no-underline mr-2` | |
| `.diagram-chrome .knob a.active` (169) | + `font-semibold underline` | |
| `... .engine-link` (176) | `inline-block px-2 py-px border border-surface-border rounded-sm bg-white mr-1 no-underline font-medium text-xs` | `0.78rem` → `text-xs` |
| `... .engine-link:hover` (187) | + `hover:bg-link-tint` | |
| `... .engine-link.active` (190) | + `bg-accent text-white border-accent no-underline` | |
| `.diagram-viewport` (205) | `flex-1 overflow-hidden p-4 bg-white min-h-0 flex` | |
| `#diagram-target` (220) | `flex-1 min-h-0 min-w-0` | apply inline |
| `.diagram-chrome .chrome-section-label` (239) | `text-xs uppercase tracking-wider text-secondary-fg -mr-2` | `0.05em` → `tracking-wider` (exact) |
| `.diagram-chrome .layer-stepper` (290) | `inline-flex items-center gap-1 text-xs text-secondary-fg` | |
| `.diagram-chrome .layer-btn` (297) | `bg-white text-primary-fg border border-surface-border px-2 py-1 rounded-sm cursor-pointer text-xs font-medium` | |
| `... :hover` (307) | + `hover:bg-link-tint` | |
| `... .active` (310) | + `bg-accent text-white border-accent` | |
| `.diagram-chrome .chrome-coverage-toggle, .chrome-trainer-toggle` (353) | `inline-flex items-center gap-1.5 text-xs text-secondary-fg cursor-pointer select-none` | |

### KEEP-AS-IS per L4 (SVG, attribute-selector dependent)

| Class | Reason |
|---|---|
| `.topology-svg` cursor + sizing (198, 228) | SVG container; `cursor-grab`/`active:cursor-grabbing` migratable but co-located with SVG-only rules — cleanest to keep together |
| `.topology-svg.hide-role-internal/-external/-rail/-template g.node[data-kind=...]` (249-253) | Attribute-selector visibility toggles, JS-driven |
| `.topology-svg.hide-template g.edge[data-kind="template_member"]` (265) | same |
| `.topology-svg.hide-edge-label-rail_bundle/-self_loop/-chain g.edge[...] text` (271-273) | same |
| `.topology-svg.hide-control_parent g.edge[data-kind="control_parent"]` (282) | same |
| `.topology-svg.hide-edge-label-control_parent ... text` (285) | same |
| `.topology-svg g.node:hover` (321) | `filter: brightness + drop-shadow` on SVG; no utility path |
| `.topology-svg text` user-select (325) | SVG-only |
| `.topology-svg.coverage-on g.node[data-presence="no"] > polygon/ellipse/path` (336) | coverage overlay; `!important` + attribute selectors |
| `.topology-svg.coverage-on g.node[data-presence="no"] text` (342) | same |
| `.topology-svg.coverage-on g.edge[data-presence="no"] path/polygon` (345) | same |
| `.topology-svg.trainer-on g.node[data-trainer-kinds] > polygon/ellipse/path` (371) | trainer overlay; attribute-selector |

After migration, these surviving rules (~50 lines) belong in `_studio_assets/diagram-svg.css`
(renamed per AM.4).

---

## data.css → utilities (33 classes)

| Class (line) | Utility string | Snap notes |
|---|---|---|
| `body.data-page` (12) | **DELETE** — empty rule | |
| `.data-knobs` (18) | `flex flex-wrap items-center gap-3 px-4 py-2 border-b border-surface-border bg-white shrink-0` | |
| `.data-knobs .knob-placeholder` (29) | `text-sm text-secondary-fg italic` | |
| **`.data-knob`** (35) ⭐ helper | `flex items-center gap-2 px-2 py-1 border border-surface-border rounded-sm bg-surface-bg` | 5+ uses; `knob_wrapper_classes()` |
| `.data-knob-label` (45) | `text-sm text-secondary-fg font-medium` | |
| `.plant-toggle` (51) | `inline-flex items-center gap-1 text-sm cursor-pointer select-none` | |
| `.plant-toggle input[type=checkbox]` (60) | `m-0 cursor-pointer` (inline on input) | |
| `.data-knob-window` (68) | helper + `gap-1` override | |
| **`.window-input`** (72) ⭐ helper | `text-sm px-1 py-0.5 border border-surface-border rounded-sm bg-white text-inherit` | 4 uses w/ end-date-input + seed-input; `compact_input_classes()` |
| `.window-sep` (81) | `text-sm text-secondary-fg px-0.5` | |
| **`.window-reset`** (87) ⭐ helper | `appearance-none bg-white border border-surface-border rounded-sm px-2 py-0.5 text-sm cursor-pointer text-inherit hover:bg-surface-bg` | 5 uses w/ end-date-step/-reset, seed-roll/-clear; `ghost_button_classes()` |
| `.data-knob-end-date` (106) | helper + `gap-1` override | |
| `.end-date-step, .end-date-reset` (110) | `ghost_button_classes()` | shared |
| `.end-date-input` (127) | `compact_input_classes()` | shared |
| `.end-date-current, .seed-current` (136, 183) | `text-xs text-secondary-fg tabular-nums ml-1` | |
| `.data-knob-seed` (147) | helper + `gap-1` override | |
| `.seed-input` (151) | `compact_input_classes()` + `w-[9ch] tabular-nums` | arbitrary `w-[9ch]` (preserves char-count semantic) |
| `.seed-roll` (162) | `ghost_button_classes()` + `border-accent` | primary-action chip variant |
| `.seed-clear` (162) | `ghost_button_classes()` | shared |
| `.data-knob-scope` (193) | helper + `gap-2` override | |
| `.scope-radio` (197) | `inline-flex items-center gap-1 text-sm cursor-pointer select-none` | |
| `.scope-radio input[type=radio]` (206) | `m-0 cursor-pointer` | |
| `.data-knob-etl-hook` (216) | helper + `gap-2 flex-nowrap min-w-0` | |
| `.etl-hook-toggle` (222) | `m-0 cursor-pointer` | |
| `.etl-hook-command` (227) | `text-xs font-mono bg-surface-bg border border-surface-border rounded-sm px-1.5 py-px max-w-[32ch] overflow-hidden text-ellipsis whitespace-nowrap text-inherit` | arbitrary `max-w-[32ch]` |
| `.etl-hook-command--disabled` (241) | + `text-secondary-fg line-through opacity-65` | |
| `.etl-hook-command--missing` (247) | + `text-secondary-fg italic` | |
| `.timeline-header` (257) | `flex flex-col gap-1 mb-2 pb-2 border-b border-surface-border shrink-0` | |
| `.timeline-total` (267) | `text-sm font-semibold text-primary-fg` | |
| `.timeline-window-note` (273) | `text-xs font-normal text-secondary-fg ml-1` | |
| `.timeline-kinds` (280) | `text-xs text-secondary-fg tabular-nums` | |
| `.timeline-rows` (286) | `flex flex-col gap-0.5 flex-1 min-h-0 overflow-y-auto pr-1` | `0.125rem`→`gap-0.5`/`pr-1` |
| **`.timeline-day`** (299) ⭐ helper | `flex items-center gap-2 px-2 py-1 border border-surface-border rounded-sm bg-white cursor-pointer text-left font-inherit text-inherit transition-colors scroll-m-4 hover:bg-surface-bg hover:border-accent` | **90×/page**; `timeline_day_classes()` (highest leverage) |
| `.timeline-day--empty / --future` (323, 346) | override `py-px px-2 border-transparent text-secondary-fg` | `0.0625rem`=`1px` exact via `py-px` |
| `.timeline-day--empty/-future .timeline-day-date` (329, 352) | `font-normal text-xs` | `0.8125rem`→`text-xs` (sub-text-sm; -1px) |
| `.timeline-day--anchor` (365) | `border-accent border-2 px-1.5 py-1.5 bg-accent/6 font-semibold relative hover:bg-accent/10` | `color-mix(...6%)`→`bg-accent/6`; **needs @source** |
| `.timeline-day--anchor::before` (374) | **KEEP** as scoped CSS | `before:content + absolute + transform` reads worse than raw rule |
| `.timeline-day-date` (388) | `text-sm tabular-nums font-medium min-w-[6.5rem]` | arbitrary `min-w-[6.5rem]` |
| `.timeline-day-chips` (395) | `inline-flex flex-wrap gap-1` | |
| **`.timeline-chip`** (401) ⭐ helper base | `text-xs font-semibold tracking-wide px-1.5 py-0.5 rounded-sm bg-surface-bg text-secondary-fg border border-surface-border` | 4 variants; `timeline_chip_base_classes()` |
| `.timeline-chip--drift` (414) | + `bg-accent/12 text-accent border-accent/25` | needs @source |
| `.timeline-chip--overdraft, --limit_breach` (420) | + `bg-danger/12 text-danger border-danger/25` | needs @source |
| `.timeline-chip--stuck_pending, --stuck_unbundled` (427) | + `bg-warning/12 text-warning border-warning/25` | needs @source |
| `.timeline-chip--supersession` (434) | + `bg-success/12 text-success border-success/25` | needs @source |
| `.data-main` (440) | `grid grid-cols-[minmax(20rem,1fr)_minmax(24rem,1.5fr)] gap-4 p-4 flex-1 min-h-0 overflow-hidden` | arbitrary grid-cols |
| `.data-timeline, .data-training` (450) | `bg-white border border-surface-border rounded-sm p-4 overflow-y-auto` | |
| `.data-timeline` (462) | + `flex flex-col overflow-hidden` | inner `.timeline-rows` owns scroll |
| `.data-empty` (468) | `text-secondary-fg italic m-0` | |
| `.data-training__heading` (478) | `text-base font-semibold m-0 mb-1 text-primary-fg` | `0.95rem`→`text-base` |
| `.data-training__intro` (485) | `text-sm text-secondary-fg m-0 mb-3` | `0.8125rem`→`text-sm` |
| `.data-training__list` (491) | `list-none m-0 p-0 flex flex-col gap-3` | |
| `.data-training__entry` (500) | `border border-surface-border rounded-sm px-3 py-2.5 bg-surface-bg` | `0.625rem`→`py-2.5` |
| `.data-training__entry-head` (507) | `flex items-baseline gap-2 mb-1.5` | `0.375rem`→`mb-1.5` (exact) |
| `.data-training__kind` (514) | `font-mono text-xs uppercase tracking-wide px-1.5 py-px border border-surface-border rounded-sm text-secondary-fg bg-surface-bg` | |
| `.data-training__title` (526) | `text-sm font-semibold m-0 text-primary-fg` | |
| `.data-training__should` (533) | `text-sm m-0 mb-1.5 text-primary-fg` | |
| `.data-training__action` (539) | `text-sm m-0 mb-2 text-primary-fg` | |
| `.data-training__link` (545) | `text-sm text-accent no-underline hover:underline focus:underline` | |

---

## Python-helper candidates

Per L2: helpers return utility strings, NEVER `@apply` classes. Create in
`src/recon_gen/common/html/_studio_assets/tw_classes.py` (new module) or absorb into
`_studio_routes.py` if scope stays small. **user-decision 3 (2026-05-25)**: make all
10 helpers — call-count threshold doesn't matter, the helper-pattern reads cleaner
at 15× than at 5× either way.

**L2.a guardrail (user-decision 3, 2026-05-25)** — helpers MUST stay single-string-
returning + zero-parameter (or, rarely, one bool). A helper like
`card_classes(variant="editing", disabled=True) -> str` that branches internally is
reinventing `@apply`'s component-class problem in Python. **Compose at the call
site instead:** `f'{entity_card_classes()} {"border-accent ring-2 ring-accent/15" if editing else ""}'`. If any helper below grows beyond one bool param OR switches on enum-like state during AM.1/AM.2 execution, that's a smell — break it up or move the state into the caller. Watch for it.

| Helper | Utility string | Used by | Count |
|---|---|---|---:|
| `entity_card_classes()` | `bg-white border border-surface-border rounded-md p-4 text-sm` | editor read-card render | 15+ |
| `field_row_classes()` | `flex flex-col gap-1 mb-3` | every form field wrapper | 30+ |
| `field_input_classes()` | `px-2 py-2 border border-surface-border rounded-sm text-sm bg-white focus:outline-2 focus:outline-accent focus:-outline-offset-1 focus:border-accent` | every `<input>/<select>/<textarea>` | 30+ |
| `primary_button_classes()` | `bg-accent text-accent-fg border border-accent px-4 py-2 rounded-sm cursor-pointer text-sm hover:opacity-85` | form submits + deploy-btn | 6 |
| `chrome_button_classes()` | `bg-link-tint text-accent border border-surface-border px-3 py-1 rounded-sm cursor-pointer text-sm hover:bg-accent hover:text-white` | diagram-chrome Reset + similar | 5 |
| `ghost_button_classes()` | `appearance-none bg-white border border-surface-border rounded-sm px-2 py-0.5 text-sm cursor-pointer text-inherit hover:bg-surface-bg` | window-reset / end-date-step / seed-roll / seed-clear | 5 |
| `compact_input_classes()` | `text-sm px-1 py-0.5 border border-surface-border rounded-sm bg-white text-inherit` | window-input / end-date-input / seed-input | 4 |
| `knob_wrapper_classes()` | `flex items-center gap-2 px-2 py-1 border border-surface-border rounded-sm bg-surface-bg` | every `.data-knob` wrapper | 5+ |
| `timeline_day_classes()` | `flex items-center gap-2 px-2 py-1 border border-surface-border rounded-sm bg-white cursor-pointer text-left font-inherit text-inherit transition-colors scroll-m-4 hover:bg-surface-bg hover:border-accent` | every timeline row | 90×/page |
| `timeline_chip_base_classes()` | `text-xs font-semibold tracking-wide px-1.5 py-0.5 rounded-sm border` | base for 4 kind variants | 4 |

Absorbs ~190+ class-string occurrences across studio renderers.

---

## Snap-decision log

Cases where L1 required judgment beyond "round to nearest":

| Source pattern | Choice | Rationale |
|---|---|---|
| `0.4rem` border-radius (12+ uses) | `rounded-md` (0.375) | -0.4px; consistent w/ App2 |
| `0.4rem` gap | `gap-2` (0.5) | +1.6px; cleaner than `gap-1.5` |
| `0.6rem` padding (`.home-section > summary`) | `py-2 px-4` | preserves 1rem horiz; -1.6px vert |
| `0.85rem` font-size | `text-sm` (0.875) | +0.4px |
| `0.85rem` padding | `px-3` (0.75) | -1.6px |
| `0.7rem` font-size | `text-xs` (0.75) | +0.8px |
| `0.6875rem` font-size (timeline-chip, training__kind) | `text-xs` (0.75) | +1px |
| `0.8125rem` font-size (training__intro, future-day-date) | `text-sm` (0.875) | +1px |
| `0.78rem` font-size | `text-xs` (0.75) | -0.5px |
| `0.95rem` font-size (training__heading) | `text-base` (1) | +0.8px |
| `1.05rem` font-size (rail-subtype-button strong) | `text-base` (1) | -0.8px |
| `0.3125rem` / `0.4375rem` padding (anchor row) | `py-1.5 px-1.5` (0.375 both) | sub-pixel; total height stays ±1px |
| `0.0625rem` padding (future/empty days) | `py-px` (1px) | exact (0.0625rem = 1px) |
| `color-mix(... 6%, white)` | `bg-accent/6` | Tailwind v4 `/N` arbitrary opacity |
| `color-mix(... 12%, white)` | `bg-accent/12` | same |
| `color-mix(... 25%, transparent)` | `border-accent/25` | direct |
| `box-shadow: 0 0 0 2px rgba(31,78,121,.15)` (entity-card.editing) | `border-accent ring-2 ring-accent/15` | theme-aware; rgba was hardcoded accent anyway |
| `filter: brightness(1.1)` hover (deploy-btn) | `hover:opacity-90` | semantic-equivalent; theme-aware |
| `filter: brightness(1.05) drop-shadow(...)` (SVG node) | **KEEP** | SVG only |
| `28rem` breakpoint (multi-select 2-col) | `sm:` (40rem) | **user-decision 2** — snap UP; defer custom-breakpoint design pass |
| `36rem` breakpoint (rail-subtype-picker) | `sm:` (40rem) | +4rem drift OK |
| `56rem` breakpoint (create-page-main) | `lg:` (64rem) | +8rem drift OK; alt `min-[56rem]:` |
| `#f9fafb` (xor-group.new bg) | `bg-surface-alt` | **user-decision 1** — theme-driven via new `--color-surface-alt` token in input.css `@theme`; per-L2 override deferred until needed |
| `#fef2f2` (error bg) | `bg-red-50` | error states conventionally red across themes |
| `#fecaca` (error border) | `border-red-200` | same |
| `#c62828` (required asterisk) | `text-danger` | drop hardcoded, theme token |
| `0.02em` letter-spacing | `tracking-wide` (0.025em) | close |
| `0.05em` letter-spacing | `tracking-wider` (0.05em) | exact |
| `4px` border-radius (deploy-btn) | `rounded` (4px) | exact |
| `9ch` / `32ch` / `6.5rem` widths | arbitrary `w-[9ch]` / `max-w-[32ch]` / `min-w-[6.5rem]` | preserves char-count + date-column semantics |

---

## "Needs `@source` expansion" prereq

Today's `output.css` (155 classes) was compiled from `render.py` + `__main__.py` +
`bootstrap.js`. Studio migration adds ~95 distinct utilities not in today's output.
**Migration prereq (AM.0.3 or first step of AM.1):** add to `input.css`:

```css
@source "../_studio_routes.py";
@source "../_studio_editor_routes.py";
```

Then rebuild via pytailwindcss. Utility families touched (representative, not exhaustive):

- **Layout/sizing:** `h-screen`, `h-auto`, `min-h-screen`, `min-h-96`, `min-h-16`, `min-h-0`, `min-w-0`, `w-full`, `w-[9ch]`, `max-w-4xl`, `max-h-36`, `max-h-56`, `max-w-[32ch]`, `min-w-[6.5rem]`, `min-h-[24rem]`, `h-[50vh]`, `flex-1`, `flex-nowrap`, `shrink-0`
- **Gap:** `gap-x-3`, `gap-x-5`, `gap-y-1`, `gap-y-3`, `gap-0.5`, `gap-1.5`, `gap-3`, `gap-5`
- **Spacing:** `px-1`, `px-1.5`, `px-2.5`, `px-3`, `px-5`, `py-0.5`, `py-px`, `py-1.5`, `py-2.5`, `pl-4`, `pr-1`, `pt-2`, `pt-6`, `pb-2`, `pb-8`, `pb-12`, `mb-1.5`, `mb-3`, `ml-1`, `ml-2`, `ml-auto`, `mr-1`, `mr-2`, `-mr-2`, `mt-2`, `mt-4`, `my-0.5`, `m-0`, `p-0`, `px-0`, `scroll-m-4`
- **Borders/radius:** `border-0`, `border-2`, `border-b`, `border-dashed`, `border-transparent`, `border-accent`, `border-accent/25`, `border-danger/25`, `border-warning/25`, `border-success/25`, `border-red-200`, `rounded`, `rounded-sm`, `rounded-md`
- **Background opacity:** `bg-accent/6`, `bg-accent/10`, `bg-accent/12`, `bg-danger/12`, `bg-warning/12`, `bg-success/12`, `bg-surface-alt`, `bg-red-50`, `bg-transparent`
- **Type:** `text-base`, `text-inherit`, `text-ellipsis`, `font-inherit`, `font-mono`, `font-normal`, `font-medium`, `font-semibold`, `tracking-wide`, `tracking-wider`, `leading-snug`, `leading-normal`, `align-middle`, `whitespace-nowrap`, `break-words`, `last:mb-0`, `list-none`
- **State:** `hover:opacity-85`, `hover:opacity-90`, `hover:bg-link-tint`, `hover:bg-surface-bg`, `hover:bg-accent`, `hover:border-accent`, `hover:text-white`, `disabled:opacity-60`, `disabled:cursor-not-allowed`, `focus:outline-2`, `focus:outline-accent`, `focus:-outline-offset-1`, `focus:rounded-sm`, `focus:underline`, `active:cursor-grabbing`, `cursor-grab`
- **Misc:** `appearance-none`, `text-left`, `inline-block`, `inline-flex`, `flex-wrap`, `tabular-nums`, `overflow-hidden`, `overflow-y-auto`, `select-none`, `cursor-pointer`, `cursor-not-allowed`, `hidden`, `opacity-65`, `opacity-85`, `line-through`, `italic`, `block`
- **Responsive:** `sm:grid-cols-2`, `min-[28rem]:grid-cols-2`, `lg:[grid-template-columns:22rem_1fr]`
- **Arbitrary:** `[grid-template-columns:repeat(auto-fill,minmax(28rem,1fr))]`, `grid-cols-[minmax(20rem,1fr)_minmax(24rem,1.5fr)]`, `grid-cols-[9rem_1fr]`

---

## Out-of-scope (preserved as-is)

| Item | Reason |
|---|---|
| `widgets-theme.css` (210 lines) | Vendored 3rd-party theming (Tom Select / Flatpickr / noUiSlider / ctxmenu) |
| `.topology-svg` + `g.node` + `g.edge` rules (~30 across diagram.css) | L4; SVG attribute-selector-driven (`[data-kind]`, `[data-presence]`, `[data-trainer-kinds]`) |
| `.topology-svg.hide-*` family (~12 rules) | L4; JS-driven visibility toggles |
| `.topology-svg.coverage-on g.node[data-presence="no"]` (~6) | L4; SVG overlay |
| `.topology-svg.trainer-on g.node[data-trainer-kinds]` (~2) | L4; SVG overlay |
| `.timeline-day--anchor::before` pseudo | `before:content + absolute + transform` reads worse as utilities; KEEP scoped CSS |
| `visual-loading` + `skeleton-block` + `@keyframes` (input.css 95-125) | Already in input.css; out of scope |
| `:root { --studio-* }` (diagram.css 13-20) | **DELETE** — aliases for old names |
| `* { box-sizing: border-box }` (diagram.css 22-24) | **DELETE** — Tailwind preflight |
| `body.data-page { }` empty rule (data.css 12-16) | **DELETE** |

**Net reduction:** 475 + 376 + 554 = **1,405 lines → ~60 lines surviving CSS** (SVG +
anchor `::before`) plus the 10 Python helpers in `tw_classes.py`. Migrating
~74 chrome classes lifts entirely into inline utility strings.
