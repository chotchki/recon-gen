# BT Cold-Read — ETL Support, First Hours

**Persona:** First-time ETL engineer at a midsize credit union. Hands-on
Python + SQL. Never seen Recon-Gen before today. Someone (a senior dev, the
vendor, a Slack message) handed me the URL to the self-hosted Studio and
said: *"land your ETL feed cleanly so the dashboards work."* I have a
half-written hook that drops rows into `<prefix>_transactions` and
`<prefix>_daily_balances`. I want to know whether it's working.

This doc is my unfiltered first-pass reaction across the three ETL Support
pages — Probe, Run, Triage — plus the L2 Editor I get bounced to. I'm
walking it in the order a new operator would: land on `/etl/`, poke around,
get confused, eventually click something destructive.

Screenshots referenced live under
`docs/audits/bt_cold_read_screenshots/`. Walk along with me.

---

## 1. Cold-read context

I'm here because dashboards aren't working. I don't know what Recon-Gen
*is*; I read "validation tool" in a Confluence stub somewhere and parked
that. My ETL is the thing I own — Airflow DAG, a couple of staging tables,
a Python `etl_hook` somebody scaffolded for me. The Studio is the only UI I
have. I'm on `qsgen-sqlite`, whatever that is — I assume it's the dev
fixture instance. I'm not going to read the SPEC.md before clicking
around; nobody does that. I'm going to react to what's in front of me.

---

## 2. `/etl/` landing — three cards, no compass

**Screenshot:** `bt_cold_read_screenshots/01_etl_landing.png`

Clean enough. Three cards: Probe / Run / Triage. The blurb tells me the
flow: probe one slice, run pipeline + score, triage gaps. But — I just
landed here. I don't actually know what I'm supposed to do *first*. The
blurb reads like a description of three tools, not a workflow. Am I
supposed to Probe before I Run? After? Is Triage only useful post-Run? If
the intended sequence is "Run → Triage → Probe individual gaps → fix L2 →
Run again," **tell me that**. A numbered "1. Run 2. Triage 3. Probe & fix"
with arrows would orient me in one read.

The card subtitles are also tool-shaped, not task-shaped. "Investigate one
L2 slice" — investigate what? "Execute the ETL pipeline (wipe → hook →
matview refresh)" — wait, **wipe**? In passing? Buried in a parenthetical
on a card I might just click? That's the kind of detail that should be its
own warning, not a sub-clause.

Also: I have no idea what "L2" means yet. The tabs reference "L2 Editor"
and "L2 Flow Tracing." The blurb says "L2-declared column expectations"
and "link back to the L2 editor." I'm guessing it's a YAML config, but
I'm guessing. First-time ETL engineer doesn't know your internal naming
convention — "L1 / L2 / L3" is your layering vocabulary, not mine. From
my seat, "L2" sounds like an OSI layer.

The breadcrumb says `qsgen-sqlite` — what's that? Deployment name?
Dataset? DB? I'd want a hover-tip or a one-liner under the breadcrumb
("dialect: sqlite • deployment: qsgen-sqlite"). It's mysterious code
furniture floating at the top of every page.

Top nav reads: Recon-Gen / L2 Editor / **ETL Support** / Training / L1
Dashboard / L2 Flow Tracing / Investigation / Executives. As a new ETL
engineer the L2 Editor / L1 Dashboard / L2 Flow Tracing trio reads as
identical vocabulary — I literally cannot tell whether L2 Editor is "the
schema config" or "the L2 Flow Tracing app." Naming collision.

---

## 3. `/etl/probe` — across three slice kinds

### 3a. Initial state — empty form, opaque vocabulary

**Screenshot:** `bt_cold_read_screenshots/02_etl_probe_initial.png`

"Slice" is a weird word for "thing in your L2." Rail I can guess (payment
rail). Transfer Template — uh, a kind of money movement event? Chain —
multi-leg sequence? I'd want one-line descriptions next to each radio.
Right now I'd have to go read the L2 yaml just to know what's what.

The default radio is **Rail**, which is the right default — rails are the
most common kind. But then the Name dropdown is empty (`— pick one —`).
How many rails are there? Are these alphabetized? Grouped by status
(has-data vs no-data)? The dropdown is the entire UX of slice selection
and it's a flat list with no hint. From the Run page I already know there
are **30 rails** declared; making me find one in a flat 30-item dropdown
without filtering, status badges, or search is asking me to scroll-hunt.

Date defaults to last 7 days. Helper text says "widen for backfill /
mass-load scenarios" — useful, but if I'm a backfill engineer my whole
job is mass-load and 7 days is the wrong default. Could you remember my
last choice? At minimum show a "Last 30 days / Last 90 days / All time"
quick-pick chip row above the date inputs.

No indication that the picker is filtered to L2-declared values vs.
anything seen in the runtime. If my ETL writes `rail_name = "FooBar"` but
the L2 doesn't declare FooBar, can I probe it? Or is the picker the L2
universe and a renegade rail in my data is just invisible? That's a
question with operational stakes — invisible bugs are the worst kind.

### 3b. Rail with data — but the window doesn't catch it

