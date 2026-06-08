# BV.4.0 vertical-slice cold-read

> **Headline verdict: ARCHITECTURE-PASS, with two P1 render bugs to clear before BV.4.1 continues.**
>
> The dual-prefix architecture works as the 15 design locks intended:
> Session Start leaves the surface in the right shape; the v overlay
> stays isolated from base; Clean (shot 04) vs Violation (shot 05)
> read distinctly different data (1-row vs 4-row Unmatched rail_name).
> The per-card layout shape will hold for 25 kinds with some density
> tuning. But two render bugs in the vertical slice need fixing before
> BV.4.1 lands the next pieces: **(a) the post-action status banner
> doesn't render** despite the source code wiring it up; **(b) the
> enabled-kinds checkbox state doesn't survive the Apply round-trip**.
> Both are state-threading bugs at the same form-post → re-render seam,
> likely a single fix.

---

## §1 What works

**Architectural locks land correctly:**

- **DL.3 (two prefixes, base + `_v`):** shot 04 reads from `qsgen_sqlite`
  → 13 gaps / 1-row Unmatched rail_name. Shot 05 reads from `qsgen_sqlite_v`
  → 14 gaps / **4-row, 2-distinct** Unmatched rail_name. v overlay isolation
  holds: the +3 plant rows (count=4 minus the 1 baseline row already there)
  surface ONLY on the v prefix, base is untouched. This is the load-bearing
  invariant — confirmed working.
- **DL.6 (two-link Tour, not toggle):** card on shot 02 / 03 renders
  `Clean dashboard →` AND `Violation dashboard →` as distinct accent-colored
  links. Shot 01 correctly degrades the Violation slot to "(Violation
  dashboard available after Session Start + Apply)" — gated on
  `v_overlay_exists`. Read works as intended.
- **DL.7 (`?prefix=` URL param first-class):** Tour links wire
  `href="/etl/triage?prefix=qsgen_sqlite"` and `?prefix=qsgen_sqlite_v`
  respectively (verified in `_studio_training_v3.py:180-187`). The
  route honors the param — shots 04 vs 05 prove the data narrowing
  takes effect at the dashboard layer.
- **DL.10 (Session Start / Re-clone / Cleanup button choreography):**
  shot 01 shows only `▶ Session Start` (correctly hides Cleanup pre-overlay
  and disables Apply). Shots 02/03 show the post-Session-Start state:
  `↻ Re-clone from base` + `🗑 Cleanup` + Apply enabled. State machine
  is wired right.
- **DL.8 (per-kind card carrying title + description + form fields inline +
  what-to-do + two Tour links):** all six anchor elements present in one
  card (checkbox, title, `phantom_rail` tag, short description, two
  inline form fields, what-to-do paragraph, two Tour links). Card shape
  per the lock.
- **Apply orchestration end-to-end:** the task description confirms 4
  `legacy_card_swipe` rows landed in the v overlay; shot 05's "4 rows
  total · 2 distinct" matches (4 new plants + the 1 pre-existing baseline
  row → 5 rows total but 2 distinct names — though the count shows 4,
  which means the baseline row may have a different rail name; either way
  the plant landed and the violation surfaces).
- **`v_overlay.py` orchestration source is clean:** Session Start drops →
  creates → clones → refreshes matviews in one transaction; Cleanup is a
  single drop; both wrap `connect_demo_db` in `asyncio.to_thread`. The
  shape will extend to the BV.4.4 diff-only Apply without restructuring.
- **`v_overlay_prefix()` helper is the right abstraction** — single
  function so the convention isn't string-concatenated ad hoc across the
  codebase. Future rename atomic per the lock intent.

**Pristine landing UX (shot 01) reads correctly:** header copy explains
the model (`qsgen_sqlite_v` overlay vs `qsgen_sqlite` base), Session Start
is the only enabled action, Apply correctly disabled with the helper text
"Click Session Start first to populate the v overlay."

---

## §2 P1 — fix before BV.4.1 continues

### P1.1 — Status banner doesn't render on shots 02 + 03

**Expected:** shot 02 should show a green success banner "Session started"
(or equivalent); shot 03 should show "Applied 1 plant(s).". Neither is visible.

**Source:** `_studio_training_v3.py:70-77` has the banner block:
```python
banner_html = ""
if session_status:
    banner_html = (
        '<div class="bg-success/10 border border-success rounded-md '
        'px-3 py-2 mb-3 text-sm" data-test-training-banner>'
        f'<strong class="text-success">✓</strong> {escape(session_status)}'
        "</div>"
    )
```

**Hypothesis:** the route handler isn't passing `session_status` into
`render_training_v3_landing(...)` after Session Start / Apply POST →
redirect. Standard POST-redirect-GET pattern needs a one-shot flash
mechanism (cookie, query param, session state) for the banner text to
survive the redirect. The renderer itself looks correct — this is a
route-handler bug, not a render bug.

