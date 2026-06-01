# BV cold-read v3 — Trainer surface after BV.4.10 polish + bug1-4 fixes

> **Scope.** Live cold-read of `http://localhost:8765/training/` against a
> running sqlite + sasquatch_pr instance. Screenshots in `/tmp/bv_shots/`
> (01–46). No source files read — drove the surface through Playwright
> only, mirroring an operator's first-week experience.
>
> **Drove:** landing → toggle one card → toggle Select-all + None → Session
> Start (streaming) → Apply (streaming + failure surface) → planted-card
> Violation dashboard → Clean dashboard → mixed add/remove preview →
> Force rebuild from base → Cleanup → ? help panel.

---

## Verdict

> **Status update — 2026-05-31.** P1.1 was a stale-process artifact (studio
> held cached `seed.py` from before bug1 landed); resolved by restart. This
> is a process-discipline gap (auto-restart Studio after commits, per
> `[feedback_design_for_claude_loops]`). Post-restart verification: Session
> Start completes (~10s sqlite), enabling `drift` + Apply produces the
> "currently planted" green badge with no ImportError banner. Screenshots
> in `/tmp/bv_p11_verify/` (10–12).

**NOT shippable.** The polish work (planted badge, live diff preview,
streaming Session Start, button disable-while-busy) is genuinely good
— this surface *feels* solid in the chrome layer. But underneath, the
"bug1 — real LedgerDriftPlant" fix is broken: planting any kind that
touches the `LedgerDriftPlant` import path **errors with `ImportError:
cannot import name 'LedgerDriftPlant' from 'recon_gen.common.l2.seed'`**.
The same exception fires for `drift`, `overdraft`,
`limit_breach_outbound`, `stuck_pending` — meaning the operator's first
3-5 attempts to plant a violation will fail with a developer-stack
error in a tooltip. Out of a 5-card test plant I sent through, 3
failed. The L1 families that are the natural starting point for a
"learn what double-entry conservation means" walkthrough are exactly
the ones that don't work. P3 polish is mostly fine; the streaming + diff
preview shapes are great when the underlying op succeeds. Fix the
import, then this is shippable.

Dominant friction: **the operator's first 3 clicks fail with a Python
ImportError tooltip**. The carefully-staged streaming + planted-badge
UX never gets a chance to teach anything because the plant doesn't
land.

---

## P1 — Trust-killers

### P1.1 — `LedgerDriftPlant` ImportError fires for at least 4 of 25 plant kinds

**Observed.** Enabled five plants across families (`overdraft`,
`limit_breach_outbound`, `stuck_pending`, `phantom_rail`,
`uncovered_rail`) and clicked Apply. Banner returned
`✗ 3 plant(s) failed on the last Apply: limit_breach_outbound,
overdraft, stuck_pending.` Hovering each card's red "error planting"
badge surfaces the identical tooltip:

```
ImportError: cannot import name 'LedgerDriftPlant' from
'recon_gen.common.l2.seed'
(/Users/chotchki/workspace/quicksight/src/recon_gen/common/l2/seed.py)
```

Also reproduces for the L1 Conservation `drift` kind (the very first
card on the page), confirmed independently when planting only that one
kind.

Net effect: of the 25 advertised plants, at minimum `drift`,
`overdraft`, `limit_breach_outbound`, `stuck_pending` fail; the only
two I successfully planted were `phantom_rail` and `uncovered_rail`.
I didn't exhaustively bisect, but the import error suggests *every*
kind whose plant class lives in the same module path is broken — the
top-of-the-list L1 Conservation family is the worst possible failure
locus.

**What should change.** Land bug1 properly — either re-export
`LedgerDriftPlant` from `recon_gen.common.l2.seed` or fix the import
path in the plant registry. The cold-read can't even reach the
"streaming spinner / planted badge / violation tour" surface for these
kinds.

**Why.** This is the trainer's whole reason for existing. The
operator's three-second mental model of the page is "check box, click
Apply, see violation light up." When that fails on the *very first
attempt* with a Python file path in a tooltip, every other piece of
polish is overhead they're trying to forgive.

### P1.2 — Session Start silently discards in-flight checkbox state

