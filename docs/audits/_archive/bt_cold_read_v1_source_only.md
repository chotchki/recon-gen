# BT cold-read — first-time ETL Engineer

> **Persona:** ETL Engineer for a midsize credit union. ~5 years
> writing Python + SQL feed code (mostly Airflow + dbt + raw `psycopg2`),
> never seen Recon-Gen before. Someone above me bought this tool, wired
> up a YAML somewhere ("our L2"), and pointed me at the Studio web UI
> with the instruction *"land your ETL feed cleanly so the dashboards
> work."* I've got a `feed.py` script my predecessor wrote that pulls
> from the core banking system and writes to two Postgres tables. I'm
> told this is "the ETL hook." I'm here to make sure my hook is
> producing the right shape.
>
> **Output scope:** I'm reacting to the four pages under `/etl/` —
> landing, probe, run, triage — plus the cross-page flow. I am NOT
> reviewing the rest of Studio (L2 editor, diagram, dashboards) except
> where the ETL pages send me there.

---

## 1. Cold-read context

I just typed in the URL my coworker emailed me. I see a horizontal top
nav with "L2 Editor", "ETL Support", "Training", then a bunch of
dashboard names. I click **ETL Support** because, well, that's me. I
land on `/etl/` and now I'm staring at three cards: **Probe**, **Run**,
**Triage**.

