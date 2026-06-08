# Phase O — Markdown to QuickSight Help Sheet Renderer (Design Doc)

> **Status.** Design doc landed via background agent during N.3 work.
> Open questions in §5 need user resolution before O.0 implementation
> kicks off.

---

## 1. What a "help sheet" is in this codebase

Today every shipped dashboard ends with one canonical "extra" sheet:
the **App Info / "Info" sheet**, built by `populate_app_info_sheet()`
in `src/quicksight_gen/common/sheets/app_info.py`. Each app
(`l1_dashboard`, `l2_flow_tracing`, `investigation`, `executives`)
constructs an empty `Sheet(sheet_id=SHEET_APP_INFO, name=APP_INFO_SHEET_NAME, ...)`,
appends it last via `analysis.add_sheet(...)`, then hands it to the
populator. The populator owns the layout (a `_TABLE_HEIGHT=12` row
holding KPI + table, then a `_TEXT_HEIGHT=6` row holding a deploy
stamp text box, all on the 36-col grid).

A **help sheet** is a sibling: an extra empty sheet appended at the
end (likely *just before* App Info, since App Info should remain the
literal canary), populated from a markdown source. The visual surface
is one or more `TextBox` nodes carrying `rt.text_box(...)` XML — no
datasets needed (text-only, no SQL).

The key composition pattern from `app_info.py` is
`populate_<thing>_sheet(cfg, sheet, *, ..., theme: ThemePreset | None = None)`
taking an empty Sheet, computing its accent color, and using
`sheet.layout.row(height=H).add_text_box(TextBox(text_box_id=..., content=rt.text_box(...)))`.
The help renderer should follow that signature shape exactly.

## 2. The markdown-to-QS rendering surface

`src/quicksight_gen/common/rich_text.py` is the **complete** primitive
set today. The XML dialect QuickSight's `SheetTextBox.Content` accepts
is undocumented but round-trip-confirmed:

- `<text-box>` root (`rt.text_box(*parts)`)
- `<inline font-size="36px" color="#hex">` runs (`rt.inline`,
  `rt.heading=32px`, `rt.subheading=20px`)
- `<br/>` line breaks (`rt.BR`)
- `<ul><li class="ql-indent-0">...` bullets (`rt.bullets`,
  `rt.bullets_raw` for inline-styled items)
- `<a href="..." target="_self">` links (`rt.link`)
- XML-escaped body text (`rt.body`)

The data shape on the QS side is
`SheetTextBox(SheetTextBoxId: str, Content: str)`
(`src/quicksight_gen/common/models.py:1082`). The tree wrapper
`TextBox` (`src/quicksight_gen/common/tree/text_boxes.py`) is a thin
shell exposing `text_box_id` + `content`.

**Markdown features that map cleanly:**

| Markdown | Maps to | Notes |
|---|---|---|
| `#`, `##`, `###` headings | `rt.heading` (32px), `rt.subheading` (20px), `rt.inline(...,font_size="16px")` | Need a sized "h3" tier; verify QS accepts arbitrary px sizes |
| `**bold**` | `rt.inline(text)` with no styling — **gap: no bold tag known** | Likely needs `<strong>` round-trip experiment (see §5) |
| `*italic*` | Same gap as bold | Needs experimental confirmation |
| `` `inline code` `` | `rt.inline(text, color=<muted-hex>)` as fallback | No `<code>` style known; mono font almost certainly unavailable |
| `- bullet` | `rt.bullets([...])` or `rt.bullets_raw([...])` | Top-level only; **no nested bullets** — every `<li>` must be `ql-indent-0` |
| `[text](url)` | `rt.link(text, href)` | Direct |
| Plain paragraph | `rt.body(text) + rt.BR + rt.BR` | Per `app_info.py` and L1 patterns |

**Markdown features that don't map:**

- **Tables** — no `<table>` known to the QS XML dialect.
- **Fenced code blocks** — no `<pre>` / `<code>` block style. Best
  fallback: render line-by-line in a `rt.inline(..., color=<muted>)`
  plus `<br/>` per line, or skip with a `[code block omitted]`
  placeholder.
- **Images** (`![alt](url)`) — `SheetTextBox.Content` has no `<img>`
  known; even if it did, the handbook screenshots live as PNGs at
  relative mkdocs paths that aren't web-reachable from the deployed
  dashboard. Strip with `[image: alt]` placeholder, or skip silently.
- **HTML blocks** (the handbook uses `<div class="snb-card-grid">`,
  `<div class="snb-hero">`) — must be stripped or routed to a "the
  full handbook is online" link.
- **Nested lists** — top-level only; flatten or indicate nesting with
  leading dashes in text.
