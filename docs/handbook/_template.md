# Handbook page template

**Internal — not rendered to operators.** This file is the shape contract
every `docs/handbook/<app>/<sheet>.md` page follows. It exists so the
CN.4 fan-out agents author 30 pages with consistent voice, depth, and
factual rigor.

Each handbook page is the prose an operator reads after clicking the
`?` button on a dashboard sheet. The reader is a finance / ops /
audit professional who has the dashboard open on screen and needs to
understand what they're looking at. They are NOT a developer reading
source code, and they are NOT a casual visitor — they came from a
sheet and they need to make a real call.

## Required sections (in order)

```
# <Sheet Title>  — exact same string as the sheet's `name=` constant.

> **What this sheet teaches.** 1–2 sentences naming the error class.
> Per `[[feedback_demo_teaches_error_classes]]`: the page exists to
> explain a *class of error*, not to describe a UI. If you can't write
> this sentence cleanly, you're papering over an unclear sheet.

## What you're looking at

A paragraph (4–8 sentences) walking the operator through the sheet
top-to-bottom: KPI strip → main visual → tables → filters. Use the
real visual titles (in italics) and KPI labels. Anchor every claim
in something the operator can see.

## How to read the numbers

The data model — which matview or dataset feeds each visual, what
the columns mean, what aggregation produces the KPI, and where the
filter knobs route into the SQL. Be specific about column names
(`stored_balance`, `computed_balance`, `drift`) and matview names
(`<prefix>_drift`). One paragraph per non-trivial visual.

## Common patterns

Three to five sub-sections (`### <Pattern name>`), each one a
recognisable shape the operator will see in the wild:
- What the shape looks like on the sheet (which row counts, which
  KPI value, which bar dominates)
- What it usually means in the underlying system
- What the operator should do next (which sheet to drill into,
  which filter to apply, who to ping)

The error-class teaching contract lives here. If the sheet teaches
"drift is the disagreement between stored and computed", the patterns
section is "feed gap vs late posting vs supersession-without-replay"
each with their fingerprint.

## What "no rows" means

The empty-state guide. Per `[[feedback_demo_teaches_error_classes]]`
and the BO.10 / BK.10 empty-state discipline phases, an empty sheet
is a teaching moment, not a failure. Cover:
- The condition under which this sheet ships zero rows
- Whether zero means "all clean" or "filter too narrow" or "matview
  stale"
- The Daily Statement / App Info cross-check that distinguishes
  data-clean from feed-failed

## Cross-sheet drills

Bullet list of every drill (left-click and right-click) the sheet
defines, in the form:
- **<Visual> → <Destination Sheet>** (<click action>). What you
  learn at the destination.

These come from the Sheet's `Drill` actions in `apps/<app>/app.py`.
The agent should grep for `Drill(` references whose source visual
is on this sheet.

## Related handbook pages

Bullet list of 2–4 sibling pages the reader is likely to want next.
Use relative paths (`[Drift Timelines](../l1/drift-timelines.md)`)
so mkdocs and the App2 `?` route both resolve them.

## QS parity notes

If this sheet has known QuickSight rendering quirks (count-distinct,
URL-param dropdown desync, etc.), name them and link to the
quirks log entry (`[quirks log §<name>](../../reference/quicksight-quirks.md)`).
Otherwise omit this section — the agents should NOT default to
"no quirks" prose; absence is the signal.

```

## Vocabulary discipline — L1 / L2 are NOT universal

`L1` and `L2` mean specific things to *us* (the account-integrity
dashboard and the per-chain flow-tracing dashboard) but are completely
opaque to anyone outside this project. The first time **any handbook
page** uses `L1` or `L2`, it MUST expand the term inline and link to
the glossary:

> ...the L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants))
> invariant matview...

Subsequent uses on the same page may drop the parenthetical. The
footer of every page MUST carry a "Vocabulary" link to the glossary.

Same rule for these other project-specific terms when they appear:
`matview`, `account_role`, `rail`, `chain`, `template`, `carry-forward`,
`internal` / `external` scope, `parent` / `leaf` account. Each has a
glossary entry; first-use expansion + link is required.

Banking-standard terms (`debit`, `credit`, `posting`, `settle`, `clear`,
`ACH`, `wire`, `DDA`) do not need expansion — the operator-reader
knows them.

## Voice + style locks

- **CPA-readable.** Standard banking terminology over codebase shorthand.
  "Aggregator rail" / "transfer template" stay in-vocabulary because
  the L2 author chose them; "matview", "drift", "overdraft" are jargon
  the operator already lives in. "SyncConnection", "isolated_cfg",
  "pyright strict" are NEVER in handbook prose.
- **Second person, present tense.** "You're looking at" / "Each row is".
  Not "the user sees" / "the dashboard displays".
- **No emojis.** Per global CLAUDE.md.
- **Bullet lists of 4+ items, not slash-separated.** Per project
  convention. Slashes OK for 2–3-item lists.
- **No "the" at the start of paragraphs** when stating what something
  is — get to the noun. "A KPI strip showing…" not "The KPI strip
  showing…".
- **Currency:** `$1,234.56` with the `$` glyph and comma separators.
  Never plain numerics for money.
- **Code refs:** backticks for column names, matview names, SQL
  fragments. Italics for visual / KPI / sheet titles.
- **Length:** ~150–300 lines. The exemplar (`l1/drift.md`) is ~250.
  Shorter is fine when the sheet is simple (App Info canary, Getting
  Started intros); longer needs justification.
- **Factual accuracy:** every claim about data shape must be checkable
  against the matview SQL in `src/recon_gen/common/l2/schema.py` or
  the dataset SQL in `src/recon_gen/apps/<app>/datasets.py`. Don't
  invent column names. Don't invent KPI values. If the matview's
  `WHERE` clause excludes external accounts, say so and cite it.

## Source-of-truth references the agent should always read

For every sheet:

1. The Sheet `description=` string in `apps/<app>/app.py` (gives you
   the canonical 1-paragraph teaching focus to expand on).
2. The matview's `{matview_create_kw} {p}_<NAME>` block in
   `src/recon_gen/common/l2/schema.py` (gives you the column set
   + filter conditions + the JOIN/UNION structure).
3. The dataset's SQL in `src/recon_gen/apps/<app>/datasets.py`
   (gives you which columns each visual binds + the parameter pushdown
   patterns).
4. The Sheet's `Drill(` actions in `apps/<app>/app.py` (gives you the
   cross-sheet drill list).
5. The relevant memory entries (e.g. `project_qs_url_parameter_no_control_sync.md`
   for sheets with cross-app drills).

For sheets with cold-read findings in `docs/audits/_archive/v*_feedback.md`,
read those too — they describe what an operator actually felt confused
by, which is exactly what the handbook page exists to prevent.