I have no idea what any of those words mean *in this product's
vocabulary*. I know what "probe" means generally (poke at something to
see what's there), I know "run" (push the button, the thing runs), and
I know "triage" (sort what's broken by severity). But I don't yet know
which one is the **start here** card. I'm going to read each card's
sub-prose to figure it out — and that's where I'll judge whether this
landing page has done its job. Let me walk through it.

---

## 2. `/etl/` landing — first impressions

> `src/recon_gen/common/html/_studio_routes.py:618-722`

### What I see

A page titled **Studio · ETL Support** with a sasquatch_pr-or-whatever
mono label, a one-paragraph intro, and three cards (Probe / Run /
Triage) laid out in a 3-column grid.

The intro paragraph (`_studio_routes.py:710-715`):

> Three workflows for getting your customer's ETL feed landing
> cleanly: **Probe** one slice, **Run** the pipeline + score, **Triage**
> gaps + link back to the L2 editor to close them.

### What lands cold

- The three-card layout is the right shape — Probe / Run / Triage are
  three workflows, fine. I can recognize all three labels as English
  words. The accent color on the headings reads as "clickable", I'd
  click them.
- "Getting your customer's ETL feed landing cleanly" — that's exactly
  my job description. I felt seen there. Good open.
- Each card has both a description AND a URL hint in mono under the
  title. The URL hint is a small detail but it's nice — when I'm later
  trying to remember what URL goes where, I have something to grep.

### What does NOT land cold

- **"L2 slice"** in the Probe card description (`:623-625`). I have no
  idea what an L2 slice is. I'll guess from context: "L2" must be the
  YAML schema my coworker mentioned. "Slice" must mean a subset. So:
  pick a piece of the L2 config to inspect? Maybe. The example —
  "pick a rail, template, or chain" — narrows it but introduces *three
  more terms I don't know*. What's a rail? Is that ACH vs. wire? What's
  a "chain"? In my world a chain is a Markov chain or a Promise
  chain. I'm guessing here.
- **"L2-declared column expectations"** (`:624`). I think this means
  "what columns the L2 config says this slice should have." But the
  word "expectations" is doing a lot of work; in dbt land "expectations"
  means Great-Expectations-style row-validation. Is that what this is?
  I don't know yet.
- **"Per-kind coverage tally"** in the Run card (`:632-633`). "Tally"
  is fine. "Per-kind" sounds like Haskell. I think it means "broken
  out by what kind of thing (rail / template / chain)" — but I have to
  re-read the Probe card to get that vocabulary.
- **"Declared primitive"** in the Run card (`:633`). "Primitive" is
  another framework word. I'd call those entities or definitions.
  Primitive makes me think of int / bool / str. I'll get used to it
  but it's a speed-bump.
- **"Diff declared contracts against observed runtime"** in the Triage
  card (`:639-640`). "Diff" — fine, programmer word. "Declared
  contracts" — okay, similar to the Probe's "expectations" but now
  called something different. So is it expectation or contract? Pick a
  word. "Observed runtime" — sure, what actually ran. But I'm a third
  of the way down a 3-line description and I've already done two
  vocabulary translations.

### What's missing from the landing

- **No "start here" arrow or recommended sequence.** If I'm new, do I
  start with Probe (look around)? Run (do the thing)? Triage (only if
  broken)? The intro paragraph implies an order — Probe → Run → Triage
  — but the cards are presented as equal peers. I'd want a clearer "if
  you've never run this before, click Run first." The mockup's
  navigation diagram (§5 of `bt_design_mockups.md`) is actually
  clearer than the implementation; nothing on the page tells me Run
  is the entry point for a first-time setup.
- **No prerequisites callout.** Do I need to do anything before
  clicking Run? Does it need an ETL hook configured somewhere? Does
  it need a DB connection? If my hook isn't wired, what error do I
  see? The page doesn't tell me. I'm going to click Run and find out.
- **No link to a "what is this" doc.** There's no help icon, no link
  to a README, no "read this first." In real onboarding I'd want one
  paragraph that explains: "Your ETL hook is a command Recon-Gen
  invokes to populate `<prefix>_transactions` and `<prefix>_daily_balances`.
  These pages help you verify your hook produces data the L2 schema
  expects." That single sentence would orient me. Without it, I'm
  reverse-engineering from the card descriptions.
- **No glossary link.** Rail, template, chain, primitive, contract,
  slice — these are all jargon. A "what do these terms mean?" link
  somewhere on this page would carry me through five clicks of
  confusion.
- **No indication that "Run" is destructive.** The Run card says
  "execute the ETL pipeline (wipe → hook → matview refresh)." Wait —
  **wipe**? Does that wipe my prod data? My dev DB? My CI DB? The
  cards mention wipe almost in passing — that's the kind of thing I'd
  expect to be in a warning panel, not a sub-clause.

### Concept tags from this page

Non-landing words I'd tag on first read: **L2 slice**, **rail**,
**template**, **chain**, **primitive**, **contract**, **declared**,
**observed runtime**, **L2 editor**, **matview refresh** (the Run card
mentions "wipe → hook → matview refresh" — "matview" is shorthand I
know, but the more important question is *what's getting refreshed*).

---

## 3. `/etl/probe` — L2-slice probe

> `src/recon_gen/common/html/_studio_routes.py:731-1229` plus
> `src/recon_gen/common/l2/probe.py` for the fetcher + predicate
> evaluator.

I'm going to start here because Probe sounded the least scary on the
landing page. (I now realize — five minutes in — that I probably should
have started at Run. The landing page didn't tell me. Noted above.)

### The picker

The form (`_render_probe_picker`, `:824`) is:

- A three-radio fieldset: **Rail / Transfer Template / Chain**.
- A **Name** dropdown that I assume changes based on the radio. (The
  comment at `:842-845` confirms: "Server-side filtering... small inline
  script just toggles visibility of the three dropdowns." Reading the
  rendered HTML I see only one dropdown is emitted depending on the
  selected kind — actually re-reading: there's no JS toggle, the
  dropdown is server-emitted for the chosen kind only. That's actually
  fine — vanilla GET form submission re-renders. But the legend says
  "filters as you type" in the design doc; this isn't a typeahead, it's
  a `<select>`. Small downgrade vs. spec but probably right for the
  sizes I'll deal with.)
- **From** / **To** date inputs.
- **Apply** button.
- A helper line: *"Window defaults to last 7 days; widen for backfill
  / mass-load scenarios where the data lives outside the live window."*
  This is the right copy for my first cold-read concern (what if my
  feed is historical?) — props for putting it inline.

### Picking a slice

I pick **Rail** (default), open the dropdown, see five rail names from
the L2. I have **no idea what these mean** — they look like
`ach_credit`, `ach_debit`, `wire`, maybe `card`. Okay, I know what ACH
means. I pick `ach_credit`, click Apply.

### The left panel — Expected (from L2)

Header reads **"Expected (from L2)"** (`:943`). Good — matches the
mockup. Then a table with three columns: **Column / Op / Expected**.

Rows for the rail I picked, from `_render_rail_contract` (`:991`):

| Column | Op | Expected |
|---|---|---|
| rail_name | = | ach_credit |
| account_role | ∈ | {CustomerLedger, ExtCorrespondent} |
| amount_direction | = | Credit |
| metadata.trace_id | ≠ | NULL |

Below the table: a "→ Edit in L2" link.

#### What lands

- The header "Expected (from L2)" — this is the right framing. I know
  what to compare against.
- The Column / Op / Expected three-col layout is the right shape — I
  can scan it.
- The `∈` (element-of) operator is mathematically correct and once I
  recognize the symbol I know what it means: account_role must be one
  of those two values. Same for `≠ NULL` (not null).
- The "→ Edit in L2" link is good — if I see something wrong I know
  where to fix it.

#### What does NOT land

- **"Op" as a column header.** "Op" = "operation"? It's a database-y
  thing — fine. But I'd probably say "Constraint" or "Test" or
  "Predicate" (one of those design-doc words). "Op" is terse to the
  point of being cryptic.
- **`metadata.trace_id`** — what is this asking? "Metadata column,
  trace_id key." I'd infer that one. But the more general question:
  is the metadata column JSON? Is it text? Where does it live? The
  contract panel doesn't tell me — I have to know that
  `<prefix>_transactions.metadata` is a TEXT/JSON column and that
  trace_id is a key inside that JSON. The system doesn't bridge the
  gap between "metadata.trace_id" in the contract and "look in the
  metadata JSON column for a trace_id field." A cold-reader would
  guess wrong here ~30% of the time and spend 10 minutes confused.
- **The `∈` symbol.** I happen to know math notation. A finance
  engineer who didn't take a CS / math degree might just see a weird
  glyph. The mockup wrote it the same way. Plain English ("one of")
  would carry more readers.
- **No explanation of what these rows COLLECTIVELY say.** The four
  rows tell me: every row tagged `rail_name = ach_credit` must have
  `account_role` in that set, `amount_direction = Credit`, and a
  `trace_id` in metadata. The page just shows me the rows — it doesn't
  say "for every transaction row in your feed where rail_name is
  ach_credit, these four conditions must hold." I have to infer that
  the rows are AND-ed together. That's fine for me — I've done dbt
  tests — but a one-line preamble *"Every row matching this rail must
  satisfy:"* would seal it.

### The right panel — Observed (window)

Header reads **"Observed (window)"**, then either a small "Showing N of
M" line + the rows table + a legend, OR the empty-state copy.

#### Happy path — table renders

> `_render_probe_observed_panel`, `:1079-1127` and
> `_render_observed_row`, `:1130-1166`.

Header: **"Showing 25 of 1,247 rows in window 2026-05-23 → 2026-05-30"**.
Good — I know my data is real, I know how much there is, I know the
window matches the picker.

Columns: **Transaction / Posting / Rail / Template / Role / Direction
/ Predicate fit**.

Then a legend: *"Predicate fit: ✓ matches L2, ✗ contradicts, — no
value to evaluate."*

##### What lands

- The row count is excellent. Tells me my feed produced data, tells me
  there's more than the table shows, no ambiguity.
- Per-row "predicate fit" as `5✓ 0✗ 1—`. This is the right
  abstraction for a dense table — at a glance I can see if any row has
  red. (One I should poke at? Sort the table by ✗ desc would be great
  but isn't there. P3.)
- The legend resolves the symbol meaning. Good — without it the `—`
  would baffle me.

##### What does NOT land

- **"Predicate fit" as a column header.** Same vocabulary friction as
  "Op". I'd call it "Match" or "L2 fit" or "Compliance" or "Tests
  passed." "Predicate" is what an engineer who wrote the code calls it.
- **The fit numbers are aggregate, not per-predicate.** If I see
  `4✓ 1✗ 0—` on a row, I know one of my contract predicates is
  failing — but not WHICH. I have to read the row's columns and
  cross-reference the contract panel on the left. For one row that's
  fine. For ten, that's tedious. I'd want to expand a row and see
  per-predicate: "account_role: ✓ CustomerLedger matches", "trace_id:
  ✗ missing". Without that, the predicate-fit column is a smell, not
  a signal. (P2: I'd figure it out, but slowly.)
- **No way to click a ✗ row and see the full row.** When I see a
  failing row, I'd want to click and inspect the full JSON
  payload — what did my ETL actually produce? Currently I have a
  transaction_id; I'd have to go to my own DB and run a SELECT. (P2.)

#### Empty-state — "No rows match this slice"

> `_render_probe_empty_observed`, `:1169-1183`.

> "No rows match this slice. The L2 declares this rail / template /
> chain but the ETL hook hasn't produced any matching rows in the
> window 2026-04-01 → 2026-04-15.
> - Widen the window — backfill / historical loads may live outside today's default.
> - Check Run + coverage to see when the last ETL ran.
> - If the last run was recent, this slice may be a real ETL gap. Open Triage."

This is **really good prose.** Three actionable next steps, the right
order (cheapest fix first), inline links to the other two pages. Cold
read: I know what to do. This is the gold standard for the rest of the
empty-states.

#### "No DB pool wired" branch

> `:920-928`.

> "**No DB pool wired.** The Probe needs a connection to
> `sasquatch_pr_transactions` to read observed rows. Run Studio against
> the demo DB to see live data."

As a first-time engineer: I would NOT know what this means. "DB pool"
is implementation jargon. What's a "pool"? Why does the system need
one? What do I do to fix it? **"Run Studio against the demo DB"** — is
the Studio not already running against my DB? Wasn't that the whole
point? Am I in the wrong mode? Is this a config issue or did I just
launch wrong?

This message will only fire when someone runs Studio without `--cfg`
(unit-test surface). The chance of a first-time engineer seeing this
branch IS LOW but not zero — if my coworker copied the Studio launch
command wrong, I might land here, and the message wouldn't help me
recover. (P2: edge case, but the message is opaque.)

### Date-range default

The "last 7 days" default is fine for me on a happy path — I'd be
running this against fresh ETL output. The helper text mentions
backfill. Good. **But** — if I'm doing a first-time-setup and my hook
just ran for the first time today, "last 7 days" includes today and
my data shows up. Probably right.

The one concern: if my ETL writes rows with `posting_date` in the
past (which is normal for any backfill scenario — last month's
transactions land in `posting_date = 2026-04-15` even though I'm
ingesting them today), the default would hide that data. The helper
text covers it: "widen for backfill / mass-load scenarios." Cold-read:
I'd notice the "0 rows" + the helper hint and widen. Should work.

### Concept friction tally for Probe

- **Slice** — okay-ish from context; nobody calls a config subset a
  slice in normal usage. Closest analog: dbt's `--select` flag. The
  word is fine *if* I know the metaphor; cold-read I had to guess.
- **Predicate** — okay-ish; comes from logic / Prolog / SQL. An
  engineer who only does Python + SQL might not have hit this term.
- **Selector** — the contract panel doesn't surface this term directly
  (the implementation uses `RowSelector` internally) but the row
  `rail_name = ach_credit` at the top of the contract IS the selector.
  A "this row picks the transactions; these rows test them" framing
  would help. Not blocking.
- **Singleton / XOR sibling** — the Chain contract panel
  (`_render_chain_contracts`, `:1009`) puts these in the "kind"
  row. "Required (singleton)" and "XOR sibling" are both jargon. The
  first half (Required) lands. The parens-singleton is the kind of
  thing I'd ignore on first read. "XOR sibling" — I know what XOR is,
  but "XOR sibling" doesn't paint a picture. (P2.)
- **`(parents/child: N)`** in chain fan_in rows — I'd be lost.

---

## 4. `/etl/run` — execution + coverage

> `_render_etl_run_page` at `_studio_routes.py:1235`, plus form / log /
> coverage helpers below it.

### The Run form

A big accent button **▶ Run ETL** + a status sidebar that shows one of
three states: "No runs yet", "● success at <ts> · gen N · M transactions",
or "● HALTED at <ts> · reason: <reason>".

#### What lands

- The button is clearly the primary action. Right size, right color.
  Cold-read: I'd click it.
- "No runs yet" — clear empty state.
- The success state includes the transaction count. Good — I can
  spot-check whether my hook produced ~the volume I expected.

#### What does NOT land

- **"gen N"** — what's `gen`? Generation? Generator? Genesis? I'd
  guess "data generation ID" but the abbreviation is gnomic. (P3 but
  why abbreviate?)
- **"● HALTED at … · reason: <code>"** is THIN compared to what the
  design mockup specified (`bt_design_mockups.md:310-322`). The
  mockup's halt banner is:

  > **⚠ Last run HALTED at step1:etl_hook (exit code 17)**
  > stderr: feed.py: KeyError: 'trace_id' on row 1422
  > The DB was wiped before the hook ran — it is currently empty.
  > Dashboards will be blank until the next successful run.
  > [ → Open Triage ]  [ ↻ Re-run ]

  The implementation shows me only the reason string in a `<code>` tag.
  No "the DB was wiped" warning. No "dashboards will be blank" framing.
  No CTAs for re-run or triage. **As a first-time engineer this is the
  scariest case** — my run halted, I don't know what to do next. The
  current copy gives me a one-line reason and a button that says "Run
  ETL again." If the reason is "feed.py: KeyError: 'trace_id'", I know
  to fix my hook. But the loud "your DB is empty until you re-run" — I
  wouldn't infer it from this UI. I'd click around dashboards, see
  them blank, and panic, not knowing it was a side effect of my halted
  run. **This is a P1 friction.**

- **No "what does Run actually do" preamble.** Per the landing page,
  Run wipes → invokes hook → refreshes matviews. The Run page doesn't
  repeat that. So I push the button without a confirm dialog and...
  what? It just runs? Does it block the page for 30 seconds? Is there
  a progress indicator while it runs? The form just POSTs and 303s
  back; until the redirect lands my browser shows a loading spinner.
  Cold-read: I'd be nervous I just borked something. **A pre-run
  description ("Clicking Run will: (1) truncate the demo DB, (2)
  invoke your hook, (3) refresh matviews. ~10s for a typical L2.")
  would settle me.** (P1.)

- **No "no ETL hook configured" branch.** If `cfg.etl_hook` is None
  or empty, what happens when I click Run? The code path goes through
  `run_deploy_pipeline` which presumably has its own gate, but the
  Run page doesn't precondition the button. **If my hook isn't wired,
  I'd hit the button and get something cryptic.** The button should
  be disabled with a "Configure your ETL hook in the L2 editor first"
  hint if no hook is set. (P1.)

### The last-run event log

> `_render_etl_run_log`, `:1348-1373`.

When a run has happened, shows an `<h2>Last-run log</h2>` + a scrolling
mono block with one line per event: `<kind> <k=v> <k=v> ...`.

#### What lands

- The mono block is the right shape. I can see the pipeline ran.
- The h2 "Last-run log" is unambiguous.

#### What does NOT land

- **`kind` values like `deploy:step2:wipe:start`** (per the mockup
  format). Cold-read: that's a colon-separated namespace and I can
  parse it. *But* — the actual implementation just iterates
  `last_summary.events` and prints `kind` as-is, then key=value for
  every other field. **What `kind` values does `run_deploy_pipeline`
  actually emit?** I'd have to read the deploy module to know. If the
  events match the mockup (`step2:wipe`, `step1:etl_hook`,
  `step3:generator skipped (disabled)`, `step4:matviews`,
  `step5:reload`), it'd be readable. The mockup format is fine; I'd
  understand it.
- **No timestamps.** The mockup wireframe shows `14:23:01 [step2:wipe]
  start` — the actual code prints whatever's in the event dict but
  doesn't enforce a timestamp prefix. If timestamps aren't in the
  events, I lose the duration signal per step.
- **No "step duration" callout.** Mockup has `done (0.3s)` per step.
  Code prints whatever is in the event. If those duration fields
  aren't emitted, the log is structurally fine but content-thin.
- **No filter / no clear button.** Mockup has `[ ↻ Re-run ]
  [ Clear log ]`. The implementation has neither. P3.
- **The log scrolls to top, not bottom.** `max-h-72 overflow-y-auto`
  with the events painted in document order means the first event is
  at the top. For a "what's the latest" log I'd want bottom-anchored.
  P3.

### Coverage cards

Three small cards (Rails / Templates / Chains) + one wide Metadata
card.

> `_render_coverage_card_for_kind` at `:1443`,
> `_render_chain_coverage_card` at `:1474`,
> `_render_metadata_coverage_card` at `:1490`.

#### What lands

- **N/M tally with percentage.** "5 of 7 declared (71%)." Right size,
  right shape, instant scannability.
- **Per-entity ✓/✗ list.** I can see exactly which rails landed.
- **The Metadata card.** "X/Y keys ✓" + "missing: foo" on the rows
  with gaps. This is the most operationally useful card on the page —
  it's the kind of granular feedback I'd build a dashboard around in
  my own tool.

#### What does NOT land

- **No "what does ✓ mean" legend.** I'm guessing ✓ = "at least one
  row landed for this rail." A first-time engineer might guess "all
  rows are valid for this rail" — opposite shape. (P2.)
- **No row counts on the per-entity list.** "ach_credit ✓" — okay,
  but did I land 5 rows or 50,000? The Probe page would tell me;
  this card doesn't. Aggregate counts somewhere would carry me.
- **The Metadata card's `0/4 keys ✗ no rows` row.** If the template
  has no rows at all, what does "no rows" mean? It means the template
  itself isn't firing — so the rails / templates card should show it
  as ✗. The "no rows" in the metadata card is downstream noise. (P3:
  not wrong, just redundant.)
- **No drill from a ✗ entity to Probe with that entity pre-picked.**
  I see `card ✗` in the Rails card; I'd want to click `card` and land
  in `/etl/probe?kind=rail&name=card` to investigate. Currently the
  list is just text. (P2 — the design mockup's nav §5 explicitly
  defers Run→Probe deep linking, but cold-read this is exactly the
  click I want.)

### Empty-state — "No ETL has been run yet"

> `:1402-1411`.

> "No ETL has been run yet on this L2. Click ▶ Run ETL above to invoke
> the configured ETL hook against the demo DB. Coverage shows up here
> once the run completes."

**This is clear cold.** Right scope (this L2), right action (click the
button), right consequence (coverage appears). Good. The empty-state
copy quality is one of the wins on this surface.

But — see "no hook configured" concern above. The empty-state assumes
the hook IS configured. If it isn't, the operator clicks the button
and bad things happen.

### Concept friction tally for Run

- **`gen <N>` in the success banner.** I'd skip past this. (P3.)
- **"matview refresh"** — I know matview is short for materialized view
  and I know what those are. A non-engineer might not. The Run card on
  the landing page mentions this, the Run page itself doesn't repeat
  it. Fine.
- **"halted" vs. "failed" vs. "errored".** I'd use the words
  interchangeably. The code says "halted." Fine; not a friction.
- **"data_generation_id"** doesn't appear directly on the page (the
  banner says `gen N`); the URL might.

---

## 5. `/etl/triage` — exception triage

> `_render_etl_triage_page` at `_studio_routes.py:1565`, body at
> `:1630`, card at `:1656`. Gap detector +
> diagnosis prose: `src/recon_gen/common/l2/triage.py`.

I arrive at Triage either by clicking it from the landing page, by
clicking "Open Triage" from a Probe empty-state, or by clicking it from
the Run page's coverage section. Cold read: I assume my run had some
gaps and I want to fix them.

### The triage body

Header: **"N gaps detected."** Then a 2-column grid of cards, one per
gap.

(The design mockup shows filter / sort / re-check controls at the top.
Implementation: none of those are present. Just the count line. I
wouldn't notice cold; I'd just scroll. P3.)

### Gap card shape

> `_render_gap_card`, `:1656`.

Each card has:

- ⚠ icon + kind label (e.g. "Unmatched rail_name").
- Diagnosis prose (one paragraph from `triage.py`).
- Extras block (`key: value` mono lines).
- Sample line (`sample: tx-13422`).
- CTA button (`→ Open Rails editor` etc.).

#### Per-kind diagnosis prose review

##### `_detect_unmatched_rails` (`triage.py:104-148`)

Diagnosis: *"47 rows arrived with rail_name=\"ach\" but the L2 declares
no Rail of that name."*
Extras: *"declared_rails: ach_credit, ach_debit, wire, check, sweep,
card, atm"*
CTA: **→ Open Rails editor**.

**Cold-read:** This is **excellent.** I know exactly what's wrong
(row count, the bad value, the L2 declaration gap), I can see what
the L2 DOES declare (in case I just typo'd "ach" instead of
"ach_credit"), and the CTA tells me where to go fix it. The diagnosis
prose is the gold standard.

One nit: "the L2 declares no Rail of that name" — the capital R
"Rail" reads like a brand name. It's a typed primitive name; in prose
I'd lowercase it. But this is bikeshedding.

##### `_detect_unmatched_templates` (`triage.py:154-199`)

Diagnosis: *"12 rows tagged with template_name=\"ReturnReversal\" — no
such template in the L2."*
Extras: *"declared_templates: ACHReturn, CheckClear, MerchantSet, …"*
CTA: **→ Open Templates editor**.

**Cold-read:** Same shape as unmatched_rails, same praise. The "tagged
with" phrasing is slightly nicer than "arrived with" — would prefer
the latter modeled on this one. (P3 consistency.)

One miss: the design mockup says "Closest declared templates:
ACHReturn, CheckReturn. Operator decides: add the new template OR
rename the ETL's tag to match an existing one." The implementation
just lists ALL declared templates — no nearest-match suggestion, no
"operator decides" framing. Cold-read I'd notice the missing decision
hint; the mockup's "operator decides" line is the right shape because
it implies *I have a choice to make*. Without it I might just blindly
add the missing template when the right answer is "fix the ETL tag."
(P2.)

##### `_detect_missing_limit_schedules` (`triage.py:205-260`)

Diagnosis: *"142 wire rows landed against CustomerLedger but no
LimitSchedule covers this (parent_role, rail) tuple. L1 Limit Breach
renders these as \"no cap\" in dashboards."*
Extras: *"existing_schedules_for_CustomerLedger: (CustomerLedger,
ach_debit, Outbound) cap=5000.00; (CustomerLedger, ach_debit, Inbound)
cap=10000.00"*
CTA: **→ Open Limits editor**.

**Cold-read:** This is the one I'd struggle with most.

- **"(parent_role, rail) tuple"** — programmer-speak. "Combination" or
  "pairing" would be plainer.
- **"L1 Limit Breach renders these as \"no cap\" in dashboards"** —
  this is GOOD because it tells me the downstream consequence. But it
  introduces "L1 Limit Breach" as a thing that exists. Is that a
  dashboard? A matview? A panel? A widget? Cold-read I have no idea.
  *Where do I look to confirm the consequence the diagnosis warns
  about?* The CTA jumps to the Limits editor, not to the L1 Limit
  Breach dashboard. (P2.)
- **`existing_schedules_for_CustomerLedger`** as the extras key —
  underscores in the key are fine in a debugger but loud in operator
  UI. Whitespace ("existing schedules for CustomerLedger") would read
  better. Same for `template_total_rows`. (P3.)
- **The "operator decides" framing is missing** here too. The mockup
  said *"Operator decides: add a schedule OR confirm 'no cap' is the
  intended posture."* Implementation just describes the gap without
  asking me to make a decision. **Sometimes "no cap" IS the right
  posture** — particularly for low-risk rails. The current diagnosis
  reads as "this is broken, fix it" when the right read might be
  "this is a policy choice." (P2.)
- **`cap=5000.00`** in the extras — that's a Money value as a bare
  decimal. No currency, no formatting (e.g. $5,000). For a financial
  product, raw decimals next to a "cap" label feel underdressed.
  (P3.)

##### `_detect_missing_metadata_keys` (`triage.py:266-343`)

Diagnosis: *"Template ACHReturn declares \"reason\" as required. 23
of 31 ACHReturn rows landed without it — L1 Conservation can't
bucket them. Operator decides: fix the ETL to emit \"reason\", or
drop the key from the template if upstream genuinely doesn't carry
it."*
Extras: *"template_total_rows: 31, missing_key: reason"*
CTA: **→ Open template editor**.

**Cold-read:** Strong. The diagnosis is concrete (X of Y rows, named
key), the consequence is named (L1 Conservation can't bucket them),
the operator decision is explicit (fix ETL or relax template). This
is the best diagnosis of the four.

Same nit re: "L1 Conservation" as a concept name. Cold-read I don't
know what that is. (P2.)

The extras row is borderline noise — `template_total_rows: 31` is
already in the diagnosis as "31 ACHReturn rows", and `missing_key:
reason` is also in the diagnosis. Could just have `sample: tx-14008`
and drop the extras for this kind. (P3.)

### Extras row in general

Across all four kinds, the extras list adds useful context (declared
rails/templates) AND noise (`template_total_rows`, the
`existing_schedules_for_<role>` key naming). A consistent "show context
not echo prose" rule would tighten the cards. (P2.)

### CTA labels

- **"Open Rails editor"** / **"Open Templates editor"** / **"Open
  Limits editor"** / **"Open template editor"** (lowercase for the
  metadata gap — inconsistent capitalization vs. the others).

  P3 nit on the capitalization. More importantly: cold-read I'd want
  the CTA to specify *what I'll do there*. "Open Rails editor" — okay,
  then what? Add a new rail? Edit `ach`? The diagnosis tells me "no
  Rail named ach" so the right action is **add** a rail. But the CTA
  doesn't say "Add new rail" — it says "Open editor." Per BT.0 lock 5
  this is intentional (link-only v1, no pre-fill), but the CTA label
  could still be more directive: **"+ Add a Rail"** would carry me
  better. (P2 — and this maps directly to the BT.0 question about
  whether the link-only friction is bearable. My cold-read: it is
  bearable, but the CTA labeling could close half the gap without
  pre-fill.)

- **"Hide this gap kind"** from the mockup is **missing** in the
  implementation. The cards have no per-card actions besides the CTA.
  Cold-read: not blocking, but for a triage flow where I want to
  acknowledge "yep, that one's known, hide it" the dismiss control is
  the right UX. (P3.)

### Empty-state — "No gaps detected"

> `:1633-1643`.

> ● **No gaps detected.** Every row produced by the last ETL run
> matches the L2's declared contracts. → Re-check on the next ETL run,
> or after editing the L2.

**Excellent.** Per BT.0.5 §6 operator decision 2, this affirmation is
intentional. It lands. After 5 minutes of triage hell I'd want this
exact green dot to confirm I'm done. The "re-check" line tells me
what to do next.

One miss vs. the mockup: "Last checked: 2026-05-30 14:23." The mockup
included a timestamp. Implementation doesn't — the page just runs
detect_gaps fresh on every GET. Cold-read I'd want to know if this
state is from 5 seconds ago or 5 days ago. (P3.)

### Gap prioritization

A real triage session has multiple gaps. The mockup shows four. Cold
read: **which do I fix first?**

- Unmatched rail_name (47 rows): probably a typo or new rail.
- Unmatched template_name (12 rows): same idea, fewer rows.
- Missing LimitSchedule (142 rows): big number but maybe-not-an-error.
- Missing metadata key (23 of 31): blocks L1 Conservation.

The cards render in gap-detector order — kind, then offending value.
There's no severity signal, no row-count sort, no "fix this first"
hint. **As a cold-read engineer I'd default to top-down**, which
might mean I fix a 12-row template typo before a 142-row limit
omission. (P2: a "sort by row count" toggle, or a severity badge,
would help.)

### Cross-card design call

Each card is self-contained — which is right for a "one decision per
card" pattern (per mockup notes). But there's no aggregate **"4 gaps
in 3 kinds — 1 rails, 1 templates, 1 limit, 1 metadata"** summary
that would help me plan my session. The header just says "4 gaps
detected." Cold-read: I'd appreciate a one-line breakdown by kind.
(P3.)

### Concept friction tally for Triage

- **"L1 Limit Breach"** / **"L1 Conservation"** — these are dashboard
  / matview names that appear in the diagnosis prose. Cold-read I
  don't know what they are. (P2.)
- **"tuple"** in the limit-schedule diagnosis. (P3.)
- **"contract"** as the diagnosis text repeats it. By now I've parsed
  that "contract" = the expectations defined by the L2 for a given
  primitive. Once I've internalized it, it's fine.
- **`parent_role`** — column name leaking into prose. I'd say "account
  role of the parent account" or just "account role." (P3.)

---

## 6. Cross-page friction

### Per the design mockup §5 nav flow

The mockup says: Probe → Triage (when slice is empty), Run → Triage
(on halt), Triage → editor (via card CTA).

Implementation status:

| Edge | Status |
|---|---|
| Probe empty → Triage | ✓ link in empty-state copy |
| Probe empty → Run | ✓ link in empty-state copy |
| Run → Triage (coverage CTA) | ✓ link at bottom of coverage section |
| Run halt → Triage | ✗ no halt-banner CTA (see §4 concern) |
| Triage → editor | ✓ CTA button per card |
| Run → Probe (deep link from coverage ✗) | ✗ intentionally deferred per mockup §5 |
| Coverage page → Probe (any link) | ✗ no link from coverage to probe at all |

The Run-halt → Triage gap is the most damaging hole. As called out in
§4, a halted run is the moment I most need a CTA, and there isn't one.

### Walkthrough — first-time setup loop

> *"I just wired up my ETL hook. What do I click first?"*

1. Land on `/etl/`. See three cards. **Confused about start point**
   (§2 concern). Read all three card descriptions. Infer that Run is
   the action that does the thing. Click Run.
2. Land on `/etl/run`. See "No runs yet" and a big ▶ button. Click
   it. **Did anything pre-confirm with me?** No. **Did I check if my
   hook is wired?** No. (§4 concerns.)
3. Wait. Browser spins. Page reloads. I see either success state +
   coverage, or HALTED state with a thin reason line.
4. **Happy path:** I see coverage. Some cards are red. I click
   nothing (no drill). I navigate to Triage manually via top nav.
5. **Sad path:** I see HALTED. **No CTA to fix.** I'd refresh, click
   Run again, get the same halt. I'd eventually navigate to my own
   feed.py code, find the error, fix it, come back, click Run.
   Throughout this I'm operating blind on the "DB is currently empty"
   side effect.
6. Land on Triage. See gap cards. Click "Open Rails editor" on a
   missing-rail card. Get teleported to the L2 editor's rail list. I'm
   now far from the ETL context. No breadcrumb back. **Where was I?**
   (P2: editor pages need a back-to-ETL crumb.)
7. Add the missing rail. Save. Navigate back to ETL Support
   (manually). Click Triage. See the gap is gone (or has a different
   shape — maybe now there are 47 valid ach rows but 12 unmatched
   `ach_credit` rows because I misspelled it the other way). Repeat.

Total loop time: probably 2-3 minutes per cycle vs. the SPEC's
sub-1-minute target. The blockers: (a) no pre-run confirmation /
preflight, (b) thin halt UX, (c) no breadcrumb from editor back to ETL
context.

### Walkthrough — debugging loop

> *"Dashboard X shows 0 rows. What do I open?"*

1. I'm in a dashboard. I see 0 rows. I don't immediately know whether
   this is an ETL issue or a dashboard issue.
2. I navigate to ETL Support via top nav.
3. I see Probe / Run / Triage. **I'd click Triage first** — "show me
   what's broken." Gap cards or "no gaps detected."
4a. **No gaps:** triage says everything is clean. But the dashboard
    is still empty. **Now what?** Triage doesn't reassure me about
    matview freshness, generation IDs, etc. I'd be stuck. (P1: when
    triage is green but dashboards are blank, where do I go?)
4b. **Gaps:** I see the missing metadata-key card. I'd map "L1
    Conservation can't bucket them" to "okay, that explains the
    dashboard." Click CTA, fix, re-run. (Happy path.)
5. If I'd clicked Probe first instead, I'd have to know which
   rail/template the empty dashboard is about. I don't. The Probe
   page is *not* the right entry point for "dashboard X is empty"
   debugging — it requires me to know which slice to investigate.
6. If I'd clicked Run first, I'd see coverage. **Coverage might
   actually be the best first-click for "dashboard is empty"** — the
   per-entity ✓/✗ tells me which rails/templates didn't fire. But
   the landing page's card descriptions don't sell Run as the
   diagnostic surface; they sell it as the execution surface. (P2:
   landing page mis-pitches Run.)

---

## 7. Top concerns + non-landing concept names

### Most likely to derail a first-time engineer

1. **No "what is this product" orientation on the landing page.** I
   spent the first 5 minutes guessing at vocabulary. A 2-sentence
   intro + a glossary link would cut that to zero. (P1)
2. **Halt-state UX is dangerously thin.** Per §4, the live page shows
   one line of halt reason and no "your DB is empty" warning. A
   first-time engineer in a halt state will be confused about side
   effects. (P1)
3. **No pre-Run confirmation / no preflight.** I can hit ▶ Run ETL
   without knowing if my hook is configured. Mistake-friendly. (P1)
4. **No breadcrumb from L2 editor back to the triage card I was
   working.** When the CTA jumps me into the editor, I lose context.
   (P2)
5. **Gap cards have no severity / sort signal.** With 4 gaps I'd be
   unsure which to fix first. (P2)
6. **"Predicate fit" column in Probe gives aggregate not per-predicate
   detail.** I see `4✓ 1✗` and have to cross-reference to find the
   bad one. (P2)
7. **"No DB pool wired" message is opaque** if a first-time engineer
   actually hits it. (P2)
8. **Run page's coverage cards don't drill back to Probe** even
   though the design's nav diagram defers this — cold-read this IS the
   click I want. (P2)

### Non-landing concept names

In rough order of cold-read pain:

- **L2 slice** — appears on landing card and probe header. Guess from
  context.
- **Primitive** — landing card. Framework word.
- **Predicate / Predicate fit** — Probe table column header + Triage
  internals. Programmer word.
- **Op** — Probe contract panel column header. Cryptic abbreviation.
- **Singleton / XOR sibling** — Chain contract panel. Mathy.
- **L1 Limit Breach** / **L1 Conservation** — Triage diagnosis prose.
  Dashboard / matview shorthand without referent.
- **(parent_role, rail) tuple** — Triage diagnosis prose. Engineer
  speak.
- **gen N** — Run page banner. Abbreviation without expansion.
- **`existing_schedules_for_<role>`** / **`template_total_rows`** —
  Triage extras keys. Snake-case debugger output in operator UI.
- **matview refresh** — landing + design docs. I know it, others may
  not.
- **DB pool** — Probe / Triage no-DB branch. Implementation jargon.
- **declared / observed runtime** — Triage card framing. Once parsed,
  fine, but jargon-dense on first read.
- **contract** vs. **expectation** vs. **predicate** — three names for
  related concepts across the three pages. Pick one and stick with it.

### Stuff that lands clean cold

- ⚠ + ✓ + ✗ + ● visual vocabulary — universal.
- 3-card landing layout (despite vocabulary friction in the copy).
- Side-by-side Expected / Observed on Probe.
- N/M tally + percentage on coverage cards.
- The Probe empty-state copy with three numbered next steps.
- The Triage empty-state ● green confirmation.
- The "Open <kind> editor" CTA pattern.
- The "Showing N of M" line on Probe observed rows.
- The "Run + coverage" header naming (combining the two concepts).
- The unmatched_rails diagnosis prose.

---

## 8. What's genuinely good

This is a small surface and the design is sturdy. Per
`feedback_agent_driven_design_works`, calling out the wins is signal.

- **The four-page split is the right shape.** Probe (investigate) /
  Run (execute) / Triage (find + fix) maps directly to the operator's
  three modes. Adding a fifth (e.g. "Config") would be wrong; merging
  any two would be wrong. The carving is good.

- **The triage diagnosis prose is the strongest copy on the surface.**
  Each gap card tells me *the row count, the offending value, the
  L2 state, the downstream consequence, and the operator decision* in
  one paragraph. That's a lot of information density at high
  readability. The unmatched_rails copy specifically would survive
  a usability test untouched.

- **Empty-states are uniformly above-average.** The Probe "no rows in
  this slice" with three numbered next steps; the Triage "no gaps
  detected" green dot; the Run "no runs yet" CTA — these all land
  cleanly without re-read. Empty-state design is usually where Studio
  surfaces fail; this one nailed it.

- **The contract module's renderer-agnostic typed shape
  (`contract.py`)** means the Probe AND Triage panels show me a
  consistent view of what the L2 expects. As an engineer I trust the
  surface more knowing there's one source for that definition.

- **Date-range picker default of 7 days with explicit "widen for
  backfill" helper text** is the right call — I read it, understood
  the operator decision the operator made, and didn't have to ask.
  The mockup-review note about flipping this from a static window
  was the right call.

- **The mono prefix label in the page header** (`sasquatch_pr` etc.)
  is a small detail but it tells me **which deployment I'm pointed at**
  — important for any engineer who has dev + staging + prod windows
  open.

- **Per-template metadata coverage card on Run.** The granular per-key
  ✓/✗ + "missing: foo" output is the most operationally useful
  artifact on the entire surface. I'd build a Slack alert against
  that shape.

- **Editor links per gap and per probe contract.** Even without
  pre-fill (BT.0 lock 5), the deep link saves me from "okay, where
  do I edit a rail?" navigation hunting.

---

## 9. Recommendations

Prioritized.

### P1 — likely to block a first-time engineer

1. **Beef up the halt banner.** Match the design mockup
   (`bt_design_mockups.md:310-322`): explicit "DB is currently empty"
   warning, stderr surface (not just `halt_reason`), two CTAs (→ Open
   Triage, ↻ Re-run). Today the halt state is one line of code in a
   sidebar; it needs to be a prominent warning banner above the
   coverage section.
2. **Add a one-paragraph orientation to the landing page.** Two
   sentences explaining what Recon-Gen does and what the ETL Engineer's
   job is in it. Bonus: a link to a glossary or a "first time? click
   Run" recommendation.
3. **Preflight the Run button.** If `cfg.etl_hook` is missing/empty,
   disable the button with a hint ("Configure your ETL hook in the L2
   editor before running"). If it's set, render the command above the
   button so the engineer can confirm. This blocks a class of "I hit
   the button and weird things happened" tickets.
4. **Add a "what does Run do" explainer above the button.** 1-line:
   "Clicking Run will truncate the demo DB, invoke your hook, and
   refresh matviews. ~10s for a typical L2." Currently the wipe step
   is mentioned only on the landing page in a sub-clause.

### P2 — causes friction but recoverable

5. **Drill from coverage ✗ to Probe with the entity pre-picked.** The
   design mockup defers this; cold-read shows it IS the click an
   operator would make. Wire `<a href="/etl/probe?kind=rail&name=card">`
   on each red entity in the coverage cards.
6. **Per-predicate fit in Probe rows, not just aggregate.** Either
   expand-on-click or a per-predicate column matrix would beat the
   `4✓ 1✗ 0—` shorthand.
7. **Sort + filter on the Triage card grid.** Sort by row count desc
   (severity proxy), filter by kind. The mockup specified these; the
   implementation skipped them.
8. **Breadcrumb from L2 editor back to ETL.** When triage's CTA jumps
   me into the editor, leave a "← back to ETL Triage" affordance.
   (Probably an editor-side change, not strictly in BT scope; flag
   anyway.)
9. **Rename "Op" / "Predicate fit" to plainer English.** "Test" / "L2
   fit" / "Match." Five-second change, real cold-read uplift.
10. **Operator-decision framing on limit-schedule and template-name
    gaps.** Mockup had "Operator decides: …"; implementation dropped
    it. The framing is what tells me a gap might be a policy choice
    rather than a bug.
11. **Resolve concept naming: contract vs. expectation vs.
    predicate.** Three pages, three names for the related concept.
    Pick one and propagate.
12. **Reframe the "No DB pool wired" message** in operator-actionable
    terms. "Studio was launched without a database — relaunch with
    `--cfg` to enable this view" or similar.

### P3 — polish

13. **Aggregate breakdown line on Triage header** ("4 gaps: 1 rail, 2
    templates, 1 metadata") instead of just "4 gaps detected."
14. **Format `cap=5000.00` as `$5,000`** in the limit-schedule extras.
15. **De-noise the gap extras keys** — swap `underscore_case` for
    "human case" where the key surfaces to the operator.
16. **Anchor the log scroll to bottom** in Run's last-run log.
17. **Surface step durations in the log** if `run_deploy_pipeline`
    emits them; otherwise add them to the event dicts.
18. **"Hide this gap kind" / "Last checked at <ts>"** on the Triage
    page per the mockup.
19. **Add a glossary page** linked from each card description. Defines
    rail, template, chain, primitive, contract, slice, predicate,
    matview, L1, L2, gen N, in 1-2 lines each. The single highest-
    leverage doc artifact in this surface.

---