- **Blockquotes** — render as `rt.inline(text, color=accent)`
  italic-substitute.
- **Horizontal rules** — substitute with double `<br/><br/>`.

Existing call sites for reference:
`src/quicksight_gen/apps/executives/app.py:135-167` (`_section_box_content`
helper) and `src/quicksight_gen/apps/l1_dashboard/app.py:433-505`
(Getting Started + Drift "Internal Accounts in Scope" patterns).

## 3. The handbook-as-input side

The existing handbook lives at `src/quicksight_gen/docs/handbook/` (5
files, 132–237 lines each, ~885 lines total). They are
**mkdocs-flavored markdown** with significant non-portable content:

- Each opens with a `<div class="snb-hero">` containing an `<img>`
  and `<h2>`.
- Each uses `<div class="snb-card-grid">` of `<a class="snb-card">`
  for sheet inventories.
- Embedded `![screenshot](../screenshots/l1/...)` references that
  depend on the mkdocs site serving the relative path.
- Internal cross-links like `[L1 Invariants](../L1_Invariants.md)`
  that resolve to `.html` after mkdocs build but are dead links from
  inside QS.

**Recommendation: do NOT directly ingest the existing handbook
markdown.** Two reasons:

1. The mkdocs files are *web-shaped* (HTML divs, screenshots,
   relative links). A QS-rendered version would either silently strip
   30%+ of the visual content or render garbled XML/missing-image
   placeholders.
2. The renderable surface in QS is far smaller (no tables, no
   images, no nested lists). Authors should write content knowing
   the constraint, not have it stripped after the fact.

**Better:** introduce a **per-dashboard markdown file specifically
authored for in-dashboard rendering**, living at
`src/quicksight_gen/docs/help/<app_segment>.md` (mirroring the
`app_segment` convention from
`build_liveness_dataset(cfg, *, app_segment="l1"|"inv"|"exec"|"l2ft")`).
These files should:

- Be a tight subset (~50–150 lines) — what an analyst opening the
  dashboard for the first time needs in front of them, not the full
  mkdocs reference.
- Cross-link out to the full handbook with `[Full handbook](https://...)`
  at the top so depth lives in the website.
- Be linted at generate-time against the supported markdown subset
  (warn or fail on unsupported features).

This also cleanly answers a Phase O / mapping.yaml-replacement
question: the help sheet content is *the* place per-instance
vocabulary substitution should land (e.g., institution name from L2
description). The renderer takes a markdown source + an L2 instance
and Jinja-renders the markdown first, then converts to XML.

## 4. The technical proposal

### 4.a Module layout

```
src/quicksight_gen/common/
├── sheets/
│   ├── app_info.py        (existing)
│   └── help.py            (NEW)
└── markdown_to_qs.py      (NEW)
```

**`common/markdown_to_qs.py`** — pure conversion. Public surface:

```python
def render_markdown_to_text_box_content(
    md_source: str,
    *,
    accent: str,
    on_unsupported: Literal["strip", "placeholder", "raise"] = "placeholder",
) -> str:
    """Returns a single rt.text_box(...) XML string ready for SheetTextBox.Content."""

def render_markdown_to_text_boxes(
    md_source: str,
    *,
    accent: str,
    split_on_heading_level: int | None = 2,
    on_unsupported: Literal["strip", "placeholder", "raise"] = "placeholder",
) -> list[tuple[str, str]]:
    """Returns [(suggested_text_box_id, content_xml), ...] split at the given heading level."""
```

The implementation is a **small hand-rolled markdown subset parser**
rather than pulling in a dependency. The handbook-targeted subset is
narrow (paragraphs, headings 1–3, bullets, links, inline emphasis,
code spans) and the failure modes need to be deterministic — a
heavyweight parser like `markdown` or `mistune` would generate HTML
we'd then have to translate back to QS's XML dialect, doubling the
surface area for mismatch. A line-by-line scanner of ~150 LOC mirrors
the `rich_text.py` minimalism and stays inside the no-new-runtime-deps
preference visible in `pyproject.toml` (only `click`, `pyyaml`,
`graphviz` listed).

If hand-rolling proves brittle, the lowest-cost dependency is
`markdown-it-py` (pure Python, no extras, well-typed) used in
**token mode** so we walk the token stream directly to QS XML
without going through HTML.

**`common/sheets/help.py`** — analogous to `app_info.py`:

```python
HELP_SHEET_NAME = "Help"
HELP_SHEET_TITLE = "Dashboard Help"
HELP_SHEET_DESCRIPTION = "Inline reference for this dashboard's sheets, drills, and filters."

def populate_help_sheet(
    cfg: Config,
    sheet: Sheet,
    *,
    markdown_source: str | Path,
    theme: ThemePreset,
    l2_instance: L2Instance | None = None,  # for vocabulary substitution
) -> None:
    """Populate the help sheet from a markdown source.

    Reads markdown_source (str = literal markdown, Path = file path),
    optionally Jinja-renders against l2_instance for vocabulary
    substitution, splits into sections at H2 boundaries, and emits
    one TextBox per section stacked in vertical rows.
    """
```