**Observed.** Checked the `drift` card on a fresh landing page, then
clicked "Session Start (re-fetch)". The streaming page appears, runs
~5s, banners "Session Start done — v overlay ready." On reload the
checkbox is unchecked; "0/25 plants enabled" persists; nothing got
planted. The pending diff (which previously read "+1 new — Apply to
commit") was thrown away by the Session Start trip even though Session
Start runs `etl_begin → wipe → step3:generator:start` which *looks*
identical to Apply at the event-log level.

**What should change.** Either (a) the Session Start streaming page
should preserve the unsaved client form state and re-hydrate the
checkboxes on return, or (b) the page should warn before discarding —
something like "You have +1 unapplied change. Session Start will wipe
the overlay AND your pending selection. Continue?" Option (a) is the
operator-friendly default; option (b) is the cheap one.

**Why.** The dominant operator confusion at landing is "do I click
Session Start, or do I click Apply, or both?" The copy
("Pick the violation plants ... click Apply, then use each card's
Clean dashboard / Violation dashboard links to see the before/after.
Session Start populates a qsgen_sqlite_v overlay...") *implies* Session
Start comes first as a prerequisite. So the natural flow is "check
some plants, click Session Start because it's the first button on the
left." Doing that resets the selection silently. This will burn the
first operator who reads top-down once per onboarding.

### P1.3 — Stale "in progress" banner survives the auto-reload

**Status: false positive — screenshot artifact 2026-05-31.** Operator
re-ran post-restart and the banner transitions cleanly to the green
"Session started — v overlay ready" state on the HX-Trigger reload.
Likely the cold-read agent screenshotted during the brief race window
between the finished event and the reload completing.

**Original observation kept below for the historical record.**

**Observed.** During the post-Session-Start auto-reload window (and
again during the post-Apply auto-reload window), the green
"Session Start in progress — re-fetches the base prefix + rebuilds the
v overlay (~10s sqlite / ~30s Postgres / ~10 min Oracle for the
/etl/run leg)" banner stays on the page even though the op has
completed. Same for the orange "Apply in progress — applying 1 plant(s)
against the v overlay. DL.9 fast path (no removals) skips the clone —
matview refresh is the dominant cost." A manual page reload clears it,
but the auto-redirect lands with stale state.

The operator sees a spinner-style banner saying "in progress" while
all the cards are interactive again and the counts say "0/25 plants
enabled" — they can't tell if it's still running or finished. (It's
finished; the banner is just leftover.)

**What should change.** Either the redirect target should clear the
SSE / progress flag (server-side cookie or query param dropped before
the GET re-renders), or the streaming page should explicitly write the
success banner and unmount the progress banner before triggering
location.reload(). Currently the success state ("✓ Apply done." /
"✓ Session Start done") only appears after a manual reload.

**Why.** The "now I can wait safely" feeling that the streaming
progress page was built to deliver gets *undone* by the stale banner.
The operator can't tell if the op finished or hung.

---

## P2 — Noticeable friction

### P2.1 — "Violation dashboard →" dumps the operator on ETL Triage with no breadcrumb back

**Observed.** Clicking the "Violation dashboard →" link on the
`phantom_rail` card lands at `/etl/triage?prefix=qsgen_sqlite_v` —
which is the ETL Support → Triage page (not a Training subpage). The
operator has lost their training context. There's no:

- "← Back to Training" link
- "You're viewing the Violation overlay for `phantom_rail`" callout
- "Compare to clean →" sibling link to flip prefix

The Triage page's own header just says "11 gaps across 2 kinds" with
no indication that 3 of them are the operator's planted rows. The
operator has to remember that the previous page said "Number of rows:
3" and visually correlate.

**What should change.** Either: (a) keep the operator on a Training
subpath with the dashboard embedded + a "back to plants" + a
"clean / violation" toggle, or (b) inject a teaching banner into the
ETL/Triage page when arrived-from-Training, naming the planted kind +
expected row count + a return link.

**Why.** This is the climactic moment of the BV trainer — "see your
plant surface in the dashboard." The lack of in-context framing means
the moment fizzles. Operator has to do the cross-reference work
themselves.

### P2.2 — Clean vs Violation comparison teaches by subtraction the operator can't do

**Observed.** Same `phantom_rail` plant:

| Link | Headline | Kinds |
|------|----------|-------|
| Clean dashboard → | 11 gaps across 1 kind | Missing LimitSchedule (baseline) |
| Violation dashboard → | 11 gaps across 2 kinds | Missing LimitSchedule + Unmatched rail_name |

Both pages headline "11 gaps" — only the *kind count* differs.
"Unmatched rail_name" appears only on the Violation side, but the
operator has to open both tabs and spot the new row manually. There's
no "delta" view.

**What should change.** Either a server-side `?compare=qsgen_sqlite`
mode that surfaces a diff (+1 kind, +3 rows: Unmatched rail_name), or
a Training-side "What you should now see" annotation alongside the link
("After planting phantom_rail you should see Unmatched rail_name appear
on the Violation dashboard with 3 rows.").

**Why.** Self-validation is half the trainer value. Without "this
specific thing should appear, here's what to look for," the operator
trusts the system on faith — which is the opposite of building
confidence in the invariants.

### P2.3 — Session Start banner promises Oracle ETL time that won't happen

**Observed.** Session Start banner reads "(~10s sqlite / ~30s Postgres
/ ~10 min Oracle for the /etl/run leg)" — but the event log shows
`deploy:step1:skip reason=etl_hook not configured`. No ETL leg ran. The
"~10 min Oracle" warning is irrelevant for any install that hasn't
wired `cfg.etl_hook`.

**What should change.** Either probe `cfg.etl_hook` and conditionally
include the ETL timing, or split the banner into a base estimate +
"if `etl_hook` is configured, add ~10 min on Oracle for the re-fetch."

**Why.** Right now the worst-case 10-min estimate scares the operator
unnecessarily for a 5-second op. They'll either learn to ignore the
banner (then miss it when it matters) or sit around waiting.

### P2.4 — "DL.9 fast path (no removals) skips the clone" leaks engineering jargon

**Observed.** Apply banner literally says
"DL.9 fast path (no removals) skips the clone — matview refresh is the
dominant cost." DL.9 is an internal phase reference that means nothing
to an external operator. Same general shape across the event log
(`session_start:etl_begin`, `deploy:step2:wipe`, etc.) — fine for an
engineer triaging, less fine as the "trust me this is making progress"
signal the streaming page is supposed to be.

**What should change.** Strip the DL.9 reference from the user-facing
banner. The "fast path skips the clone — matview refresh is the
dominant cost" half is fine if you want to surface that the slow part
is unavoidable; the phase code is pure noise. Event log is fine to
keep dense if it's gated behind "Show event log" (which it is).

**Why.** Operator-facing copy + internal phase IDs is a smell the
codebase has flagged elsewhere ("CPA-readable standard banking
terminology"). DL.9 doesn't pass that bar.

### P2.5 — Cleanup is destructive with no confirmation

**Observed.** Clicking the orange Cleanup button immediately wipes the
v overlay with no "are you sure?" prompt. It's fast enough that a
fat-finger on landing destroys all plants. The success banner
("✓ Cleanup done — v overlay dropped.") is good, but there's no undo
and no "type CLEANUP to confirm."

**What should change.** Either a single confirm dialog ("This will
drop all planted violations. Continue?") or — since this is a *demo*
trainer surface, not production — leave it as-is and lean on the green
banner. The op is genuinely cheap to reverse (Session Start re-creates
+ Apply re-plants). Argued either way; flagging as friction not
blocker.

**Why.** Destructive button on a training page is a common
"oops I lost my work" trigger. But because the op is cheap and
reversible, low priority.

---

## P3 — Polish

### P3.1 — Force rebuild from base has no streaming, breaking symmetry

**Observed.** Session Start + Apply both go through the streaming
progress page (spinner, [EVT] event log, banner). Force rebuild from
base instead returns synchronously to a green
"✓ v overlay rebuilt from base — Apply state wiped." banner with no
spinner. For a sqlite trainer this completes in <1s so it's not
visible — but on Postgres / Oracle the op presumably takes seconds to
tens of seconds, and the operator will see a frozen browser instead
of a progress page.

**What should change.** Route Force rebuild through the same streaming
shape as Session Start. Cheap consistency win.

### P3.2 — Help (`?`) panel is global glossary, not Trainer guidance

**Observed.** Clicking `[?]` from the Training page opens a side panel
populated with global terminology (Chain, ETL Hook, L2, Limit Schedule,
Matview, Predicate, Rail, Singleton, ...). Nothing on it explains the
training workflow, what Session Start vs Apply vs Cleanup mean, what
the "v overlay" is in operator terms, what "Currently planted" means,
or what to look for on the Violation dashboard.

**What should change.** Either a page-specific Help that prefixes
"Training workflow: 1) Session Start... 2) check plants... 3) Apply...
4) compare Clean vs Violation dashboards... 5) Cleanup when done." then
falls through to the glossary, or a "?" tip near the buttons
themselves.

**Why.** Operators clicking ? are asking "what am I supposed to do
here?" — not "what does Predicate mean in the abstract?".

### P3.3 — "0/25 plants enabled" wording ambiguous about state vs intent

**Observed.** The counter "0/25 plants enabled" updates when checkboxes
are toggled (before Apply). So "enabled" means "selected" / "ticked",
not "planted in the overlay." Meanwhile the green badge on a card says
"currently planted" — different vocabulary for the actual committed
state.

It's not wrong, but the dual vocabulary ("enabled" = client intent,
"planted" = server commit) is something a careful reader will notice
and a fast reader will conflate. The Apply preview ("+5 new — Apply to
commit") helps bridge it, but only when there's a diff.

**What should change.** Either "0/25 plants selected" + "currently
planted" (split intent vs committed), or "0/25 enabled (0 planted)"
when they diverge. Minor.

### P3.4 — Family accordions collapsed by default hide the bulk-action affordances

**Observed.** Landing page opens with L1 Conservation expanded and all
other families collapsed. The collapsed family rows show
"L1 Cap (0/3 enabled) [all] [none]" — the `[all]` / `[none]` chips are
the bulk-family-toggle but they're visually de-emphasized (bracketed,
gray) on a collapsed family header. The operator might assume they
have to expand the family to interact with it.