**Why P1:** without the banner the operator gets ZERO feedback that
Session Start / Apply succeeded. They only know it worked because the
buttons changed shape (Session Start → Re-clone) on shot 02 — and on
shot 03 there's NO state change visible to confirm Apply did anything.
At 25 cards with many Apply cycles, silent success is a UX cliff. Fix
this before adding more kinds.

**Fix shape:** flash via `?status=...` query param on the redirect (smallest
change) OR Starlette session middleware. Query-param flash is simpler +
keeps URL-as-truth (DL.13).

### P1.2 — Enabled-kinds checkbox state lost across Apply round-trip

**Expected:** shot 03 (post-Apply) should show the phantom_rail checkbox
**ticked** since the operator enabled it before clicking Apply. It renders
unchecked.

**Source:** `_studio_training_v3.py:192` reads
`checked_attr = " checked" if enabled else ""`, where `enabled` is
`_VERTICAL_SLICE_KIND in enabled_kinds`. Caller (`render_training_v3_landing`)
takes `enabled_kinds: tuple[str, ...] = ()` — defaults to empty.

**Hypothesis:** same root cause as P1.1 — the post-Apply route handler
re-renders the page without threading the `enabled_kinds` state back in.
Either the v overlay's `<v>_config_kv.trainer_applied_plants` row isn't
being read on GET, or the route handler reads it but doesn't pass it to
the renderer.

**Why P1:** if the checkbox state is lost, the operator's mental model
("I enabled phantom_rail; let me also enable a second plant and re-Apply")
is broken — they'd have to re-tick phantom_rail every time. At 25 kinds
this turns Apply into a checkbox-rebuilding task. Also: the form_values
parameter has the same shape (`Mapping[str, Mapping[str, str]] | None`)
and presumably the same bug — count=3 + rail_name=legacy_card_swipe on
shot 03 might be the *primitive defaults* re-rendering, not the
operator's submitted values.

**Fix shape:** Apply route, on its redirect-or-render, must (a) read
`<v>_config_kv.trainer_applied_plants` for `enabled_kinds`, (b) read the
form-values fingerprint per DL.9's planned shape. Even for the vertical
slice these need to round-trip or the surface is non-functional once
multiple plants land.

**Both P1.1 and P1.2 are likely the same fix** — the
`/training/session-start` and `/training/apply` route handlers' POST
response. Worth touching them together.

---

## §3 P2 — scale risks for BV.4.4 attention

### P2.1 — Form field name collisions at 25 kinds

**Source:** `_render_primitive_field` emits `<input name="form_{primitive.name}">`.
Many kinds share primitive names (`count`, `days_ago`, `rail_name` recur
across plants). At 25 cards in one `<form action="/training/apply">`, the
POST body will have multiple `form_count=...` entries with no per-kind
disambiguation.

**Fix shape for BV.4.4:** prefix the input name with the kind, e.g.
`name="form_{entry.kind}_{primitive.name}"`. The form-value parser on the
server then groups by kind. Trivial fix but easy to miss if BV.4.4 just
adds 24 more `_render_card` calls without touching the field-name scheme.

### P2.2 — "What to do about it" paragraph density at 25 cards

**Observation:** shot 03's phantom_rail card has a ~6-line dense paragraph
of "What to do about it" copy. Multiplied 25× that's roughly 150 lines of
guidance text on one page even at the active-only filter view.

**Mitigations to consider for BV.4.4:**
- Default-collapse the "What to do about it" copy behind a `<details>`
  disclosure on each card. Title + short_statement + form + Tour links
  stay visible; the long-form guidance is one click away.
- The DL.8 lock already calls for a "Show: [All / Only enabled / Only with
  errors]" top-level filter — that lessens the problem but doesn't solve
  it when 5-10 kinds are enabled.
- Per-family `<details>` accordion (also in DL.8) helps if families
  default-collapsed.

Not a re-shape, just a density tuning. Worth deciding BEFORE adding 24
more cards rather than after.

### P2.3 — Per-family grouping not yet present

The vertical slice has zero family chrome (no L1 Conservation / L1 Cap /
L2 Triage / etc. headings). DL.8 calls for per-family `[Select all] [None]`
chips + per-family `(N/M enabled)` badges + family accordions. Designing
the family-grouping shape with one card was impossible — the BV.4.4 work
needs to plant the family scaffolding as a step **before** adding the
remaining 24 cards, not at the same time. Otherwise the visual layout
churns twice.

### P2.4 — Apply button placement at the bottom won't scale well

Right now `⚡ Apply selection` is at the bottom of the single card (shot
03). With 25 cards stacked vertically, that's a long scroll to find Apply
after toggling something near the top. Common patterns: sticky-bottom bar,
or sticky-top action header. The DL.8 lock says "Apply button at the
bottom" — worth re-checking with operator at BV.4.4 design time whether
sticky-bottom is implied. Single Apply at the literal bottom of 25 cards
is rough.

### P2.5 — Tour link semantics on disabled kinds

