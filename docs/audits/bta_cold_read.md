# BTa Cold-Read — ETL Support, Second Walkthrough

**Persona:** First-time ETL engineer at a midsize credit union. Hands-on
Python + SQL. Never seen Recon-Gen before today. Someone (a senior dev, the
vendor, a Slack message) handed me the URL to the self-hosted Studio and
said: *"land your ETL feed cleanly so the dashboards work."* I have a
half-written hook that drops rows into `<prefix>_transactions` and
`<prefix>_daily_balances`. I want to know whether it's working.

This is my second-walkthrough reaction. I didn't read the BT writeup — the
operator told me Phase BTa changed a bunch of things (Refresh Data button,
numbered landing, accordion Triage, polished Probe, sub-nav, live tail,
side-panel glossary) and asked me to react fresh. Screenshots live under
`/tmp/bta_coldread/`. Walk along.

This revision integrates 15 follow-up screenshots that landed after the
first pass, so anything that was a "need a screenshot to react" placeholder
is now either confirmed, contradicted, or upgraded into a new finding.

---

## 1. Cold-read context

Same scene as before. Dashboards aren't working. I own the ETL hook. I'm
on `qsgen-sqlite`, which I'm guessing is a dev fixture. I'm not going to
read the SPEC; I'm going to react.

What's immediately different from BT: the page chrome reads more like a
*workflow* now. The landing page is numbered. There's a sub-nav across
the ETL pages so I always know where I am inside the loop. The Probe
date inputs default to "All time" instead of last-7-days. The Triage page
is condensed to four accordion rows instead of 60 cards. Big visible
upgrades; my job is to find the new sharp edges.

---

## 2. `/etl/` landing — numbered cards, banner, but…

**Screenshots:** `01_etl_landing.png`, `19_etl_landing_banner_dismissed.png`

The numbered cards (`1. Refresh Data` → `2. Triage gaps` → `3. Probe & fix`)
do exactly what I wanted on the first cold-read. I land here and I
immediately know the order. Arrows between cards reinforce the
left-to-right reading. Good.

The "First time here? Walk the loop ▼" banner at the top is the right
move too. It's collapsible (`Show the 5-step checklist`), it's
dismissable, and the 5 steps are short. Reading the steps in the
banner:

1. Configure your ETL hook — Set ETL_HOOK in your config.yaml (or skip — the bundled demo regenerates Sasquatch data when the hook is unset).
2. Refresh Data — Click Refresh Data to run your hook, then the matview refresh. The coverage report shows every L2-declared primitive (rail / template / chain / metadata-key) with the observed row counts.
3. Triage the gaps — If anything missed, the Triage view groups gaps by kind. Each card carries the diagnosis + a deep-link to the L2 editor's create-new form, with a one-click "Back to Triage" breadcrumb that survives the save.
4. Probe a single slice — Use Probe to investigate one specific entity — pick a rail name, set the date window (defaults to All time), and see the L2-declared contract next to the runtime rows. Faster than running the whole pipeline when you're iterating on one fix.
5. Re-run + repeat — Edit the L2, click Refresh Data again, watch the coverage tally close. The loop tightens as you go — most operators hit clean coverage on the third pass.

I really like step 5 framing it as a loop. That's how this surface
actually works.

**Persona snag on the banner itself:**

- The banner text contains a typo / line-wrap artifact: *"Probe a single slice — Use Probe to investigate one specific entity — pick a rail name, set the date window…"* — the em-dash before "Use Probe" reads like a sentence break but it's just elaboration. Minor copy nit.
- The banner is *long*. Five paragraphs of dense prose at the top of the landing page. I'd want the steps to be MORE terse — three to five words each, clickable to expand. Right now it's "tutorial as a wall of text." First-time persona might just dismiss it without reading.
- The "Dashboards" link is present in the top nav but I notice the landing tutorial doesn't tell me when to actually GO LOOK at a dashboard. The whole point of fixing the ETL is that downstream dashboards work — but the loop closes at "clean coverage" without sending me to verify a dashboard renders. Step 6 should be "Open a dashboard tab and confirm a real number renders."

**Banner-dismissed state confirmed (`19_etl_landing_banner_dismissed.png`):**

After I click X on the banner, the landing becomes very clean:

```
Studio · ETL Support   qsgen-sqlite

Three steps to land your customer's ETL feed cleanly. Walk them in
order on a first pass; once you know the surface, jump anywhere via
the numbered cards.

[1 Refresh Data /etl/run] → [2 Triage gaps /etl/triage] → [3 Probe & fix /etl/probe]
```

This is the right resting state. The two-line tagline above the cards
("Three steps to land… Walk them in order on a first pass; once you
know the surface, jump anywhere via the numbered cards") is **actually
better copy than what's inside the banner** — terse, action-shaped,
acknowledges both the first-time flow and the experienced flow. I'd
strongly consider making the dismissed state the default and putting
the long checklist behind an explicit "Show the 5-step checklist"
button rather than auto-expanded on first load. **P2 ask:** make the
banner default-collapsed, lead with the dismissed-state tagline.

Also: the `Studio · ETL Support` breadcrumb + `qsgen-sqlite`
deployment chip now appears explicitly in the dismissed-state shot.
That's the right placement — the deployment chip sitting next to the
breadcrumb (not floating in the chrome) makes it feel like context,
not magic furniture. Confirms the BT P3.1 ask is partially addressed
(still no hover-tip explaining what `qsgen-sqlite` *is*, but the
visual placement is right).

**Top nav restructure:**

The top nav is now clearly grouped with `STUDIO` / `DASHBOARDS` / `REFERENCE`
section labels (small caps badges in green tint). I see:

```
Recon-Gen | STUDIO: L2 Editor | ETL Support | Training |
DASHBOARDS: L1 Dashboard | L2 Flow Tracing | Investigation | Executives |
REFERENCE: Docs | [?]
```

This is **a real improvement** over the flat nav from BT. The grouping
tells me ETL Support sits in the BUILD half of the editor, not the VIEW
half — which is exactly the mental model I want as a first-time operator.
Win.

The `[?]` at the far right is the new glossary drawer trigger. It's
**very subtle** — I almost missed it. A `?` in brackets reads as a
keyboard-shortcut hint or a debug toggle, not "click me to open the
glossary." Would expect "❓ Help" or "Glossary" or even a tooltip on
hover. As-is I only know what it is because the operator briefed me.
**P2 friction:** the highest-value learning surface on the page is
behind the most cryptic icon.

---

## 3. `/etl/run` — the page that used to be scary

**Screenshots:** `02_etl_run_idle.png`, `05_run_midrun_live_tail.png`,
`06_run_postrun_flash.png`, `16_run_log_zoom_timings.png`,
`17_run_coverage_failures_only.png`, `18_run_metadata_detail.png`

This page had the most claimed changes; round 2 corroborates almost
all of them. Below I confirm + add new findings.

### 3a. The new button is dramatically better

The "Run ETL" button is now `↻ Refresh Data`. That single rename does
80% of the work of BT P1.2 (the "destructive-button-no-confirmation"
problem). "Refresh Data" reads as a re-derive / re-pull operation, not
a wipe. Even though it still truncates + re-loads under the hood, the
*framing* of "refresh" tells me what I'll see: the data on the next
page reflects my hook's most recent output. The CTAs in the success
banner reinforce this — `success at <timestamp> — got X transactions`
or similar.

The button is also now PROMINENTLY at the top of the page, where it
belongs. In BT it was buried alongside everything else. Good.

### 3b. Sub-nav makes the ETL surface feel like a coherent app

The new sub-nav row is the second-biggest win on this page:

```
↻ Refresh Data | ⚠ Triage | 🔍 Probe | ← Loop overview
```

Four tabs, current one underlined. I always know where I am inside the
ETL loop, and I can jump between Refresh / Triage / Probe without going
back to the landing page. The "← Loop overview" back-link is in
the right place (rightmost, with the back-arrow). This pattern should be
the convention for any multi-page workflow surface in Studio.

**Nit:** the icons are inconsistent. Refresh Data has a unicode `↻`,
Triage has `⚠`, Probe has `🔍` (an emoji?). Mixing geometric glyphs with
emoji feels chaotic. Pick a family.

### 3c. The context strip nails the BT P1.3 ask — and the demo plant disclosure

The "What clicking Refresh Data will do" context strip is one of the
biggest wins this round. Looking at the post-run state
(`06_run_postrun_flash.png`):

```
WHAT CLICKING ↻ REFRESH DATA WILL DO
DEPLOYMENT: qsgen-sqlite
DIALECT: sqlite
ETL HOOK: (none configured — bundled demo regeneration will run)
          + demo gap overlay (phantom rail / template / missing
          metadata + uncovered rail/template DELETEs)
```

**This single addition resolves my biggest round-1 P1.** The orange
secondary line literally tells me "the bundled demo intentionally
plants phantom gaps so Triage has content." That kills the panic
hypothesis cold — when I get to Triage and see 16,823 missing
LimitSchedule rows, I will (eventually) connect the dots that this is
plant data, not my hook misbehaving. **However**, see §4a: the
plant-disclosure copy lives on the Run page but the panic moment
happens on the Triage page; the disclosure needs to be echoed there
too. Right now I have to remember context from a page I clicked
through.

**Nits:**

- "ETL HOOK: (none configured — bundled demo regeneration will run)"
  is correct but it doesn't tell me HOW to swap it for my real hook.
  A `→ Wire your own hook (docs)` link inline would close the loop.
- The orange demo-overlay disclaimer is great vocabulary but the
  parenthetical itself is dense: "(phantom rail / template / missing
  metadata + uncovered rail/template DELETEs)". As a first-time
  persona I have to slow down and parse "uncovered rail/template
  DELETEs" — that's L2-author vocabulary. Reword as plain English:
  *"plus deliberately broken records so the Triage view has examples
  to walk through."*