Also, clicking `[Select all]` at the top scrolls past the collapsed
families silently — the operator sees the count change but never sees
the now-checked cards inside.

**What should change.** Either auto-expand families on `[Select all]`,
or leave them collapsed but render a "(3 enabled — click to view)"
hint. Cosmetic.

### P3.5 — "(Violation dashboard available after Session Start + Apply)" replaces the link post-Cleanup

**Observed.** After Cleanup, the per-card link row changes from
"Clean dashboard → · Violation dashboard →" to
"Clean dashboard → · (Violation dashboard available after Session
Start + Apply)" — and the Apply footer changes to "Click Session Start
first to populate the v overlay." Both copies are *correct*, but the
parenthesized italics version of a previously-active link reads as
"this link is broken" at a glance. Worth styling more obviously as
"awaiting prerequisite" — e.g. dimmer text + a tooltip on hover.

**What should change.** Style the disabled-state copy as an obvious
"awaiting prerequisite" pill, not as a parenthesized inline phrase
that looks like an error description.

---

## What's strong (don't regress)

- **Apply preview diff is excellent.** "+1 new — Apply to commit" and
  "+1 new, −1 removed — Apply to commit" update instantly on toggle,
  and the "no changes pending" gray state when intent == committed is
  the right empty state.
- **"Currently planted" green badge** is unambiguous on a card.
  Distinct from the checkbox state, distinct from the error badge.