**Screenshot:** `bt_cold_read_screenshots/05_probe_rail_green_InternalBalanceMaintenance.png`

> **Author note (2026-05-30):** the first screenshot capture of this
> URL caught a **transient HTTP 500** (the themed dashboards 500
> handler — "Something went wrong"). The screenshot was retaken after
> the cold-read agent's first pass and the page renders cleanly. The
> 500 didn't reproduce across 5 follow-up curl + Playwright probes;
> hypothesis is DB / matview lock contention during the Run-ETL
> pipeline's matview-refresh phase, which happened concurrently with
> the first screenshot script. **Tracked as a P3 flake worth
> investigating but not the headline finding.** The cold-read below
> is the persona's reaction to the corrected (200) screenshot.

OK so I picked the one rail the Run page said had data
(`InternalBalanceMaintenance`, the lone green check in
`03_etl_run_initial.png`) and the page loads cleanly. Left panel
**Expected (from L2)**:

```
COLUMN                       OP    EXPECTED
rail_name                    =     InternalBalanceMaintenance
account_role                 ∈     {ExternalCounterparty,
                                    InternalSuspenseRecon}
metadata.source_transfer_id  ≠     NULL
→ Edit in L2
```

Right panel **Observed (window)**:

> **No rows match this slice.** The L2 declares this rail / template /
> chain but the ETL hook hasn't produced any matching rows in the
> window 2026-05-24 → 2026-05-30.

Wait — what? The Run page just told me this rail was the ONE with data
(`1 of 30 (3%)` and the only green check). Now Probe tells me there's
nothing? **The two pages disagree, and that's because they use
different windows.** Run shows all-time coverage; Probe defaults to
the last 7 days. The seed-data anchor is `2030-01-01` so my real data
sits outside any reasonable "last N days" window — and nobody on the
page tells me that.

This is the **single most disorienting moment of my cold-read so far.**
The two pages purport to answer "do I have data for X?" and they
give me opposite answers. If I trusted Probe over Run, I'd believe my
ETL is dead. If I trusted Run over Probe, I'd doubt the Probe tool.
Either way I lose trust in the suite.

**The Probe page does helpfully nudge me:**
- "Widen the window — backfill / historical loads may live outside
  today's default."
- "Check Run + coverage to see when the last ETL ran."
- "If the last run was recent, this slice may be a real ETL gap."

But "widen the window" assumes I know my data is outside the 7-day
default — and I just got here. Why isn't the window auto-set to
"window that brackets the most-recent posting" or simply "All"? Why
7 days specifically? The helper text under the picker explains
WHEN to widen but not why the default is 7. And I'd expect the page
to autopromote me to "no rows in 7d, but here's how the last 90d
look" rather than leaving me to manually fiddle.

**Other observations from the corrected screenshot:**
- The `account_role ∈ {ExternalCounterparty, InternalSuspenseRecon}`
  row is genuinely useful — it tells me which roles a row of this
  rail should hit. But "∈" is math notation; "is one of" or "in" would
  read more naturally.
- `metadata.source_transfer_id ≠ NULL` — same issue, "≠" is math
  notation. "must be set" or "required" reads cleaner.
- The "Edit in L2" link is well-placed (right under the contract
  table) and gives me a way to verify the L2 declaration.
- The contract is **3 rows**. That's the entire expected-side. I
  was expecting more — every column on the transactions table where
  this rail's contract has a constraint. Is 3 rows really
  representative of what InternalBalanceMaintenance declares?

### 3c. Rail with no rows — empty state lands cleanly

**Screenshot:** `bt_cold_read_screenshots/06_probe_rail_red_CustomerInboundACH.png`

This is the failure-mode UX and it actually works for me. Left panel
**Expected (from L2)** shows a tidy contract table:

```
rail_name                  =   CustomerInboundACH
account_role               ∈   {CustomerDDA, ExternalCounterparty}
metadata.external_reference ≠  NULL
metadata.originator_id     ≠   NULL
metadata.customer_id       ≠   NULL
```

That table is **exactly** what I want as an ETL engineer. The operators
(`=`, `∈`, `≠`) are math-y but unambiguous; even if I'd never seen the L2
spec I can read this. The metadata-key column names are concrete — I can
go grep my hook for `originator_id` right now.

Right panel **Observed (window)** shows the empty-state message and three
bullets:

- Widen the window — backfill / historical loads may live outside today's
  default.
- Check Run + coverage to see when the last ETL ran.
- If the last run was recent, this slice may be a real ETL gap. Open
  Triage.

This is **genuinely good copy**. It gives me three branches in plain
language and links me forward. The "Open Triage" link from here back to
the Triage page is the obvious next step.

Minor friction: "the L2 declares this rail / template / chain but the ETL
hook hasn't produced any matching rows" — the "rail / template / chain"
trio is template-y phrasing because the page didn't bother to pick the
right word for the slice I picked (I picked Rail; just say "rail"). Small
copy nit.

The little `→ Edit in L2` link under the Expected table is the right
escape hatch. It's a passive offer, not pushy. Good.

### 3d. Transfer Template — same shape, but where's the per-leg map?