### 3d. Last-run log — per-stage timings + log levels confirmed

Round 1 I asked for a zoom-in to verify the per-stage Δms timings + log
levels actually rendered. `16_run_log_zoom_timings.png` confirms they
shipped and **the rendering is the right shape**:

```
       [INFO] deploy:step2:wipe:start db_table_prefix=qsgen_sqlite dialect=sqlite
+29ms  [INFO] deploy:step2:wipe:done transactions_deleted=76238 daily_balances_deleted=3928
+0ms   [WARN] deploy:step1:skip reason=etl_hook not configured
+0ms   [INFO] deploy:step3:generator:start scope=full end_date=None seed=None
+6667ms[INFO] deploy:step3:generator:done transactions_written=76487 daily_balances_written=3928
+0ms   [INFO] deploy:step4:matviews:start db_table_prefix=qsgen_sqlite dialect=sqlite
+5524ms[INFO] deploy:step4:matviews:done
+0ms   [INFO] deploy:step5:reload:bump data_generation_id=8
```

Three columns in scan order: **Δms** (right-aligned, monospace) → **level
badge** (`[INFO]` / `[WARN]` in a tinted pill) → **event name + kv
pairs**. This is the right column order — when scanning a long log my
eye anchors on the level badges first (to spot WARNs/ERRORs) and the
timing column second (to spot slow stages). The +6667ms generator and
+5524ms matview steps pop visually. Win.

**New findings from the zoom:**