### 4.b Sheet sizing and pagination

The 36-col grid scales fine width-wise; the constraint is **vertical
scroll inside a TextBox vs across multiple TextBoxes**. Existing
usage (`l1_dashboard/app.py:433`) uses heights of 6–8 grid rows per
text box. QS does scroll within a TextBox, but long content stuffed
in one box is awkward to navigate.

Recommendation: **split on H2** (or whatever level the renderer is
configured for). One TextBox per section, each in its own
`sheet.layout.row(height=H)` where H is estimated from the rendered
content (a rough heuristic: `max(6, lines_of_body // 4 + bullet_count // 2)`).
The estimator can be conservative — overshooting wastes whitespace;
undershooting causes the TextBox to scroll, which is fine but uglier.

For v1, just use a constant `_HELP_ROW_HEIGHT = 12` per section and
move on. Tune later.

### 4.c App-side wiring

Each app's `build_*_app(cfg, *, l2_instance=None)` adds a
`Sheet(sheet_id=SHEET_HELP, ...)` immediately before App Info, then
calls:

```python
populate_help_sheet(
    cfg, help_sheet,
    markdown_source=Path(__file__).parent.parent.parent / "docs/help/l1.md",
    theme=theme,
    l2_instance=l2_instance,
)
```

The helper itself is app-agnostic. The per-app cost is one
`analysis.add_sheet(...)` + one populator call (~10 lines per app).

## 5. Open design questions

Flagged for the user to resolve before implementation:

1. **Markdown subset scope (v1).** Recommended in-scope: paragraphs,
   H1/H2/H3, bulleted lists (top-level only), `[text](url)` links,
   inline emphasis (if QS supports `<strong>`/`<em>` — needs
   experimental round-trip), inline code as colored span. Recommended
   deferred: tables, fenced code blocks, images, nested lists,
   blockquotes, HTML passthrough. **Confirm or override.**

2. **Source location.** Recommendation above is
   `src/quicksight_gen/docs/help/<segment>.md` — new files
   specifically authored for in-dashboard rendering, NOT the existing
   mkdocs handbook. If you want the existing handbook to be the
   source of truth, the renderer needs a much more aggressive
   sanitizer pass (strip `<div>` blocks, strip images, rewrite
   cross-links). **Confirm: new authored files vs sanitize existing
   handbook?**

3. **Rendering granularity.** One big text box per sheet vs
   split-by-H2 into multiple boxes. Recommendation: split-by-H2 —
   keeps each box at a navigable size, lets the layout estimator
   pick row heights per section, mirrors how the existing app sheets
   compose multiple side-by-side TextBoxes. **Confirm.**

4. **Image handling.** Today's handbook is screenshot-heavy. QS XML
   has no known `<img>` tag, and even if it did, the screenshots
   live at mkdocs-relative paths not reachable from a deployed
   dashboard. Recommendation: strip with a
   `[Screenshot: <alt> — see online docs]` placeholder + a link to
   the published mkdocs page. Could revisit if QS exposes
   embedded-image support. **Confirm strip-and-link-out.**