- **Button-disable-while-busy** is correctly implemented across
  Session Start / Force rebuild / Cleanup / Apply selection.
- **Error tooltips carry the real exception**. The
  `ImportError: ...` text is in the badge `title` attribute and visible
  on hover. Painful copy for an operator, but the right pattern for
  debuggability.
- **Failure banner enumerates failed kinds by name**
  ("limit_breach_outbound, overdraft, stuck_pending"). Operator can
  scroll to each card and see the per-card badge — coordinated cross-
  reference.
- **Session Start event log** is well-formatted dense
  `[EVT] session_start:begin refresh_base=True` lines that an engineer
  triaging a stuck Session Start can scan quickly. Right thing to
  have, right thing to hide behind a Show toggle.
- **"v overlay" framing in the lede paragraph** ("Session Start
  populates a qsgen_sqlite_v overlay ... your production qsgen_sqlite
  prefix is untouched") is the right reassurance for a new operator
  worried about wrecking production data.

---

## Action priority

1. **Fix P1.1 (LedgerDriftPlant ImportError)** — blocking. Trainer is
   not shippable until L1 Conservation family plants land.
2. **Fix P1.3 (stale progress banner after redirect)** — high-visibility
   bug, undoes the streaming-page polish.
3. **Decide P1.2 (Session Start vs in-flight selection)** — either
   preserve client state or warn on discard.
4. **P2.1 + P2.2 (Tour context)** — the trainer's whole purpose. Even
   a one-line "you should now see Unmatched rail_name appear with 3
   rows" callout would lift the experience materially.
5. **P2.3 + P2.4** (banner copy hygiene) — five-minute fixes.
6. **P3.x** — sweep when convenient; none are blockers.