**Screenshot:** `bt_cold_read_screenshots/07_probe_template_MerchantWeeklyPayoutBatch.png`

The Template view looks structurally identical to the Rail view, which is
nice — same Expected/Observed split. The Expected table shows:

```
template_name              =   MerchantWeeklyPayoutBatch
rail_name                  ∈   {MerchantWeeklyBatchClose}
metadata.merchant_id       ≠   NULL
metadata.payout_batch_id   ≠   NULL
```

But Templates are multi-leg. The L2 declares a template as a set of legs,
each with its own role and (per my prior reading) its own rail expectation.
This view collapses all of that into "rail_name ∈ {MerchantWeeklyBatchClose}",
which is the *set* of rails the template might touch, not the per-leg
map. That's a real loss of information if I'm debugging "I emitted the
right rail but on the wrong leg." Specifically I'd want:

```
leg_role        |  expected rail_name  |  expected account_role
----------------|----------------------|------------------------
batch_open      |  ...                 |  ...
batch_close     |  MerchantWeeklyBatchClose | ...
```

Even just expanding the `{...}` set into a list of (role → rail) pairs
would make this useful. As is, it tells me less than the L2 yaml does for
the cognitive cost of clicking into the page.

Also: the radio label says "Transfer Template" but the breadcrumb and the
page chrome still say "Probe." No mode indicator. Once I'm in the page the
selected radio is the only signal of what mode I'm in. Easy thing to
miss when I come back to a tab.

### 3e. Chain — what's the unit?

**Screenshot:** `bt_cold_read_screenshots/08_probe_chain_ACHOriginationDailySweep.png`

Chains are the most foreign concept of the three. The Expected table:

```
parent             =   ACHOriginationDailySweep
child              =   ConcentrationToFRBSweep
kind               =   Required (singleton)
transfer_parent_id ≠   NULL
```

OK, so a Chain is the (parent, child) edge between two transfer events
linked via `transfer_parent_id`. That's a useful model — but the page
doesn't tell me that anywhere; I'm inferring from the columns. The "kind
= Required (singleton)" reads like good L2 vocabulary but I don't know
what alternatives exist. Optional? Fanout? Mass? A hover tip on `kind`
showing the small enum would help.

Cross-comparing the three slice views: Rail and Template feel parallel.
Chain reads as a different beast (it's about *relationships* not
*rows*). I'd put the Chain selector in a different tab altogether, or at
least make the Expected table for a Chain look visually distinct (an arrow
diagram? parent → child?) so I know I'm in a different conceptual frame.

The Observed-window empty-state is the same copy for all three slice
kinds. That's fine for the no-rows path; less fine if Chain ever has a
true happy state — does Observed show the actual edges that landed? Did
they all pair up? Are there orphan parents? Without a green-state
screenshot I can't tell. (The default window also misses the seed-data
anchor, so I'm rendering "no rows" in a fixture that very much has
chains-with-data — same window-mismatch as the rail case.)

---

## 4. `/etl/run` — the anxious page

### 4a. Initial state — a destructive button I don't trust

**Screenshot:** `bt_cold_read_screenshots/03_etl_run_initial.png`

This is the page that **made me anxious**. There's a "Run ETL" button. I
just got here. I have no idea what it does. The card on the landing page
told me "wipe → hook → matview refresh." So clicking that button **wipes
the demo DB**? Without a confirmation? Without telling me what it's about
to do *on this page*? That's a footgun.

Coverage panel below is fantastic actually — Rails 1/30, Templates 1/3,
Chains 0/9 — I can see at a glance how dead my pipeline is. Red ✗ vs
green ✓ on the right is clear. Metadata panel showing "2/4 keys • missing:
card_network_ref, card_brand" is exactly the granular hint I need.

But the page composition is confused:

- Button label "Run ETL" doesn't tell me it's destructive.
- "No runs yet" + a Run button + a populated Coverage panel below is
  confusing. If there's been no run, where did Coverage come from? Is
  Coverage computed from current DB state regardless of who put it
  there? Then the page header lies — there *have* been runs, just not
  through this UI.
- "Coverage report green = ETL contract satisfied. Not green? → Open
  Triage" — good footer, but I'd put it AT THE TOP next to the giant
  button. As a first-time landing the footer is the last thing I read.
- The Rails column is overwhelming — 30 declared, almost all red ✗. I
  want a "show me only the failures" toggle. Or a "show me the wins"
  toggle. Either gives me a focus mode.
- The "0 of 9 declared (0%)" for Chains contradicts what the L2 yaml
  declares (5 chains, per the fixture). Where's "9" coming from? Are
  individual chain *edges* counted separately from chain *roots*? If so,
  that's not labeled. I'd expect "0 of 5" or "0 of N (chain edges)".

And the bottom Metadata block — "2 of 4 required metadata keys landed
across non-empty templates (50%)" — is good information but the
denominator math is weird. It says 2/4 but the per-template breakdown
shows 0/4, 2/4, 0/2. So it's 2 keys landed total across one template,
out of 4+4+2 = 10 expected. The "50%" reads as 2/4 which is one
template's score, not the aggregate. I'd want the header to say "Metadata
coverage" with the per-template detail below, no roll-up. Roll-ups that
disagree with their own subtotals make me distrust the page.