DL.6 says the Violation link STILL points at the violation-dashboard URL
even when the kind isn't enabled, so the operator gets the
prove-it-to-yourself empty-state. The vertical slice card emits the
Violation link as long as `v_overlay_exists=True` — independent of whether
that *kind* is enabled. That's correct per DL.6, but at 25 kinds × 2 links
each = 50 Tour links, all of which navigate to the same `/etl/triage` page
with the same `?prefix=` (the prefix is shared across kinds, only the
sheet/section anchor differs by kind). Worth confirming each card's
`entry.tour_destination.primary_url` actually anchors to the right
section/sheet so 50 links don't all go to the same generic triage view.
For phantom_rail → `/etl/triage` is right (unmatched_rail is what triage
surfaces). For other kinds it may need to be `/etl/triage#unmatched_rail`
or per-app deep-links. Verify the registry's tour_destination values
during BV.4.4 wiring.

---

## §4 P3 — polish backlog

- **Banner styling:** when P1.1 ships, the banner uses `bg-success/10
  border border-success` — verify against the theme's success token.
  Existing pattern elsewhere in Studio for confirmation.
- **Session Start title tooltip mentions "vertical slice skips the /etl/run
  leg"** (`_studio_training_v3.py:136-137`): correct vertical-slice
  behavior, but operators reading the tooltip post-BV.4.1 will be confused.
  Sweep this string when /etl/run wiring lands.
- **"Plants (vertical slice — 1 of 25)" section title** — sweep at BV.4.4
  to "Plants" or per-family titles.
- **Cleanup button color (`bg-warning` brown):** semantically warning is
  right (destructive op). Consistent.
- **Card border + padding:** acceptable for one card, may want tighter
  spacing once 25 cards stack. Not a blocker.
- **Anchor for clean+violation links is the underlying base table — but
  shot 04's "1 row total · 1 distinct" Unmatched rail_name means the
  base ALREADY has an unmatched rail.** That's a property of the seed data
  (the planted demo gaps from `add_broken_rail_plants(15)` in `data apply`),
  not a bug — but worth a one-line note on the Trainer landing copy that
  explains why Clean isn't actually "zero violations" out of the box. The
  current header copy doesn't mention this.

---

## §5 What's *correctly* missing (scope confirmation)

These are absences expected per the BV.4.x phase plan in spike §7 — **do
not flag as bugs**:

| Missing | Lands in | Per |
|---|---|---|
| Other 24 plant cards | BV.4.4 | Spike §7 |
| Per-family / top-level `[Select all] [None]` bulk-toggle chips | BV.4.4 | DL.8 |
| Per-family + top-level `(N/M enabled)` density badges | BV.4.4 | DL.8 |
| Per-family `<details>` accordion grouping | BV.4.4 | DL.8 |
| Top-level "Show: [All / Only enabled / Only with errors]" filter | BV.4.4 | DL.8 |
| Diff-only Apply (current path is naive clone-and-replay) | BV.4.4 | DL.9 |
| `/training/setup` streaming progress page | BV.4.3 | Spike §7 |
| Per-kind failure / "error planting" card state | BV.4.5 | DL.12 |
| L2-staleness banner | BV.4.5 | DL.14 |
| Anti-drift tests (prefix-routing exhaustiveness; Info-sheet prefix row) | BV.4.6 | Spike §7 |
| BV.3.1 round-trip over PG + Oracle Docker | BV.4.7 | Spike §7 |
| `etl_hook` / `/etl/run` invocation as step 1 of Session Start | BV.4.1 final wire | DL.10 + Session Start tooltip already discloses this |
| QS dual-prefix support | NEVER (deferred indefinitely) | DL.1 |
| CLI `--trainer-mode` flag or mode-aware code paths | NEVER | DL.10 |
| L2-Editor / Probe / Coverage / Top-nav awareness of v overlay | NEVER | DL.4 |

The vertical slice has correctly resisted the temptation to land any of
the above — scope discipline is clean.

---

## Summary

Architecture passes the soundness gate. The dual-prefix model, two-link
Tour, URL-param wiring, and Session Start / Apply / Cleanup orchestration
all land per the locks. The per-card shape is right for 25× multiplication
once family grouping (BV.4.4) lands.

**Before BV.4.1 continues:** fix P1.1 (status banner not rendering after
POST) and P1.2 (enabled-kinds + form values not surviving Apply round-trip)
— same form-post → re-render seam, probably one fix touching both
`/training/session-start` and `/training/apply` route handlers.

**At BV.4.4 design time:** decide form-input naming scheme
(`form_{kind}_{primitive}` to avoid collisions), what-to-do-about-it
density mitigation (default-collapsed `<details>`?), Apply-button placement
strategy (sticky?), and verify each registry entry's `tour_destination`
deep-links to the right section.

**Files reviewed:**
- `/Users/chotchki/workspace/quicksight/src/recon_gen/common/html/_studio_training_v3.py`
- `/Users/chotchki/workspace/quicksight/src/recon_gen/common/l2/v_overlay.py`
- `/Users/chotchki/workspace/quicksight/docs/audits/bv_5_dual_prefix_spike.md`
- `/tmp/bv_vslice_coldread/01_landing_pristine.png` through `05_etl_triage_violation.png`