- **P3 polish:** the first row has no Δms (because it's the anchor).
  An empty cell reads odd; consider `   0ms` or `start` in muted text
  to keep the column visually consistent.
- **P3 polish:** `step1:skip reason=etl_hook not configured` is a
  `[WARN]` but the message is actually informational ("we used the
  demo because you didn't configure a hook" is expected demo
  behavior). A WARN badge for *expected demo state* is a false
  positive — I'll tune out the badge if it cries wolf. Demote to
  `[INFO]` when the hook absence is the configured state.
- **P2 friction:** step numbering reads `step2:wipe` → `step1:skip` →
  `step3:generator` → `step4:matviews` → `step5:reload`. Stage 1 fires
  *between* stage 2 and stage 3. That's the order they actually
  execute in, but the *numbers* suggest the wrong order. Either
  renumber by execution order, or drop the step numbers and let the
  Δms-cumulative timeline carry the ordering.
- **The `[INFO] deploy:step5:reload:bump data_generation_id=8` final
  line** — what does that *mean* to me as a persona? "Reload bump" is
  vocabulary for the engine that pushes the new data through the
  matview cache. Either give it a friendly final line ("Done — 9
  events, 12.2s total") or drop the `bump` event from the visible log.
  Final-line-as-engine-internal makes the run feel unfinished even
  though it succeeded.

### 3e. Coverage — verified, plus the failures-only toggle works

`17_run_coverage_failures_only.png` confirms the "Show failures only"
toggle is there and it does the right thing. With the toggle ON I see:

```
Coverage ☑ Show failures only

Rails (29 of 30 declared, 97%)
  ZBASweep                                  ✗

Templates (2 of 3 declared, 67%)
  MerchantWeeklyPayoutBatch                 ✗

Chains (7 of 9 declared, 78%)
  MerchantSettlementCycle → MerchantWeeklyPayoutBatch    ✗
  MerchantDailySettleAggregator → MerchantWeeklyPayoutBatch ✗

Metadata (8 of 10 required metadata keys landed, 80%)
  MerchantWeeklyPayoutBatch                 0/2 keys × no rows
```

This is **the best possible state of this panel.** Filtering to the
~5 failures across all four categories means I instantly know what to
fix: ZBASweep rail, MerchantWeeklyPayoutBatch template, two chains
involving it, and the metadata keys it should have carried. The
toggle defaults to OFF (showing all) but I would consider defaulting
to ON when coverage is >90% and one or two failures are the actual
signal — at 80% coverage the unfiltered view is mostly green noise.

**New findings from the failures-only view:**

- The Chains panel renders chain edges as `Parent → Child` text. That's
  readable but a quick **legend ("→ means parent triggers child")**
  would help first-timers — without context "MerchantSettlementCycle →
  MerchantWeeklyPayoutBatch" looks like a Python import or a CSV row.
- The metadata failure renders `0/2 keys × no rows` in red. The `× no
  rows` qualifier is important — it tells me the template was missing
  required keys *because no rows of that template arrived at all*, not
  because rows arrived without the keys. **But that distinction is
  buried in the failure annotation.** The two failure modes ("no rows
  → all keys missing by construction" vs "rows present but keys
  missing") are very different bugs with very different fixes. **P2
  ask:** elevate the distinction into the row layout — color-code
  "no-rows" failures differently from "rows-without-keys" failures.

### 3f. Metadata roll-up math — BT P2.6 confirmed fixed

`18_run_metadata_detail.png` shows:

```
Metadata
8 of 10 required metadata keys landed (80%)

InternalTransferCycle           4/4 keys ✓
MerchantSettlementCycle         4/4 keys ✓
MerchantWeeklyPayoutBatch       0/2 keys × no rows
```

4 + 4 + 0 = 8. 4 + 4 + 2 = 10. 8/10 = 80%. **The math agrees with the
subtotals now.** BT P2.6 is closed. The "× no rows" annotation on the
failing template is the right tell — it tells me WHY this template got
0/2 (no rows arrived to carry the keys) instead of leaving me to wonder
if my hook is dropping the metadata keys specifically.

### 3g. Mid-run state — live tail + Cancel works

`05_run_midrun_live_tail.png` shows the mid-run state I was waiting on.
What's there:

```
✗ Cancel run    ● Pipeline running — live events below.

Live event tail
[INFO] deploy:step2:wipe:start db_table_prefix=qsgen_sqlite dialect=sqlite

No Refresh Data run this session.
Coverage only renders after a Refresh Data click in this Studio session —
pre-existing rows from prior sessions / CLI runs aren't auto-trusted.
Click ↻ Refresh Data above to populate.
```

**What's good:**
- **`✗ Cancel run` is red and prominent** — exactly the right placement
  (where the Refresh Data button was, now swapped out). I can't
  accidentally re-click Refresh Data while a run is firing.
- **The amber dot + "Pipeline running" status line** is the right
  affordance. Color-coded, conversational.
- **Live event tail panel** with the current stage streaming in.
  First event line is rendered ~immediately so I know my click took.
- **The Coverage panel remains empty + carries the "No Refresh Data run
  this session" copy** — Coverage isn't rendering stale data mid-run.
  Conservative + correct.

**What still concerns me (new P-tier asks):**

- **P1: What does Cancel actually do?** Mid-run cancel of a destructive
  pipeline is *the* danger zone. The screenshot shows the button but
  doesn't tell me whether Cancel:
  (a) lets the current stage finish + stops before the next one (safe-ish),
  (b) hard-kills the subprocess mid-stage (could leave half-truncated tables),
  (c) prompts me to confirm with a "this will leave partial data" warning.
  **A confirmation modal on Cancel** is one of the few places I'd
  actively *want* a modal — wiping data, then aborting halfway through
  re-loading it, is the worst possible state for the DB. Mid-run UX is
  also where the user's BT comment about "I HATE modals" cuts the
  other way — the modal on Cancel is asymmetric with the
  no-modal-on-Run choice, but the asymmetry is correct because the
  destructive cost of aborting mid-run is much higher than the cost
  of initiating one.
- **P2: No indication of total stages or progress.** The status reads
  "Pipeline running — live events below" but I don't know if I'm 10%
  in or 90% in. Even a simple `Stage 2 of 5` counter (since the log
  format shows step numbers anyway) would let me decide whether to
  walk away or wait it out. For real customer ETLs that take 10+
  minutes, this is critical.
- **P2: What if I navigate away?** The mid-run shot is captured on the
  Refresh Data tab. If I click Triage in the sub-nav, does the run
  continue? Is there a sticky "RUN IN PROGRESS" badge anywhere in the
  chrome (top nav? sub-nav?) so I know to come back? Tab title
  pulse? The operator notes for BTa mentioned tab-title flashing but
  I can't tell from this screenshot whether it's wired.
- **P3 polish:** the live-event-tail wrapper says `Live event tail`
  while the post-run version of the same panel is labeled `Last-run
  log`. Two names for the same panel is mild friction; pick one.

### 3h. Post-run flash state — coverage renders cleanly

`06_run_postrun_flash.png` is the post-run resting state, taken right
after the run completes:

```
✓ success at 2026-05-30T17:47:02
gen 9 · 76,487 transactions
hook: (bundled demo regeneration — no operator hook configured)
```

Three lines:
- Green checkmark + timestamp.
- `gen 9 · 76,487 transactions` — generation counter + row count.
- `hook:` line *echoing the context-strip hook line*. Belt-and-suspenders
  confirmation that the bundled demo ran. Good.

The Coverage panel is fully populated below. Showing 29/30 rails,
2/3 templates, 7/9 chains, 8/10 metadata — most things green, a few
red. **That's the right post-run state for a happy first-time run.**

What's good:
- **`gen 9 · 76,487 transactions`** — the gen counter is invisible
  vocab (data_generation_id) BUT having a small monotonic counter
  visible in the success banner gives me a way to verify "yes, my
  click did update something" beyond just trusting the timestamp.
- The "hook:" line echoing the context strip is good redundancy.

What I'd nit:
- **P3: `2026-05-30T17:47:02`** is ISO-with-T. Persona-friendly is
  `today at 17:47` or `2026-05-30 17:47:02`.
- **P3: "76,487 transactions"** — comma-separator is right (BT had
  raw 76487). But I'd add "rows" or "events" so I'm not guessing
  what the count is of. Coupled with the gen counter: `gen 9 ·
  76,487 transactions written` reads better.
- **P2: Did anything flash?** I asked round 1 about CSS flash /
  tab-title pulse. Static screenshot can't show animation, so I
  can't verify. **If it didn't flash, add it** — the page chrome
  doesn't change much between mid-run and post-run other than
  swapping the red Cancel button for the green Refresh Data
  button, and a brief highlight on the Coverage panel would draw
  my eye to "the new state is here."

---

## 4. `/etl/triage` — accordion replaces the wall

**Screenshots:** `03_etl_triage_no_run.png`,
`07_triage_expanded_unmatched_rail.png`,
`08_triage_expanded_missing_limitschedule.png`,
`09_triage_expanded_unmatched_template.png`,
`10_triage_expanded_missing_metadata.png`

**Holy shit, this is dramatically better.** Where BT had ~60 dense
cards, this is FOUR rows:

```
51 gaps across 4 kinds.

⊘  Unmatched rail_name          13 rows total · 4 distinct
⚠  Unmatched template_name      17 rows total · 2 distinct
☒  Missing LimitSchedule        16,823 rows total · 42 distinct
☐  Missing required metadata key 21 rows total · 3 distinct
```

The accordion groups all gaps of one kind under a single row that I can
expand. Each kind has:
- A distinct icon (⊘ / ⚠ / ☒ / ☐ — four different glyphs)
- A title in clear language ("Unmatched rail_name", "Missing
  LimitSchedule")
- A row count and distinct-value count

This is **exactly the BT P2.7 ask** — "Strip declared_rails from
individual cards / find a common group-by method." Win.

### 4a. The 16,823-row bomb — somewhat defused, not fully

Round 1 I flagged "Missing LimitSchedule: **16,823 rows total · 42
distinct**" as the top P1 because first-time persona reads "my data is
broken in 16,823 places" and panics.

Round 2 updates:
- **The Run page context strip now explicitly says "demo gap overlay
  (phantom rail / template / missing metadata + uncovered
  rail/template DELETEs)"** (`06_run_postrun_flash.png`). That helps a
  lot — *if I read the Run page first and remember it when I get to
  Triage.*
- **The Triage page itself still does not echo the demo-plant
  disclosure.** Header reads "51 gaps across 4 kinds." with no
  context for whether the count is real customer-shaped data, demo
  plants, or a mix. The 16,823 LimitSchedule number sits there
  unannotated.

**P1 still standing:** the panic moment is on the Triage page, not on
the Run page. The disclosure has to be where the panic happens. Add a
top-of-page banner / subtitle: *"Showing **51 real gaps** + **N
demo-planted gaps** for tutorial purposes. The plants live in
the LimitSchedule and metadata kinds; the rail / template kinds are
real."* Color-code planted rows differently from real ones in the
expanded views.

**Plant-vs-real evidence inside the expansions** (now I can see):
- `09_triage_expanded_unmatched_template.png` shows two cards with
  template names `MerchantDailySettleAggregator` and
  `orphan_settlement_batch`. The second card has a `sample tx` of
  `__demo_gap_phantom_tmpl_000`. **The `__demo_gap_phantom` prefix
  in the sample tx ID is the only signal that this card is a plant.**
  That's invisible to a first-time persona — they don't know the
  convention.
- `07_triage_expanded_unmatched_rail.png` shows 4 cards: `_spine_client`,
  `legacy_card_swipe`, `__demo_null_foi_tx1_paa`,
  `__demo_null_foi_tx2_pa`. **Two of the four cards have `__demo_*`
  prefixed names** but the cards visually look identical to the
  real-ish `_spine_client` and `legacy_card_swipe` cards (which I'm
  guessing are also plants based on the leading underscore).

**P1 ask:** the demo plants need a visible **`DEMO PLANT`** badge on
each card, with an explanation either inline ("planted by the bundled
demo for tutorial purposes — does not represent your ETL output") or
linked to the docs. Without that, the persona reads every card as a
real bug to fix.

### 4b. Expanded view: Unmatched rail_name (`07_...`)

The expansion lays out cards in a 2-column grid. Each card has:

```
┌── (vertical color stripe, orange / amber) ─────────────────────┐
│ _spine_client                                      7 rows      │
│ 7 rows arrived with rail_name='...' but no L2 declares it.     │
│                                                                 │
│   sample tx       tx-fanin-extra-...                           │
│   declared_rails  ACHOriginationDailySweep,                    │
│                   ConcentrationToFRBSweep, CustomerInbound...  │
│                                                                 │
│ [→ Open rails editor]                                          │
└────────────────────────────────────────────────────────────────┘
```

**Round-1 wishlist items, now confirmed:**
- **Vertical color stripe per kind** (orange/amber for unmatched_rail
  in this shot) — landed. Combined with the icon system it gives me
  two scan signals per row. Win.
- **`declared_rails:` list collapsed/folded into the card** — landed
  but in a different shape than I expected. The list now sits inside
  each card as a one-line wrap rather than the dense per-card block
  it was in BT. **Still feels redundant** (same `declared_rails`
  appearing in 4 cards on this view is still noise). The original BT
  ask was "move it OUT of cards" — what shipped is "make it less
  obnoxious inside the card." Half the win.
- **Sample row** is now a 1-line summary (`sample tx`
  `tx-fanin-extra-...`) instead of the BT JSON dump. **Cleaner, but
  also less informative** — the BT version showed *which fields* were
  set on the row. The new version shows only the transaction ID. P2
  ask: bring back the column breakdown (account_role, signed_amount,
  posted_at, metadata keys present) but as a 4-column mini-table, not
  JSON.
- **CTA `→ Open rails editor`** — kind-specific button. Same as BT.
  See §6 for the destination check.

**New findings from the expansion:**

- **P3: `7 rows arrived with rail_name='...' but no L2 declares it.`**
  — the `'...'` is the rendered placeholder where the actual rail
  name should go. The card title above ALREADY shows the rail name
  (`_spine_client`), so this isn't a bug — it's redundancy
  optimization. But persona reads the sentence and goes "huh, what's
  the rail name?" before noticing it's the title. Either inline the
  name in the sentence (`7 rows arrived with rail_name='_spine_client'
  but no L2 declares it`) or drop the sentence entirely since the
  title carries the same info.
- **P3: `sample tx` shows ONE transaction ID per card.** Round 1 BT
  cards showed multiple sample rows. One sample is fine for the
  common case (I can copy-paste the ID and grep my source data),
  but for cards with 256+ rows, one sample isn't representative.
  Consider showing `1 of 7` with arrow buttons to flip through.

### 4c. Expanded view: Unmatched template_name (`09_...`)

Same card shape as rail. Two cards:
- `MerchantDailySettleAggregator` (15 rows): `15 rows tagged with
  template_name="MerchantDailySettleAggregator" — no such template in
  the L2.`
- `orphan_settlement_batch` (2 rows): `2 rows tagged with
  template_name="orphan_settlement_batch" — no such template in the L2.`

**The sentence template differs from the rail version** ("X rows
arrived with rail_name='Y' but no L2 declares it" vs "X rows tagged
with template_name='Y' — no such template in the L2"). Two cards on
adjacent pages saying the same shape of thing in two different
sentence shapes is friction. Pick one phrasing pattern and replicate.

**`declared_templates`** is shown inline (3 names, comma-separated)
which is much more reasonable than the 30-rail list on the
unmatched_rail card. The smaller list naturally compacts.

**`orphan_settlement_batch` card's `sample tx` = `__demo_gap_phantom_tmpl_000`**
— this is the strongest "this card is a plant" tell I see in any
expansion. The naming is honest but only if you know the convention.

### 4d. Expanded view: Missing LimitSchedule (`08_...`) — the giant

This screenshot is **1.27 MB** and renders as a tall wall of cards.
I cannot read individual card content at the natural zoom — the
page is so dense it has to be visually scanned rather than read.

**This is itself a finding:** the operator briefed me to flag this,
and the same problem will hit a real first-time operator. The 16,823
rows are grouped into 42 distinct cards by `(parent_role, child_role)`
pair; even at the new compact card density that's a wall.

**P1 ask:** when a single accordion kind exceeds ~20 cards, the
expanded view needs to either:
1. Paginate ("Showing 1-20 of 42 distinct pairs · [next 20] · [show all]"), or
2. Default to grouped-by-second-axis (e.g., group by parent_role with
   a row count badge → expand one parent_role at a time), or
3. Show a search/filter input at the top of the expansion.

As-is, expanding "Missing LimitSchedule" hits the persona with a
~2000px-tall wall of nearly-identical cards. The cognitive cost is
high and the marginal value of the 21st card is approximately zero.
**The accordion redesign solved the per-kind density problem; it did
not solve the per-distinct-value density problem inside the expansion.**

Also: most cold-read operators run on a laptop screen (1440×900 or
1366×768). A card grid that's 2 columns × N tall is paginated by the
viewport at ~3 rows visible. Scrolling through 21 viewports of cards is
not a real workflow. **The expansion needs viewport-aware density.**

### 4e. Expanded view: Missing required metadata key (`10_...`)

Three cards visible, each is a (template, metadata_key) pair:
- `InternalTransferCycle: internal_transfer_id` — `15 rows...`
- `MerchantSettlementCycle: merchant_id` — `... rows...`
- `MerchantSettlementCycle: settlement_period` — `... rows...`

The card titles use **`Template: metadata_key`** colon notation,
which is the right disambiguation when one template has multiple
missing keys. Good.

Cards show:
- Diagnosis sentence
- `sample tx` + likely the metadata-keys-actually-present
- `→ Open templates editor` CTA

The metadata-key cards are visually consistent with the rail /
template cards (same color stripe, same card chrome). Good
consistency.

**Same P3 ask as 4b applies:** the diagnosis sentence is partially
redundant with the title. Trim or merge.

### 4f. The icon system

Four distinct icons across the four kinds (⊘ / ⚠ / ☒ / ☐). Visual
scan works — I can tell the four rows apart at a glance. Now that I
can see expanded views, the icons + color stripes pair up nicely:
each kind has its own (icon, stripe-color) tuple. **Good.**

Nits:
- ☒ vs ☐ for "Missing LimitSchedule" vs "Missing required metadata
  key" — both look like unchecked checkboxes. Hard to distinguish at
  a glance. Now I have evidence to triage: ☒ for the kind with the
  biggest count is fine (it pops as "deliberately struck through" vs
  the empty box), but the icons themselves are font-dependent and
  may render inconsistently across browsers/OSes. Consider SVG icons
  with semantic differences (a list-with-gap icon for LimitSchedule,
  a key icon for metadata-key).

### 4g. The page header — still missing context

This page reads `51 gaps across 4 kinds` with no context for what
produced the count. Now that I've seen the Run page acknowledge the
demo overlay, the Triage page should echo it. Header copy ask
(combining round-1 + round-2 findings):

```
51 gaps across 4 kinds — including ~16,800 planted by the bundled
demo for tutorial purposes (see Refresh Data → ETL hook for details).
Real customer-shaped gaps: ~51. Click any row to expand.
```

### 4h. The `← Loop overview` sub-nav link

Same sub-nav as on Run page. Good consistency.

---

## 5. `/etl/probe` — polish landed, but the form is now busier

**Screenshots:** `04_etl_probe_initial.png`,
`13_probe_rail_selected_alltime.png`,
`14_probe_typeahead_open.png`,
`15_probe_chain_side_panel.png`

### 5a. Inline radio definitions — yes please

```
○ Rail
   one money-movement leg shape (e.g. ACH credit, internal GL move) — the lowest-level L2 primitive.
○ Transfer Template
   a multi-leg event template that bundles two or more rails into one logical transfer (e.g. card-purchase = auth + post).
○ Chain
   a parent → child dependency between transfers (e.g. ACH settlement triggers GL clearing 1-2 days later).
```

**This is the BT P2.2 win.** Three one-line definitions next to each
radio. The persona reads them once and the vocabulary lands. I now
understand:
- Rail = one leg of money movement
- Transfer Template = bundles legs into a logical event
- Chain = causal relationship between transfers

The examples (`card-purchase = auth + post`, `ACH settlement triggers
GL clearing 1-2 days later`) are particularly good — they ground the
abstraction in banking events I recognize. Excellent copy.

**Nits:**
- "Rail" definition says "the lowest-level L2 primitive." That word "L2"
  still appears. I still don't know what L2 is until I open the
  glossary drawer (which I might not find behind `[?]`).
- The Chain example "ACH settlement triggers GL clearing 1-2 days
  later" is good but actually contradicts the Chain model as I
  understand it from BT — a Chain is a parent→child between *transfer*
  events, not a time-delayed cascade. The "1-2 days later" framing
  makes it sound like a scheduled job, not a structural relationship.
  Worth a clarity pass.

### 5b. The Name input — typeahead behavior verified

`14_probe_typeahead_open.png` shows the Name input filled with `ACH`
and… the screenshot doesn't show a visible datalist dropdown. The
operator briefing mentioned WebKit may or may not render datalist
suggestions visibly — confirmed. **Persona implication:** if the
typeahead lives in `<datalist>` and WebKit doesn't render it, then on
Safari/WebKit users get *no visible feedback* that the input filters
anything. They type, see nothing, hit Apply with a typo, get an
empty Observed panel, and conclude the data is broken when actually
the input string didn't match anything.

**P1 trust-killer:** the Probe Name input must show visible
suggestions on click/keystroke. Three options:
1. Replace `<datalist>` with a custom typeahead component that
   renders the suggestions panel directly (most robust, most work).
2. Below the input, render `Matching 3 of 30 rails: ACHOrigination...,
   CustomerInboundACH, ...` as live text as the user types — at
   least the user gets feedback that the filter is working.
3. Add a "Show all rails" link next to the input that opens a
   modal/dropdown panel as a fallback affordance.

**Also missing:** the round-1 ask was for the typeahead to show
status badges (✓ has-data / ✗ no-data). Can't verify from this
screenshot since the dropdown isn't visible at all. **P2 once
typeahead rendering is fixed:** add status badges in the suggestion
list.

### 5c. Date defaults to All time + quick chips

```
FROM           TO          [Apply]
01/01/1900     05/30/2026

QUICK WINDOW:  [Last 7d] [Last 30d] [Last 90d] [All time ●]

Window defaults to All time (1900-01-01 → today). Pick a chip or set the date inputs to narrow.
```

**This is BT P2.4 + BT P1.1 in one shot.** The Probe and Run pages now
both default to "all" — they will agree on whether a rail has data.
The four quick-pick chips give me one-click narrowing for backfill
investigations. The helper text underneath is conversational and
actionable.

**Nits:**
- `01/01/1900` as the "from" date reads odd. I'd expect "All time" to
  mean "no start date" (an empty input or a placeholder text like
  `(no start)`) rather than a magic-number date. If I click into the
  From input and see `01/01/1900` I might think someone fat-fingered a
  year. Probably fine in practice but feels like a sentinel value
  leaking through.
- The chips' selected state (`All time` highlighted in green) is clear
  and well-styled. Good.
- The Apply button next to To-date is good — date pickers without a
  commit button are surprisingly hostile. Win.

### 5d. Rail-selected state — Expected vs Observed renders correctly

`13_probe_rail_selected_alltime.png` shows
`ACHOriginationDailySweep` selected with All-time window. The page
splits into:

**Expected (from L2):**
```
COLUMN                       OP    EXPECTED
rail_name                    =     ACHOriginationDailySweep
account_role                 ∈     {ExternalCounterparty, CashSettle...}
metadata.source_transfer_id  ≠     NULL
→ Edit in L2
```

**Observed (window):**
```
Showing 25 of 1568 rows in window 1900-01-01 → 2026-05-30.

TRANSACTION             POSTING            RAIL/TEMPLATE                 ROLE/        PREDICATE
                                                                         DIRECTION    FIT
tx-fixed-               2026-05-           ACHOriginationDailySweep      Customer     ✗ ≠ NULL
ACHOriginationDailySw...  04T19:11...                                    Settlement
                                                                         /Credit
... [many more rows]
```

**This is what I wanted in BT.** Side-by-side expected/observed for a
rail that has data. Two big wins from the round-2 view:

1. **The "Predicate Fit" column on the right shows ✗ for rows that
   fail the predicate.** That's the killer feature — I can scan a
   thousand-row Observed panel and instantly spot which rows fail
   which constraint. The footer note `Predicate fit: ✓ matches all
   declared L2 constraints, ✗ no value to evaluate` (or similar)
   wraps it up.
2. **`Showing 25 of 1568 rows in window`** — pagination disclosure.
   I know I'm seeing the tip of the iceberg. Good.

**New findings on the rail-selected view:**

- **P2: 1568 rows is great signal but where's the summary?** "1568
  rows total · 0 predicate failures" or "1568 rows total · 312
  predicate failures" as a one-liner above the table would tell me at
  a glance whether this rail is healthy. Right now I have to scan the
  ✗/✓ column visually across 25 rows × scroll.
- **P2: The column names in the table header are abbreviated
  unhelpfully** — `RAIL/TEMPLATE`, `ROLE/DIRECTION`. Stacked headers
  with slashes are how spreadsheets render but it makes
  copy-into-grep slow. Either spell out (one column per concept) or
  pick the dominant header for this slice kind ("RAIL" alone when
  probing a Rail).
- **P3: `Predicate fit: ✗ no value to evaluate`** — what does "no
  value to evaluate" mean? If `metadata.source_transfer_id` is NULL,
  is the predicate failure because the field is missing entirely
  (couldn't evaluate ≠ NULL) or because the value IS null? Wording
  unclear. **Predicate failure modes:** "missing field" vs "field
  present but doesn't satisfy" vs "field present + null" — three
  different bugs, three different fixes.
- **P3: Inline diff styling could amp the ✗ rows visually.** Right
  now ✗ is in red text but the row itself looks identical to ✓ rows.
  A light red background on the row, or red left border, would let
  me jump straight to the broken rows without column scanning.
- **P3: All time window shows 1568 rows.** That's a lot. I'd want a
  "show predicate failures only" toggle on this panel too (mirroring
  the Coverage "Show failures only" toggle on Run). Same pattern,
  same payoff.

### 5e. Chain-selected state — side-panel diagram trigger present

`15_probe_chain_side_panel.png` shows `ACHOriginationDailySweep`
chosen as a Chain. What I can see:

```
Chain: ACHOriginationDailySweep
ACHOriginationDailySweep
↓
Diagram (square icon)
↓
[Outgoing edges (1)]
{leaf-style chain pill: ConcentrationToFRBSweep
required (singleton)
transfer_parent_id ≠ NULL}

[→ Open chain diagram for the wider view]
```

Plus a small **"Open chain diagram for the wider view →"** link.

**This is exactly the round-1 ask** — the chain Probe view now has a
visual representation of parent → child relationship + a CTA to open
the full diagram. Win.

**New findings on the chain side-panel:**

- **P2: The diagram-link is small** and tucked at the top-right of
  the side panel. I almost missed it. Visual weight should be higher
  — the chain *is* fundamentally a visual concept, the link should
  invite the click rather than apologize for existing.
- **P2: The side panel renders parent → diagram → child in vertical
  stack.** Chain is "parent triggers child" — that's a horizontal
  relationship in my mental model (left → right reads as cause →
  effect). Vertical stacking reads as a list rather than a flow.
  Consider rotating the panel to horizontal (parent → child with the
  diagram tile between) so the geometry matches the semantics.
- **P3: `required (singleton)`** — "singleton" is still jargon.
  Inline-tooltip ("singleton: exactly one child per parent; other
  options: optional, fanout") would help.
- **The Observed panel rendered correctly for the Chain slice** too
  — showing the actual transfer rows in window. Same column layout
  as Rail. Consistency win.

### 5f. Persona snags on the Probe form composition

Reading the form top-to-bottom as a first-time user:

1. SLICE KIND fieldset with 3 radios — clear, definitions are gold.
2. NAME input — empty placeholder; typeahead may or may not render
   suggestions (P1 above).
3. FROM / TO dates pre-filled to All time → Apply.
4. QUICK WINDOW chips — All time pre-selected.
5. Helper text below dates.

That's a LOT of controls on one page now. The previous Probe was 3
fields; this is 7+ (3 radios + name + 2 dates + apply + 4 chips). The
information density bumped up. Operator notes from BT mentioned
"check information density once we've gotten through these changes" —
**flag**: this surface is approaching the limit of "scan in 5 seconds."
Consider collapsing the inline radio definitions to a hover-tooltip
once the user has dismissed them (localStorage flag).

Also notice: the radios are stacked vertically with their definitions
inline, eating a lot of vertical real estate. A two-column layout
(radio + def in one row) would compact this without losing the
definitions. **P3 polish.**

---

## 6. L2 editor deep-link from Triage — partially landed

**Screenshot:** `11_l2editor_with_back_breadcrumb.png`

The Triage card's `→ Open rails editor` CTA destination at
`/l2_shape/rail/new?from=/etl/triage`. What I see:

```
Create new rail   ← back to Studio   → list all rails

▶ ⓘ Reference

[Two-leg rail →]
Debit + credit per firing (ACH, wire, internal, settlement)

[Single-leg rail →]
One leg per firing (fee, charge, sub-template leg)
```

**What's good:**
- **`← back to Studio`** breadcrumb is present at the top, with the
  back arrow. ✓
- **Two clear options** (Two-leg / Single-leg) as the first decision.
  That's the right disambiguation for "what kind of rail am I
  creating?" — and the parenthetical examples (ACH, wire, internal,
  settlement for two-leg; fee, charge, sub-template leg for
  single-leg) ground the choice in things I recognize.
- **`→ list all rails`** secondary nav for "I actually wanted to
  browse first" escape hatch.

**What's wrong (P1):**

- **`← back to Studio` is the wrong target.** I arrived from
  `/etl/triage` with a specific gap to fix (let's say
  `_spine_client` rail). The back-breadcrumb should read `← back to
  Triage`, not `back to Studio`. The operator's BTa briefing said
  this was implemented as `?from=/etl/triage` — the URL parameter
  appears to be flowing through but the rendered breadcrumb label is
  generic. **Bug:** the `from=` query parameter is not influencing
  the breadcrumb label. Currently the page lands me as if I arrived
  from anywhere, losing the Triage context completely.
- **The rail name from the Triage card is not pre-filled.** I clicked
  the card for `_spine_client` rail and landed on a generic "Create
  new rail" form with no name pre-filled. The most important data
  point I had — the name of the rail to add — is not propagated.
  **Bug:** the deep-link should carry `?name=_spine_client&from=/etl/triage`
  and the create form should pre-fill the name input. As-is I have
  to copy-paste from the previous tab (which I closed because I
  thought the deep-link did this for me).
- **No "what brought me here" inline note.** Round 1 ask: "From
  Triage: Unmatched rail_name '_spine_client' (7 rows). Add this rail
  to close the gap." Nothing of the sort renders. The L2 editor is
  context-free wilderness as soon as I land.

**P2:**

- **The Reference panel is collapsed by default.** "▶ ⓘ Reference"
  takes a click to expand. For a first-time persona on a
  create-new-thing form, the reference should be open. Toggle the
  default.
- **The Two-leg / Single-leg choice has no preview of what the form
  looks like after I pick.** I want to know "if I pick two-leg, what
  fields will I have to fill in?" before I commit. A tiny inline
  preview ("Two-leg rails need: name, debit_role, credit_role,
  metadata_keys[]") under each option would let me make the choice
  with confidence.
- **No "save and return to Triage" button visible.** Once I fill the
  form, where do I land? The round-trip requires that I save → land
  back at Triage with the gap I fixed now greyed out / removed. If
  the form just saves and dumps me on `/l2/rails`, I lose my
  triage flow.

**P3:**

- **The breadcrumb `Create new rail` is the page title and the
  breadcrumb at the same time.** Visually unclear which it is.
  Consider a smaller breadcrumb above and a larger H1 below.

**Big-picture take:** the deep-link plumbing is half-shipped. The
URL parameter passes through and SOME UI affordances exist, but the
*name pre-fill*, the *contextual breadcrumb label*, and the *post-save
redirect back to Triage* are not wired. The persona arriving from
Triage hits the same disorientation as in BT, just one page further
in. **This is the round-2 P1 regression to flag.**

---

## 7. Glossary drawer

**Screenshot:** `12_glossary_drawer_open.png`

The `[?]` button does open a drawer. The drawer is a right-side
panel that overlays the page. Content visible (the screenshot crops
parts but the structure is clear):

```
Help (drawer header)

Chain
  Chain is a thing — see somefield…
  …

ETL Hook
  ETL Hook is your customer's hook that your customer's data hits...

L2
  L2 is your institution's modeling — accounts, account_templates,
  rails, transfer templates, chains, and limit schedules — declared
  in a (per-institution) YAML.
  …

Limit Schedule
  LimitSchedule is a kind of cap on per-time-bucket volume across…
  …

Matview
  Matview = a precomputed materialized view; the dashboards' data
  source. Refreshed only when you click Refresh Data.

Predicate
  Predicate is something L2-side that the runtime is required to
  satisfy. The Run page's Coverage panel and the Probe page's
  Predicate Fit column both reference predicates.
```

**Confirms:** the drawer is a real glossary — entries for the
vocabulary I struggled with in BT (Chain, ETL Hook, L2, Limit
Schedule, Matview, Predicate). Good content choices.

**New findings:**

- **P2: The drawer renders as right-side-overlay covering ~1/3 of the
  viewport.** The underlying ETL Support page is partially obscured
  on the right. That's tolerable for a quick reference dip but
  intrusive if I want to read a definition while looking at a
  Triage card. **Either**: (a) make it a side-by-side panel that
  resizes the main content (push, not overlay), or (b) make the
  overlay narrower and let me dock/undock.
- **P2: No search box.** I see a stack of definitions in some order
  (looks alphabetical) but with 8+ terms visible and probably more
  below the fold, I want `[Ctrl+K] search` or even just a top-of-
  drawer filter input. Especially as the glossary grows.
- **P2: Terms are not cross-linked.** "L2 is your institution's
  modeling — accounts, account_templates, rails, transfer templates,
  chains, and limit schedules" — the words "rails", "transfer
  templates", "chains", "limit schedules" should be clickable
  cross-references that scroll to those entries. As-is the glossary
  is a flat list of definitions; with cross-links it becomes a
  navigable concept map.
- **P3: Definition tone is uneven.** "Chain is a thing — see
  somefield…" reads as placeholder. "Matview = a precomputed
  materialized view; the dashboards' data source. Refreshed only
  when you click Refresh Data." reads as polished. Pass through
  every entry for tone parity.
- **P3: The drawer doesn't appear to be page-aware.** On
  `/etl/triage` I'd want the drawer to auto-scroll to "LimitSchedule"
  (the most-prominently-mentioned term on that page). Context-
  awareness would multiply the value of the surface.

**Discoverability ask reaffirmed:** the `[?]` icon needs to be more
obvious. The drawer is genuinely good content; persona just isn't
finding it. Rename to "❓ Glossary" or "❓ Help / glossary" in the
top nav.

---

## 8. Cross-page friction — round 2

### 8a. The first-time setup loop (revisited)

With round-2 evidence, tracing the new flow:

1. Land on `/etl/` (`01_etl_landing.png`). Numbered cards say "1. Refresh
   Data → 2. Triage → 3. Probe & fix". Banner is expanded by default
   (P2 ask: collapse). I click 1.
2. `/etl/run` shows the context strip with `qsgen-sqlite` /
   `sqlite` / `bundled demo + demo gap overlay` (✓ disclosure
   present). I click Refresh Data.
3. **Mid-run** (`05_run_midrun_live_tail.png`): red Cancel button
   replaces Refresh Data, amber dot + status line, live event tail
   streaming. No total-progress indicator (P2 ask). What Cancel does
   is unclear (P1 ask).
4. **Post-run** (`06_run_postrun_flash.png`): green success banner +
   gen counter + populated Coverage panel. Failures-only toggle works
   (`17_...`). Metadata math agrees with subtotals (`18_...`).
5. I click Triage (sub-nav). 4 accordion rows, distinct icons + color
   stripes, clear counts. **But:** the 16,823 LimitSchedule gap
   sits unannotated as "demo plant" — I have to remember the
   disclosure from page (2) to interpret it (P1 ask).
6. I expand "Unmatched rail_name" (`07_...`). Cards visible, two
   of four are demo plants but only the `__demo_` prefix in the
   sample tx ID gives that away (P1 ask).
7. I click `→ Open rails editor` on `_spine_client`. Lands on
   `/l2_shape/rail/new` with `← back to Studio` (wrong, should be
   Triage) and no `name=` pre-filled (P1 ask).
8. I navigate back via browser back, click Refresh Data again, watch
   coverage tally close.

**This is a far cleaner first-time loop than BT's.** The numbered
landing, sub-nav, context strip, accordion, log timings, failures-
only toggle all chain together. **But the L2 editor deep-link is the
weak link in the chain** — the most context-dense moment (I have a
specific rail to add, I clicked the CTA for it, I land on a generic
form) is where the loop breaks. Round 1 thought this was claimed
fixed; round 2 reveals it's half-fixed.

### 8b. The debugging loop

*"Dashboard X shows 0 rows. What do I open?"*

The landing page now reads more like a workflow, but that workflow is
*linear* (run → triage → probe). A debugger doesn't start at step 1 —
they start at "this specific dashboard is broken." The landing page
doesn't have an entry point for "I have a specific broken thing,
where do I go?"

A 4th card or an inline mini-section: **"Got a specific broken
dashboard? Start at Triage (groups gaps) or Probe (deep-dive on one
entity)."** Right now the implicit "start at 1" framing is great for
first-time setup and slightly wrong for debugging.

### 8c. The reverse-link gap — still there

From a broken dashboard, can I get to the ETL Support surface with
context? The BT writeup flagged this; I have no evidence BTa addressed
it. The dashboards don't appear to link back to ETL Support with "this
visual is fed by [X] which has Y gaps — fix in ETL Support →".

Still backlog-worthy. Not a regression, just unfinished.

---

## 9. What BTa changed vs. what regressed (updated with round-2 evidence)

| BT issue | BTa status (round 2 verified) |
|---|---|
| P1.1 Probe/Run window disagreement | **Fixed.** Both default to All time. Verified in `13_...`. |
| P1.2 "Run ETL" no-confirmation footgun | **Fixed.** Renamed to "Refresh Data"; framing is non-destructive. |
| P1.3 No "whose hook ran" disambiguation | **Fixed.** Context strip + post-run "hook:" echo line. Verified `06_...`. |
| P1.4 Triage CTAs land on L2 home, not entity | **Partially fixed.** Deep-link goes to `/l2_shape/<kind>/new` but the rail name from the card is NOT pre-filled. Verified `11_...`. |
| P1.5 No back-breadcrumb from L2 → Triage | **NOT fixed.** Breadcrumb reads `← back to Studio`, not `← back to Triage`, despite the `?from=/etl/triage` URL parameter. **Round-2 P1 regression.** |
| P2.1 Landing reads as 3 tools, not workflow | **Fixed.** Numbered cards + arrows. Tagline above cards is even better than banner copy. |
| P2.2 Probe radio labels lack definitions | **Fixed.** Inline one-liners with examples. |
| P2.3 Name dropdown flat 30-item list | **Half-fixed.** Switched to `<datalist>` but WebKit doesn't render suggestions visibly. Round-2 P1: typeahead suggestions invisible on WebKit. |
| P2.4 Date defaults to 7d, no quick-picks | **Fixed.** Defaults to All; chips for 7/30/90/All. |
| P2.5 No "show failures only" toggle on Run | **Fixed.** Verified `17_...`. |
| P2.6 Metadata roll-up math doesn't agree with subtotals | **Fixed.** 8/10 = 4+4+0. Verified `18_...`. |
| P2.7 declared_rails repeated in every card | **Mostly fixed via accordion redesign.** Per-card noise reduced but the list is still rendered inside each card. |
| P2.8 No volume badge on Triage cards | **Fixed.** "X rows" badge top-right of each card. |
| P3.1 qsgen-sqlite breadcrumb opaque | **Partially fixed.** Visually placed next to breadcrumb now, but still no hover-tip on hover. |
| P3.2 Probe empty-state copy slice/template/chain word | N/A — empty state is now slice-agnostic ("pick a slice"). |
| P3.3 Per-stage timings + level in log | **Fixed.** Δms + [INFO]/[WARN] columns rendered. Verified `16_...`. |
| P3.4 Transient flash after Run | Static screenshot can't verify CSS flash. Believe it landed but unverified. |
| P3.5 Distinct color/icon per gap kind | **Fixed.** Color stripes + icons per kind. Verified `07_...` / `08_...` / `09_...` / `10_...`. |
| P3.6 Columnar sample-row view | **Half-fixed.** Sample is now a one-line ID instead of JSON. Less noise, but also less informative — lost the field breakdown. |
| P3.7 Arrow diagram for Chain Probe Expected | **Fixed.** Side-panel diagram trigger + parent → child stack rendered. Verified `15_...`. |
| P3.8 Transient 500 on Probe | **No regression seen** in this pass. |
| Tutorial path | **Fixed.** "First time here?" banner with 5-step checklist. (Plus operator-facing P2: collapse by default.) |
| Reverse-link from dashboards | Unchanged (backlog). |
| Glossary popover | **Fixed via [?] drawer**, but discoverability + search + cross-links + context-awareness are new P2s. |

The BTa team really shipped. **Most BT P1s and P2s are addressed, and
the architecture moved in the right direction.** Round-2 evidence
overturns the optimism on P1.4 + P1.5 (only half-shipped) and exposes
a new P1 on Probe typeahead (WebKit datalist invisibility).

---

## 10. New issues introduced or surfaced by BTa — editorial summary

Round 2 raises **4 new P1s, 11 new P2s, 19 P3 polish items**. The full
ladder lives in §12 (round-2-specific) and the per-section findings
above (per-page detail). The headline shape:

- **The first-time loop is one half-shipped step away from clean
  end-to-end.** The L2 editor deep-link (§6) loses the rail name AND
  the back-to-Triage breadcrumb at the most context-dense moment —
  this single bug breaks the round-trip flow the whole BTa redesign
  was building toward. Round 1 thought P1.4 + P1.5 were closed; they
  aren't.
- **Two trust killers the baseline shots couldn't show.** Probe Name
  typeahead is invisible on WebKit (§5b) and Triage demo plants are
  visually identical to real gaps (§4a + §4b). Both compound the
  panic-or-dismiss reaction.
- **The mid-run destructive-pipeline UX has one missing piece:**
  Cancel semantics are undocumented (§3g). One modal in the right
  place (asymmetric with the no-modal-on-Run choice) closes it.
- **Glossary drawer + Triage expansion density** are P2s that share
  shape: both surfaces shipped right content but the chrome makes
  them awkward to actually use (no search/cross-links in drawer,
  no pagination/sub-grouping in expansion).

See §12 for the actionable triage ladder.

---

## 11. What's good (genuine wins)

Don't lose these:

- **Numbered landing cards with arrows.** Instantly tells me the
  workflow shape. BT P2.1 closed.
- **Dismissed-state landing tagline** is excellent copy. Make it the
  default.
- **"Refresh Data" rename.** Single rename absorbs the entire BT P1.2
  "destructive-button" anxiety arc.
- **Sub-nav across ETL pages.** Coherent navigation, current-page
  underlining, ← back-link in the right place.
- **"What clicking Refresh Data will do" context strip with demo-
  overlay disclosure.** Pre-answers "whose hook is about to fire?"
  before I click — AND admits the demo plants exist. BT P1.3 closed.
- **Live tail + Cancel for Refresh Data.** Resolves the BT "I clicked
  it and have no idea what's happening" anxiety. Cancel semantics
  need clarity (round-2 P1) but the live-event-tail panel itself is
  the right shape.
- **Per-stage Δms timings + [INFO]/[WARN] badges in the log.** Right
  column order, scan-friendly. BT P3.3 closed.
- **Coverage `Show failures only` toggle.** Lets me focus on the 5
  failures across all four categories instead of scrolling 47 greens.
- **Metadata coverage math agrees with subtotals.** BT P2.6 closed.
- **Post-run success banner with gen counter + hook echo line.**
  Three lines, three useful facts.
- **Accordion-grouped Triage.** Replaces 60 cards with 4 rows. Massive
  cognitive load reduction. BT P2.7 closed in one redesign.
- **Distinct icons + color stripes per Triage kind.** Two scan signals
  per row. BT P3.5 closed.
- **Triage cards now have a volume badge** ("X rows" top-right). BT
  P2.8 closed.
- **Probe radio definitions with banking examples.** Possibly the
  best new copy in the whole release. "card-purchase = auth + post"
  is the kind of grounding metaphor that makes vocabulary stick.
- **Probe date defaults to All time + quick-pick chips.** BT P1.1 +
  P2.4 both closed.
- **Probe Observed panel `Predicate Fit ✓/✗` column.** Killer feature
  for scanning a thousand-row dataset for the bad rows. Right shape,
  needs the failures-only toggle (round-2 P2) to be world-class.
- **Probe Chain side-panel diagram trigger** with parent → child
  stack and full-diagram link. BT P3.7 closed.
- **Top nav STUDIO / DASHBOARDS / REFERENCE grouping.** BUILD/VIEW
  separation. Major orientation win.
- **Glossary drawer exists** with real content (Chain, ETL Hook, L2,
  Limit Schedule, Matview, Predicate). Discoverability + search +
  cross-links are the round-2 P2s on top of a good foundation.
- **L2 editor's create-new form distinguishes Two-leg vs Single-leg
  rail** with banking examples. Good disambiguation.

---

## 12. Round-2 net new findings (triage ladder)

Highest-value issues surfaced by the 15 new screenshots, ordered by
P-tier within tier then by reaction-value. Triage this list first; the
rest of the doc has the supporting evidence.

### P1 (4)

1. **L2 editor deep-link from Triage drops the rail name + the back-
   to-Triage breadcrumb.** `11_l2editor_with_back_breadcrumb.png`
   shows the form lands with `← back to Studio` (generic) and no
   `name=_spine_client` pre-fill, despite the URL carrying
   `?from=/etl/triage`. Round-1 marked P1.4 + P1.5 as "claimed
   fixed"; round-2 reveals they're half-shipped. The most context-
   dense moment in the loop loses all the context.
2. **Probe Name typeahead is invisible on WebKit.**
   `14_probe_typeahead_open.png` shows the input filled with `ACH`
   and no visible suggestions. `<datalist>` is unreliable on WebKit/
   Safari — replace with custom typeahead or render live-text "Matching
   N of K rails: …" below the input. Without visible feedback the
   persona types a typo and concludes the data is broken.
3. **Triage demo plants are visually indistinguishable from real gaps,
   in both the accordion header and the expanded cards.** The 16,823
   LimitSchedule count + the `__demo_gap_phantom_tmpl_000` /
   `__demo_null_foi_tx*` cards all read as "real bugs in my hook" to
   the persona. The Run page's demo-overlay disclosure isn't echoed
   on Triage. **Echo the disclosure in the Triage header + badge
   demo-plant cards inside expansions** with a `DEMO PLANT` pill.
4. **Mid-run Cancel button has unclear semantics + no confirmation.**
   `05_run_midrun_live_tail.png` shows a red Cancel button mid-
   destructive-pipeline with no documented behavior. If Cancel hard-
   kills the subprocess between wipe + reload, tables are left half-
   truncated. **Add a confirmation modal on Cancel + document stop
   semantics** (this is the one place a modal is justified).

### P2 (6)

5. **Triage expansion for "Missing LimitSchedule" is a 2000px+ tall
   wall of cards** (`08_triage_expanded_missing_limitschedule.png` is
   1.27 MB and nearly unreadable at natural zoom). The accordion
   solved the per-kind density problem; it did not solve the
   per-distinct-value density problem. **Paginate / sub-group /
   search-filter inside expansions when card count exceeds ~20.**
6. **Mid-run state has no total-progress indicator.** Status reads
   "Pipeline running" without "Stage 2 of 5". For 10-minute customer
   ETLs this is the difference between "walk away" and "wait it out."
7. **Landing-page banner is default-expanded with long prose** when
   the dismissed-state tagline (`19_etl_landing_banner_dismissed.png`)
   is better copy. **Make banner default-collapsed**; lead with the
   dismissed tagline.
8. **Glossary drawer (`12_glossary_drawer_open.png`) has no search,
   no cross-links, is right-side overlay (intrusive), and is not
   page-aware.** Each of these is a separate ask but they compound on
   what's otherwise solid content.
9. **Probe Observed panel needs a `Show predicate failures only`
   toggle** and a top-line summary ("1568 rows · 312 predicate
   failures") — scanning ✗ icons across 25 rows × multiple pages is
   slow when the failure count is the actual signal.
10. **L2 editor create-form Reference panel is collapsed by default.**
    First-time persona on a create-new form should see the reference
    open.

### P3 (5)

11. **Run-log `step1:skip [WARN]` for expected demo state** trains me
    to ignore WARN badges. Demote to [INFO] when hook absence is
    expected/configured. (`16_run_log_zoom_timings.png`)
12. **Run-log step numbering** (`step2:wipe` → `step1:skip` →
    `step3:generator`) is in declaration order, not execution order.
    Renumber or drop the numbers.
13. **Probe Observed `Predicate fit: ✗ no value to evaluate`** wording
    unclear — three different failure modes collapsed into one label.
14. **Chain Probe side-panel vertical orientation** reads as a list,
    not a flow. Parent → child is horizontal in mental model.
15. **Triage card diagnosis sentence is partially redundant with the
    card title** (round-2 expansions confirm) — both the title and the
    sentence carry the rail/template name. Merge or drop the sentence.

### Operator-facing meta-finding

The 1.27 MB screenshot of `08_triage_expanded_missing_limitschedule.png`
is itself the finding. The operator who tried to capture it hit the
same problem the persona would hit on a laptop: at natural zoom the
content is illegible; zoomed out enough to fit, the individual cards
blur. **If the screenshot can't be reasonably read in one zoom level,
neither can the live page.** Pagination/sub-grouping isn't just nice-
to-have — it's the only way the page is usable for the LimitSchedule
kind.

---

## TL;DR for the operator who didn't read the rest

BTa shipped most of the BT board. Round-2 screenshots confirm the
context strip, per-stage timings + log levels, failures-only toggle,
metadata roll-up math, expanded Triage cards with color stripes +
volume badges, Probe Predicate Fit column, Chain side-panel diagram,
and the dismissed-state landing tagline. All wins.

**Round-2 reveals four new P1s the baseline shots couldn't show:**
(1) the L2 editor deep-link drops both the rail-name pre-fill and the
back-to-Triage breadcrumb despite the URL parameter being correct;
(2) Probe Name typeahead is invisible on WebKit (`<datalist>` issue);
(3) Triage demo plants are visually indistinguishable from real gaps,
even though the Run page now discloses the overlay; (4) mid-run
Cancel has no confirmation or documented semantics on a destructive
pipeline.

The single biggest P2 is **Triage expansion density** — the
"Missing LimitSchedule" expansion is a 21-viewport wall of cards
that the operator's own screenshot capture struggled with.

The Triage panic moment + the L2 editor deep-link are the two places
where the first-time loop is still broken end-to-end. Both are
fixable; both are blocking trust on the first cold-read.

---

## 13. BTb verification — cold-read v4 sign-off

Targeted re-look at the four surfaces BTb touched. Same persona,
same fresh-eyes posture, but constrained to "did the v3 P1 reactions
actually close?" Not a full re-walk.

### P1.1a — back-breadcrumb on the rail subtype picker (BTb.1)

**Screenshots:** `11_l2editor_with_back_breadcrumb.png` (the v3 baseline that
showed no Triage breadcrumb) and `btb_01a_rail_picker_with_breadcrumb.png`
(after BTb.1). Note: the two files are byte-identical in this capture
run — same `54518`-byte image at `18:21` — meaning the BTb regeneration
overwrote the v3 baseline. That's fine for sign-off: both URLs now
render the picker with the breadcrumb.

What I see: the page-title row at top is unchanged (`Create new rail
← back to Studio → list all rails`), and **below it now sits a gray
sticky strip with `← Back to Triage`**. That's exactly what was
missing in v3 — the picker page (step 1 of the 2-step Rail flow) was
the only path that didn't call `_back_breadcrumb_html`, and BTb.1
extended `_render_rail_subtype_picker` to render it AND propagate
`&from=` onto the Two-leg / Single-leg links so step 2 inherits.
Visible AND propagating.

**Verdict: ✅ shipped.** Round-2 P1 closes. The "context wilderness"
disorientation is gone — I can see Triage as my origin without
checking the URL bar.

**Net-new P3 polish:** the picker now stacks two back-affordances —
the generic `← back to Studio` in the page chrome and the new
`← Back to Triage` strip below it. For a first-time persona arriving
from Triage, the generic Studio link is now noise. Consider hiding
(or de-emphasizing) the `← back to Studio` chrome link when
`?from=/etl/...` is present — the Triage breadcrumb is the only one
that matters in that flow. Minor; not blocking.

### P1.1b — name prefill on rail step-2 + transfer-template direct form (BTb.2)

**Screenshots:** `btb_01b_rail_step2_with_prefill.png` (picked Two-leg
from `btb_01a`, landed on the actual form) + `btb_02_tt_with_prefill.png`
(non-rail kind, single-step).

Both show:
- `← Back to Triage` strip at top — survived the picker → step-2
  transition for rails AND lands directly on the non-rail single-step
  form.
- **Name input pre-filled** — `legacy_card_swipe` on the rail form,
  `orphan_settlement_batch` on the transfer_template form. The most
  context-dense data point — the exact name from the Triage gap card —
  is now waiting for me when I arrive. I'm no longer copy-pasting
  from a tab I already closed.

The rail form is dense (Two-leg rails carry source_role / destination_role
checkbox grids + bookkeeping_entity + a long list of metadata-key
inputs), so having even ONE field pre-decided is real friction relief.
The transfer-template form is shorter but the same principle applies.

**Verdict: ✅ shipped on both kinds.** The deep-link plumbing v3
flagged as "half-shipped" is now end-to-end. Round-trip flow lands.

**Observed but not blocking:** prefill is name-only. The other fields
the operator might have known from the Triage row (`source_role`,
`destination_role`, an example `transfer_key` value) are still empty.
That's the correct v1 scope per BT.0 lock 5 ("link-only in v1") —
and frankly the right call: the operator's `legacy_card_swipe`
probably maps to a NEW pair of roles they haven't created yet. Don't
gold-plate the prefill.

### P1.3 — Triage demo-plant disclosure banner (BTb.3)

**Screenshot:** `btb_03_triage_with_demo_banner.png`.

New banner at the very top of `/etl/triage` — full-width tan/cream
aside with the ⓘ icon: **"Bundled-demo data. Some gaps below are
intentional demo plants (rows tagged `__demo_gap_*`) so this page
has content to demo. With a real ETL hook configured (set
`cfg.etl_hook`), only your real gaps surface."**

This is the right copy. It does three things at once:
1. Frames the data source ("bundled demo, not your hook").
2. Names the convention (`__demo_gap_*`) so I can pattern-match in
   the expansions.
3. Tells me how to make it go away when I'm done (configure
   `cfg.etl_hook`) — i.e. confirms the banner is contextual to my
   current cfg state, not a permanent fixture.

The headline below still reads **"51 gaps across 4 kinds"** with the
16,823 LimitSchedule row count loud and prominent — so the panic
*number* hasn't shrunk. But the banner converts the panic from "my
ETL is fundamentally broken" to "some of this is plants; let me see
which." That's the right defusal.

**Verdict: ✅ shipped.** P1.3 closes. The panic is contained.

**Net-new P3 polish:** the banner names the `__demo_gap_*` prefix
but the per-kind row counts in the accordion summaries (`13 rows
total · 4 distinct`, `16,823 rows total · 42 distinct`, etc.) don't
indicate which fraction is plants. After expanding a section I can
pattern-match prefixes — but the operator looking at the summary
view still has the 16,823 mystery. **Future:** a `(of which N are
demo plants)` annotation in each accordion header, or a per-card
`DEMO PLANT` pill inside expansions. The v3 ladder's P1.3 third
sentence ("badge demo-plant cards inside expansions with a DEMO
PLANT pill") is still un-shipped — banner closes the *header* panic,
not the *per-row* one. Banking that as P2 follow-up, not BTb
regression.

### P1.4 — Cancel copy + tooltip (BTb.4)

**Screenshot:** `btb_05_midrun_with_cancel_copy.png`.

Mid-run state: red `✕ Cancel run` button on the left, "● Pipeline
running — live events below." status next to it, and **directly below
the button a three-sentence gray help block: "Stops the pipeline
immediately. Partial DB state stays until the next Refresh Data
wipes it — we don't auto-clean to help with troubleshooting.
Subprocess hooks may keep running until they exit."**

This nails what v3 asked for. The three sentences each answer a
different fear:
- "Stops the pipeline immediately" — yes, Cancel does what it says.
- "Partial DB state stays until the next Refresh Data wipes it — we
  don't auto-clean to help with troubleshooting" — this is the
  *reframe* I didn't know I wanted. v3 said the half-truncated-tables
  scenario was the worry; this turns "we leave a mess" into "we leave
  evidence." Operator-locked phrasing, and it's the right one. I'll
  hit Cancel without the modal-grade hesitation.
- "Subprocess hooks may keep running until they exit" — calls out the
  one footgun (subprocess leaks) so I don't think Cancel is magic.

No modal, per the BTb decision. Given the troubleshooting reframe,
the modal is no longer needed — the operator concern was "this is
destructive and irreversible" and the copy answers both halves
(destructive: yes; irreversible: no, next click wipes).

**Verdict: ✅ shipped.** P1.4 closes.

**Net-new P3 polish:** the help copy renders in light gray that
visually merges with the page chrome — on a quick scan I might miss
it and just hit Cancel without reading. Consider tightening the
contrast OR moving the first sentence inline next to the button as
a `cursor: help` underline + tooltip (the title= is there per the
BTb.4 PLAN note but a static visible affordance reads first). Minor.

### TL;DR

**BTb sign-off: ready to release.**

All four v3 P1s flagged for BTb scope are closed. The breadcrumb
renders on the picker page (BTb.1), name prefill survives the rail
2-step AND lands on the direct transfer-template form (BTb.2), the
Triage demo-plant banner defuses the headline panic (BTb.3), and
the Cancel copy reframes partial state as a troubleshooting
affordance rather than a footgun (BTb.4). P1.2 (WebKit datalist)
was correctly closed as a Playwright capture artifact per BTb.5 —
verified in real Safari.

Three small P3 polish items surfaced (none blocking):
- Hide `← back to Studio` chrome when `?from=/etl/...` is present
  (avoid double back-affordance stacking).
- Per-section "(N of which are demo plants)" annotations + per-card
  `DEMO PLANT` pills inside Triage expansions (banner closes header
  panic, not per-row).
- Tighten contrast on Cancel help-copy or promote the partial-state
  sentence to an inline tooltip the operator notices before clicking.

Bank these into the BTb backlog cluster (or BX, since two of them
touch editor chrome). None of them are reasons to hold the release.