### 4b. After clicking — was I right to be anxious?

**Screenshot:** `bt_cold_read_screenshots/12_etl_run_post_click.png`

So I clicked it. I'm now staring at:

- Header bar: green checkmark, "succeeded at 2026-05-30 09:57:09.83 — got 1
  inserted row" (or similar — the timestamp + counts read).
- A **Last-run log** panel, which is a sequence of `event/log/etl-stage-...`
  lines, raw and unstyled. I can read them — they're stage breadcrumbs
  like `etl-stage-start`, `etl-stage-truncate-cleared`, `etl-stage-hook-completed`,
  `etl-stage-matview-refreshed`, etc. Useful as a transcript, but the
  rendering is a wall of text with no level (info/warn/error), no
  duration, no row counts per stage. If I'm debugging a slow ETL hook
  I'd want a per-stage timing column.
- Coverage panel reloaded. Now shows higher counts — though *still*
  mostly red, because the demo hook is a stub.

**Was I right to be anxious?** Partly. Nothing exploded — the run
succeeded, my data is "intact" (it was the fixture's seed). But:

1. **No confirmation dialog.** I clicked "Run ETL" and it ran. If I were
   on a customer DB, that wipe would have been destructive of real ETL
   state. The dry-run-by-default-then-`--execute` convention the CLI uses
   should be mirrored here: a "Preview" button (shows what'd run) +
   "Run" button (does it), or a confirmation modal with a typed
   acknowledgement for prod environments.
2. **No "this is the dev fixture, you can't break anything" reassurance.**
   The page chrome doesn't tell me I'm in `qsgen-sqlite` (dev / safe)
   vs. some prod alias. The breadcrumb shows the deployment name but
   doesn't classify it as "safe to wipe" vs. "wipe at your peril."
3. **No undo, no snapshot.** A "snapshot before this run / restore last
   snapshot" affordance would massively de-risk the button.

The log itself is reassuring after the fact — I can see the stages
fired in order. But the post-click experience would be even better if it
told me up-front: "ran 12 stages in 4.3s; truncated 60k rows; inserted
60k rows; refreshed 9 matviews." A summary line above the log.

Also: the log auto-collected and the page reloaded silently. I'd have
liked a transient flash ("Run finished, refreshing coverage…") so I'm
sure the panel below me is *current state* not *stale pre-run state*.

Coverage post-run looks like... almost the same as pre-run? Most rails
still red. Which tells me the bundled hook is a placeholder that only
hits one or two rails. If I were a real operator I'd be confused — I just
ran ETL, why didn't more lights turn green? **The page should explain:
"You ran the bundled demo hook. To wire your real hook, edit
`<path>` and re-run."** Right now I have no idea where the hook code
*is*.

---

## 5. `/etl/triage` — density wall

### 5a. Top cards — the first wall

**Screenshots:** `bt_cold_read_screenshots/09_triage_top_cards.png`,
`bt_cold_read_screenshots/04_etl_triage_initial.png`

Holy density. The Triage page is populous — dozens of red cards. Persona
instinct: if I open this fresh and see 60 cards, I close the tab.

Top cards are all `⚠ Unmatched rail_name`. Each card has:
- Title (`Unmatched rail_name`).
- Sentence: "X rows arrived with rail_name='SomeName' but the L2 declares
  N rails in Rail."
- A `declared_rails:` list (the full set of L2-declared rails, in every
  card — that's 30 names per card, and they repeat in every card).
- A `sample:` block with example rows.
- An `Open Rails editor` CTA.

The repeating `declared_rails:` is the most expensive real estate on the
page and it's identical across all 60 cards. **Cut it.** Or put it in a
collapsible. Or show it once at the top of the page as context and
remove from cards. The cards are 90% noise and 10% signal because of
this.

Sample block: useful, but `sample: 5 rows from etl_hook_output @
2026-05-30T09:57:09.83` is meta-info, then the *actual* sample rows
underneath are also dense JSON-ish lines. I'd want a small table view
with columns, not a JSON dump.

Severity sort? Grouping? I'm staring at six cards that all have the same
title and same red triangle. Which one matters? "Unmatched rail_name"
where the rail is responsible for 10000 rows is wildly more urgent than
one where it's 1. The card title doesn't surface volume; I have to read
the sentence to extract "256 rows" vs. "8 rows." Make volume a badge.

### 5b. Middle cards — same shape, same density

**Screenshot:** `bt_cold_read_screenshots/10_triage_middle_cards.png`

More `⚠ Unmatched rail_name` cards. Same shape. Same problems. The
sample blocks are different per card (good, real data), the declared_rails
block is the same (bad, noise).

I notice one card title is different in the middle:
`⚠ Unmatched template_name` — different concept but visually
indistinguishable from the rail cards. Color, icon, layout all match.
**The four gap kinds** (`unmatched_rail`, `unmatched_template`,
`missing_limit_schedule`, `missing_metadata_key`) should each have a
distinct color or icon family. As-is I'm reading every card title twice
to figure out which kind it is.

Prose readability of the diagnosis sentence varies:

- **Unmatched rail_name**: "X rows arrived with rail_name='Foo' but the
  L2 declares N rails in Rail." → clear. Tells me what landed, what was
  expected, where to look.
- **Unmatched template_name**: same shape, swap "rail" for "template."
  Also clear.
- **Missing LimitSchedule**: "0 BatchPayoutClose rows arrived against
  CustomerLedger but no LimitSchedule covers the (parent_role,
  child_role) pair (..., ...). Limit-breach metrics for CustomerLedger
  may be 'no-op'." → reads okay but I have no idea what a LimitSchedule
  *is* on first encounter. Need a one-liner.
- **Missing metadata key**: I see fragments in the bottom screenshot like
  "0 BatchPayoutClose rows arrived against CustomerLedger but
  LimitSchedule covers the … ID may be 'no-op'." Wait, that overlaps
  with LimitSchedule too. The card titles and prose start running
  together as I scroll.

### 5c. Bottom cards — different shape (Limit schedules), same disorientation

**Screenshot:** `bt_cold_read_screenshots/11_triage_bottom_cards.png`

The bottom of the page shifts from unmatched-name cards to:

- `⚠ Unmatched template_name` (still)
- `⚠ Missing LimitSchedule`

Card titles now show new kinds in the same visual treatment. CTAs change
to `Open Templates editor` and `Open Limits editor` respectively — at
least the CTAs are kind-specific, which is good. But the visual
distinction between an "Unmatched template_name" (which is "your data
sent something L2 doesn't know about → fix L2 or fix ETL") and a
"Missing LimitSchedule" (which is "your data sent something L2 *does*
know about but the limits config is silent on it → only fix L2") is
totally different conceptually. Same red triangle.

The 4 gap kinds and how they read:

| Gap kind | Diagnosis clarity | Action clarity | Jargon |
|---|---|---|---|
| `unmatched_rail` | Clear — row count + name + L2 had N | Clear — Open Rails editor | None (after first read of "rail") |
| `unmatched_template` | Clear — same structure | Clear — Open Templates editor | None |
| `missing_limit_schedule` | OK — describes the (parent_role, child_role) pair | OK — Open Limits editor | "LimitSchedule" undefined; "no-op" jargon |
| `missing_metadata_key` | OK — names the missing key | Probably the L2 templates section, but I can't tell from here | "metadata key" is fine but the consequence isn't stated |

**Winner:** unmatched_rail / unmatched_template — these read cleanly and
the action is obvious.

**Loser:** missing_limit_schedule — I don't know what LimitSchedule does,
so "your metrics may be no-op" doesn't motivate me. Add one sentence:
"LimitSchedules tell the L1 invariant matviews how much volume is
'expected' on each (parent, child) leg — without one, the
limit-breach matview emits zero rows for this pair, so dashboards under-report."

### 5d. The CTA destination — L2 Editor home

**Screenshot:** `bt_cold_read_screenshots/13_l2_editor_home.png`

Each card has an `Open Rails editor` / `Open Templates editor` / `Open
Limits editor` CTA. I clicked one and... I landed on the L2 Editor
**home page**, not on the specific rail/template/limit I was triaging.

That's a major flow break. The Triage page had concrete context — "your
data sent rail_name='Foo' but the L2 doesn't know it" — and the
destination is the *general* L2 editor with dozens of cards (looks like a
schema map: Account types, transfer templates, rails, accounts,
chains...). I now have to find "Foo" myself, or find the place to add a
new rail, or figure out which template section to edit. The breadcrumb
trail from Triage is **lost**.

The L2 editor home is itself dense — looks like a card-per-concept grid
plus a topology diagram at the top. As a first-time user this is the
*entire schema* in one view. It might be the right canonical view for
someone who knows the model, but for someone arriving from a Triage card
with one specific edit to make, this is a "where am I" page.

What I'd want:
- The CTA deep-links to the specific entity. `Open Rails editor` →
  `/l2/rails#Foo` with the Foo row highlighted/scrolled-to. If Foo
  doesn't exist yet (this *is* an unmatched rail!), open the "add new
  rail" form with `name=Foo` pre-filled.
- A back-breadcrumb: "← back to Triage" sticky at the top of the L2
  editor when I arrived from a triage card.
- A "what brought me here" inline note: "From Triage: Unmatched
  rail_name 'Foo' (256 rows). Add this rail to close the gap."

Without these, the L2 editor is a context-free wilderness. I'll get
disoriented within 30 seconds and either give up or accidentally edit
the wrong thing.

---

## 6. Cross-page friction

### 6a. The first-time setup loop

*"I just wired up my ETL hook. What do I click first?"*

Tracing the screenshots:

1. Land on `/etl/` (`01_etl_landing.png`). Three cards. I *should* click
   Run first to fire my hook. The page doesn't tell me that. Coin flip
   — let's say I correctly guess Run.
2. `/etl/run` (`03_etl_run_initial.png`). "Run ETL" button stares at me.
   Coverage panel below shows pre-existing state. I'm anxious about the
   button. I read the footer ("Coverage report green = ETL contract
   satisfied"), decide it's the canonical action, click it.
3. Post-click (`12_etl_run_post_click.png`). Log appears, coverage
   refreshes. Still mostly red. **I don't know if that's because my hook
   is wrong, or because the bundled demo hook only covers 2 of 30
   rails.** The page doesn't tell me whose hook just ran. **This is the
   biggest single failure point in the first-time loop.**
4. I click into Triage (`04_etl_triage_initial.png`) because the footer
   told me to. 60 cards.
5. I pick the first one, click "Open Rails editor" → I'm in the L2 Editor
   home (`13_l2_editor_home.png`) with no breadcrumb back.

Friction summary for first-time setup:
- **No clear "1, 2, 3" sequence on landing.**
- **No "your hook vs. the demo hook" disambiguation on Run.**
- **No deep-link from Triage to the specific entity.**
- **No "I'm new — show me the tutorial" link anywhere.**

The Probe page is theoretically the right tool for first-time setup
("hey, my CustomerInboundACH hook ran but produced no rows — why?"), but
the window-mismatch bug (Probe defaults to 7d, real data lives outside)
means I'd never get a happy state to anchor my mental model — even on
the one rail Run says is green.

### 6b. The debugging loop

*"Dashboard X shows 0 rows. What do I open?"*

Tracing the screenshots:

1. Dashboard is broken. I navigate to ETL Support (top nav).
2. Land on `/etl/` (`01_etl_landing.png`). Three cards. **Which one's
   the debugger?** Triage sounds right. Probe sounds right. Run *isn't*
   right (don't want to wipe). The landing card subtitles don't tell me
   which is the debugger. I guess Triage.
3. `/etl/triage` (`04_etl_triage_initial.png`). 60 cards. None of them
   say "Dashboard X" by name. I have to know that Dashboard X is fed
   from matviews fed from templates fed from rails... and find the
   right card.
4. I switch to Probe, pick a rail, see "No rows match this slice" because
   the default window doesn't bracket my data
   (`05_probe_rail_green_InternalBalanceMaintenance.png`), widen the
   window manually, then look at the side-by-side.

The debugging loop assumes I know the data lineage. **From a dashboard
zero-row to a Triage card is several inferences.** A "what feeds this
dashboard" reverse-link from each dashboard's Info sheet to the ETL Support
section would close this gap dramatically. Or a Triage filter: "show me
gaps that affect [dashboard]."

I'd guess wrong if my zero-row dashboard is investigating customer ACH
volume. I'd open Triage, find a card about CustomerInboundACH, click
"Open Rails editor" — and now I'm in the L2 editor home with no idea
whether L2 actually declares CustomerInboundACH or whether I'm being
told to add it. (In the fixture L2 *does* declare it; it's just
zero-row. The Triage card title "Unmatched rail_name" implies it's
unknown to L2 — but the rail *is* in the L2 dropdown on the Probe page.
There's an inconsistency between Probe's view of the world and Triage's.)

### 6c. Round-trip through the L2 editor

Once I'm in the L2 editor (`13_l2_editor_home.png`), can I get back?
There's no "← back to Triage" breadcrumb. The Studio breadcrumb at the
top says "Studio / L2 Editor" — not "Studio / ETL / Triage / L2 Editor."
So my history is mediated by the browser back button alone. That's
fragile — if I make an edit and the L2 Editor reloads, my back button
goes weird.

The L2 Editor itself is rich. I see Account / Transfer Template / Rails /
Limit Schedules / Chains sections. The diagram at the top is the
"topology" view. As an orienting page for someone who knows the model
it's probably great. For someone arriving from a Triage card, it's a
distraction — I just want to add one rail and get back.

---

## 7. Top concerns + non-landing concept names

### P1 blockers (5)

1. **Run and Probe disagree about whether a rail has data.** Run shows
   all-time coverage (`InternalBalanceMaintenance` = green); Probe
   defaults to last-7-days and shows "No rows match this slice" for
   the same rail. The seed-data anchor is `2030-01-01` so any
   reasonable "last N days" window misses it entirely. Neither page
   tells me the windows differ. I lose trust in both tools the
   moment I notice. **The default window needs to be derived from
   actual data presence (e.g. max(posting) backward N days) or
   default to "All" with a chip to narrow.**
2. **"Run ETL" button has no confirmation, no preview, no
   safe/destructive labeling.** Mirrors the CLI's `--execute` discipline
   inconsistently.
3. **Post-Run coverage panel doesn't tell me whose hook ran.** First-time
   operators will conclude their hook is broken when actually the
   bundled demo hook is the one that fired.
4. **Triage card CTAs deep-link to the L2 Editor home, not to the
   specific entity.** Context is lost between Triage and L2 Editor.
5. **No back-breadcrumb from L2 Editor to Triage.** Round-trip flow is
   broken.

### P2 friction (8)

1. Landing page reads as three tools, not a workflow.
2. Probe radio labels lack one-line definitions ("Rail / Transfer
   Template / Chain" — what's the difference?).
3. Probe Name dropdown is a flat 30-item list; no status badges, no
   filter, no "remember last."
4. Date defaults to last 7 days; no quick-pick chips for 30 / 90 / All
   time.
5. Run page Rails column needs a "show failures only" toggle.
6. Run page Metadata roll-up math (`2 of 4`) disagrees with the
   per-template breakdown.
7. Triage `declared_rails:` block repeats in every card (30 names ×
   ~60 cards = enormous noise).
8. Triage cards lack volume badges (256 rows vs. 8 rows reads identically
   in the title).

### P3 polish (8)

1. Breadcrumb `qsgen-sqlite` needs a hover-tip explaining what it is.
2. Probe Empty-state copy says "rail / template / chain" — should pick
   the right word for the selected slice.
3. Run page log has no per-stage timings or level (info/warn/error).
4. Run page log appears with no transient flash; user is unsure if
   coverage below is current.
5. Triage 4 gap kinds visually identical (same red triangle, same
   layout) — distinct color/icon per kind would help scanning.
6. Sample block in Triage cards is a JSON-ish blob — should be a small
   columnar table.
7. Chain probe Expected table is text-only — a small parent → child
   arrow diagram would communicate the concept faster.
8. **Transient HTTP 500 on Probe under matview-refresh contention.**
   First screenshot pass caught a "Something went wrong" themed 500
   on the same URL that rendered cleanly seconds later. Hypothesis:
   DB lock contention with the concurrent Run pipeline's matview
   refresh. Not reproducible across 5 follow-up probes; worth
   investigating + adding a retry-on-lock-busy on the SELECT path,
   or a friendlier "this slice is recomputing, try again in a few
   seconds" message instead of the dashboards 500 page.

### Non-landing concept names

These are vocabulary I (the persona) had to *infer* meaning for. None
were defined where I first encountered them:

| Term | First seen on | Did I get it? |
|---|---|---|
| **L2** | top nav, landing blurb | inferred YAML config; ambiguous with "L2 Flow Tracing" tab |
| **Slice** | Probe page (`02_etl_probe_initial.png`) | sort of — "thing in the L2 you can probe" |
| **Rail** | Probe radio + Run Coverage panel | guessed "payment rail," confirmed by names |
| **Transfer Template** | Probe radio + Run Coverage panel | guessed correctly; some uncertainty re: multi-leg |
| **Chain** | Probe radio + Run Coverage panel | only understood after reading the Chain probe (`08_...`) |
| **Hook** | landing card subtitle + log entries | inferred "the Python entrypoint that loads data" |
| **Matview** | landing card subtitle + log entries | only understood because I'm SQL-fluent; non-SQL users wouldn't |
| **Limit Schedule** | Triage `Missing LimitSchedule` cards | did NOT understand; jargon-heavy |
| **Coverage** | Run page Coverage panel | got it from context |
| **Predicate** | (not seen on these screens) | n/a |
| **`qsgen-sqlite`** | breadcrumb on every page | did NOT know — fixture? dialect? deployment? |
| **Singleton** (in chain `kind`) | Chain probe | got it from "(singleton)" parens — but unsure if there are other kinds |
| **"no-op"** | Limit Schedule diagnosis | got it but it's jargon |

Recommendation: a **glossary popover** keyed to bolded terms in copy,
or a small "?" hover-tip next to each term on first encounter per
session.

---

## 8. What's good

I want to be fair — there are real wins here. Don't lose these:

- **The Probe red-state side-by-side layout is excellent.** Expected
  table on the left, Observed panel on the right, the three-bullet
  branch-out copy at bottom. If the green-state worked, this would be
  the most informative debug surface in the app.
- **The Triage diagnosis sentences are mostly clean prose, not stack
  traces.** "X rows arrived with rail_name='Foo' but the L2 declares N
  rails" — that's a human sentence. Compare to typical ETL tools that
  hand you a Python traceback. Big win.
- **The Run page Coverage panel** is honestly the best part of the whole
  flow. At-a-glance counts (1/30, 1/3, 0/9) + per-item ✓/✗ + a metadata
  roll-up below = I know exactly how dead my pipeline is in 3 seconds.
- **The CTAs in Triage are kind-specific** ("Open Rails editor" /
  "Open Templates editor" / "Open Limits editor"). Even though they
  destination is wrong (home, not entity), the *labeling* shows the
  designer thought about per-kind action.
- **The L2 Editor topology diagram** at the top of `13_...` (small
  network graph) is a nice 30-second "what is this schema" view, even
  if it's the wrong landing for a Triage round-trip.
- **The `→ Edit in L2`** link tucked under the Expected table on Probe is
  a tasteful passive offer — not a giant button, just an opt-in. Good
  taste.
- **Default radio = Rail.** Right call — rails are the most common slice.
- **Date helper text mentions backfill.** Acknowledges the real-world
  scenario where 7 days is wrong. Small thing, but it signals the
  designer thought about the operator's life.
- **Empty-state Probe copy with three bullets** (widen window / check
  run / open triage) is the kind of next-step plumbing that
  distinguishes good tools from "here's a blank panel, good luck."

---

## 9. Recommendations

Framed as persona reactions (what I'd expect / what I'd want), not
implementation prescriptions.

### P1 (5 items) — ship-blockers for me to trust the page

1. **Reconcile Probe and Run windows.** I'd expect both pages to
   answer the same "does X have data?" question the same way. Make
   Probe's default window derive from `max(posting)` minus N days,
   or default to "All" and let me narrow with a chip. Today's 7-day
   default means I see "no rows" for a rail that Run just told me
   was green. I'd close the tab.
2. **Add a confirmation step to "Run ETL"** — either a "Preview run"
   button that shows what will happen, or a modal that says "This will
   truncate `<prefix>_transactions` and `<prefix>_daily_balances`, run
   the hook at `<path>`, and refresh 9 matviews. Continue? [Cancel]
   [Run]". I'd expect any button that wipes a table to ask me before
   firing.
3. **Tell me whose hook ran.** I'd expect the post-run summary to say
   "Ran the bundled demo hook (`recon_gen._dev.etl_hook.demo_hook`).
   To wire your real hook, see `<docs link>`." Right now I have no idea
   the run was a dry placeholder.
4. **Deep-link Triage CTAs to the specific entity.** I'd expect "Open
   Rails editor" on a card about rail 'Foo' to take me to a Foo-focused
   editor view (or "add Foo" form if it doesn't exist), not to the L2
   editor home.
5. **Add a back-breadcrumb from L2 Editor → Triage when arriving via a
   CTA.** I'd expect "← back to Triage" sticky at the top until I
   commit an edit.

### P2 (8 items) — friction that I'd grumble about

1. **Numbered workflow on landing.** I'd expect "1. Run, 2. Triage, 3.
   Probe & fix" arrows on the landing page instead of three equal cards.
2. **One-line definitions next to Probe slice radios.** I'd expect "Rail
   — a single payment movement primitive" / "Transfer Template — a
   multi-leg event" / "Chain — a parent→child relationship between
   transfer events."
3. **Probe Name dropdown filtering.** I'd expect a search box and ✓/✗
   status badges so I can find "rails with no data" in one scan.
4. **Date quick-picks.** "Last 7d / Last 30d / Last 90d / All time"
   chips above the date inputs.
5. **"Show failures only" toggle on Run Coverage.** I'd expect to
   collapse the green entries so I can focus on what's broken.
6. **Fix Run Metadata roll-up math** or explain the denominator.
   "2 of 4" disagreeing with `0/4 + 2/4 + 0/2` makes me distrust the
   panel.
7. **Strip `declared_rails:` from individual Triage cards.** Show once
   at top of page or in a collapsible. ~60× redundant block is the
   biggest readability hit.
8. **Volume badges on Triage cards.** I'd expect the card title to
   show row count: "Unmatched rail_name • 256 rows."

### P3 (8 items) — polish

1. **Hover-tip on `qsgen-sqlite`** breadcrumb explaining dialect +
   deployment.
2. **Probe empty-state copy** should pick the right word for the slice
   ("rail" not "rail / template / chain").
3. **Per-stage timings + level in Run log.**
4. **Transient flash after Run** so I know coverage refreshed.
5. **Distinct color/icon per Triage gap kind.**
6. **Columnar sample-row view** in Triage cards instead of JSON dump.
7. **Arrow diagram for Chain Probe Expected** (parent → child visual).
8. **Investigate the transient Probe HTTP 500** caught in the first
   screenshot pass. Hypothesis: DB lock contention with Run's
   matview-refresh phase. Either add a retry-on-lock-busy or a
   "this slice is recomputing, try again" message instead of the
   dashboards-styled 500 page.

### Workflow-shaped recommendations (orthogonal to P-tier)

- **First-time tutorial path.** A "First time here?" banner on `/etl/`
  that walks me through Run → Triage → fix one rail → re-run. Even a
  5-step inline checklist would orient new operators.
- **Reverse-link from dashboards.** Each broken dashboard should
  point me to the ETL Support gap that's causing it. Right now I
  have to know the lineage.
- **Glossary popover** keyed to bolded terms (L2, Rail, Hook, Matview,
  LimitSchedule, Chain, Slice, Singleton).
- **Snapshot/restore around Run.** Even a "last 3 runs" rollback list
  would massively de-risk the destructive button.

---

## TL;DR for the operator who didn't read the rest

The Probe page is **structurally well-designed**, but it gives a
contradictory answer to Run's coverage panel — Run says rail X has
data, Probe (defaulted to last 7 days) says no rows. The seed-anchor
sits years outside any reasonable "last N days" window, so the two
pages disagree on a basic question. The Run page is **anxiety-
inducing** — destructive button with no confirmation, ambiguous about
whose hook ran. The Triage page is **info-dense and signal-rich** but
loses operator context the moment I click a CTA (lands in L2 Editor
home, no breadcrumb back). Landing page reads as three tools, not a
workflow. Concept vocabulary (L2, Slice, LimitSchedule, Hook, Matview)
is undefined at first encounter. The Coverage panel on Run is the
single best UI element across all four pages and should set the tone
for the rest.

Five P1s, eight P2s, eight P3s. Fix the **Probe/Run window
disagreement first** — without aligned answers between the two
pages, the whole ETL Support story falls apart. One transient
HTTP 500 was caught in the first screenshot pass; couldn't
reproduce, tracked as P3 flake worth investigating.