5. **Cross-dashboard help vs per-sheet help fragments.** Two
   interpretations of the user's brief:
   - **(a) One Help sheet per dashboard with that dashboard's full
     handbook.** Matches the audit doc quote literally ("a sheet at
     the end of each dashboard and load the documentation").
     Recommendation: ship this in v1.
   - **(b) Per-sheet help fragments**, where each operational sheet
     (Drift, Overdraft, etc.) gets a top-of-sheet help TextBox loaded
     from a per-sheet markdown fragment. This already exists
     informally — `_populate_drift_sheet` has an "Internal Accounts
     in Scope" TextBox at the top. A markdown-driven version would
     generalize that.

   These aren't mutually exclusive but they're different scopes.
   Recommendation: ship (a) first; (b) is a Phase O follow-on once
   the renderer is proven and the per-sheet markdown authoring
   discipline exists.

6. **Vocabulary substitution.** Should the markdown go through Jinja
   (or similar) against the L2 instance before rendering, so help
   prose can say "the {{ institution_name }} dashboard..." instead
   of hardcoded text? This dovetails with the broader Phase O goal
   of replacing `mapping.yaml`. Recommendation: yes, lightweight
   `str.format_map` or Jinja with strict-undefined, with a small
   documented context object (`institution`, `accent_role_terms`,
   etc.) — but keep v1 stupid (just `str.format_map` against
   `{"institution_name": l2_instance.description.split('.')[0]}` or
   similar).

7. **Strict mode vs lenient mode.** When the parser hits an
   unsupported feature, should it raise (strict, fails fast at
   generate time) or render a placeholder (lenient, dashboard ships
   with `[image: foo]` stubs)? Recommendation: parametric
   `on_unsupported`, default to `"placeholder"` for v1 + a
   generate-time CLI lint subcommand that runs the parser in
   `"raise"` mode against the help sources.

8. **Boldness of in-XML `<strong>` / `<em>` experiment.** Before
   scoping bold/italic into v1, someone needs to round-trip a
   UI-authored TextBox containing bold text and confirm what tags QS
   uses. **This is the only blocking unknown.** If `<strong>` works,
   bold/italic is in v1; if not, both defer.

## 6. Order-of-operations recommendation

This wants its **own sub-phase**, not a single substep, because:

- It's two new files (`markdown_to_qs.py`, `sheets/help.py`) plus
  per-app wiring (4 apps × ~10 LOC) plus per-app authored markdown
  (4 × ~100-line files) plus tests for each layer.
- The `<strong>`/`<em>` round-trip experiment is a hard prerequisite
  for the bold/italic decision and shouldn't block
  markdown-without-emphasis from shipping.
- The vocabulary-substitution layer (open question 6) ties into the
  broader `mapping.yaml` replacement that's Phase O's main thrust,
  and probably wants to share infrastructure.

Suggested decomposition under Phase O:

- **O.0 — Help sheet renderer (PREREQUISITE for O.1 / O.2 vocabulary
  work).**
  - O.0.a — Round-trip a UI-authored TextBox with bold + italic;
    document the tag set; update the supported-subset table.
  - O.0.b — `common/markdown_to_qs.py` with hand-rolled scanner for
    the agreed subset; unit tests covering every supported +
    every-rejected feature.
  - O.0.c — `common/sheets/help.py` mirroring `app_info.py` shape;
    unit test populating a fake Sheet.
  - O.0.d — Author `src/quicksight_gen/docs/help/l1.md` (~100 lines,
    distilled from `handbook/l1.md`).
  - O.0.e — Wire the help sheet into L1 (one app first to prove the
    pattern).
  - O.0.f — Aurora deploy verification: render the L1 dashboard,
    open the Help sheet, eyeball the layout. Iterate.
  - O.0.g — Author the other three help markdowns (l2ft, inv, exec)
    and wire them.
  - O.0.h — Generate-time lint subcommand: `quicksight-gen lint
    help-sources` walks every help markdown and runs the parser in
    `on_unsupported="raise"` mode.

This keeps Help-sheet rendering as a **clean prerequisite** that can
ship before O.1's vocabulary work; O.1 can then layer Jinja-style
substitution on top of an already-rendering markdown pipeline rather
than building both at once.

---

## Read uncertainty / things to verify before implementation

- The exact set of inline tags QS accepts is undocumented.
  `<strong>`/`<em>`/`<code>` need round-trip confirmation before the
  v1 markdown subset is finalized. The header in `rich_text.py` is
  honest about this: "undocumented — confirmed by round-tripping a
  UI-authored text box."
- Whether `SheetTextBox` content scrolls vertically inside a small
  grid slot vs gets clipped — I haven't verified. The existing usage
  keeps boxes short (height 6–8) so this hasn't been stressed. A
  help sheet with a 30-row text box is novel.
- The mkdocs site is published (per the README and `pyproject.toml`
  `[project.urls]`), so cross-linking from QS Help to the full
  mkdocs handbook via `https://chotchki.github.io/Quicksight-Generator/`
  is viable. **The user should confirm that's the intended
  cross-link target.**
- I did not verify whether `<a href="...">` actually opens in a new
  browser tab from inside QS — `rich_text.link()` uses
  `target="_self"` which would replace the dashboard. For the "see
  full handbook online" use case, you'd want `target="_blank"` (and
  to confirm QS honors it).

### Critical Files for Implementation

- `src/quicksight_gen/common/sheets/app_info.py` — sibling pattern to
  copy
- `src/quicksight_gen/common/rich_text.py` — XML primitives to extend
  if `<strong>`/`<em>` round-trip
- `src/quicksight_gen/common/tree/text_boxes.py` — TextBox node
  wrapper consumed by the populator
- `src/quicksight_gen/apps/l1_dashboard/app.py` — first app to wire
  the help sheet into (lines 2120–2130 show where App Info is
  appended; help goes immediately above)
- `src/quicksight_gen/docs/handbook/l1.md` — source material to
  distill into the per-dashboard help markdown
