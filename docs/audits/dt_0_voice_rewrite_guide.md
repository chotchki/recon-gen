# DT.0 — Voice-rewrite guide (recon-gen docs → chotchki's voice)

Dev-facing working note for Phase DT. The full voice model lives at
`/Users/chotchki/workspace/career-portfolio/voice_profile/chotchki-voice-profile.md`
(PRIVATE). This file is the **repo-specific operating subset**: the lint every
rewrite runs, the constraints THIS codebase adds on top, and the provenance map
of what to touch vs leave.

Why this phase exists: the profile names the recon-gen README and the QuickSight
quirks catalog as prose Claude wrote *under chotchki's guidance* — so they carry
the AI voice, not his. Converting that polished-AI prose back to his raw voice IS
the job. His own SPECs and design-thoughts are the opposite — voice SOURCES — and
get left alone.

---

## 1. The lint (run on every draft)

**Meta-rule above all:** maximize INFORMATION DENSITY bounded by digestibility.
The root value is respecting the reader's time — a dense 500 words beats a sparse
200. Most tells below are just low-density fluff; this is their parent. Never
bring a problem without a proposed solution. Prefer the densest CARRIER — a link
or a diagram often beats prose.

STRIP (he never does these):
- **Crafted maxims / takeaway buttons / thesis-restatement recaps** — in TITLES
  and HEADERS too. Replace with a mechanism, a real-world analogy, or blunt
  candor. The recap FUNCTION dies; an antithesis header for a GENUINE contrast
  survives. CAPS does not redeem a recap. *(his #1 remaining tell — hunt it hardest)*
- **Marketing adjectives:** seamless, powerful, robust, cutting-edge, world-class,
  best-in-class, leverage, utilize, holistic, unlock, realistic-as-puffery.
- **"load-bearing"** — a Claude crutch he deletes ON SIGHT. Replace with the
  concrete dependency or a blunt word ("the part I can't lose", "what breaks
  everything else"), never the metaphor.
- **Rule-of-three balanced triads** and the balanced "X — thing — Y" restatement
  cadence (the signature AI rhythm). Includes the dash-interjected "no X, no Y,
  just Z" three-beat. It's about the RHYTHM, not the dash glyph.
- **Fluff / throat-clearing / journey-narration:** windups, scene-setting, "let
  me walk you through", meta-commentary, padding, rhetorical-question openers.
- **Conclusion/summary wrap-ups** ("In conclusion", "To summarize"); restating
  the premise before answering.
- **Hype:** "Great question!", "Absolutely!", exclamation-point peppiness.
- **Oxford / serial commas** — a list of 3+ is "a, b and c", never "a, b, and c".
  (A comma correctly joining two INDEPENDENT clauses stays.)
- **Context-assuming words** (ACCESSIBILITY): "tests are green" / "make the red go
  away" → "tests pass" / "make the failures go away" (colored output isn't
  universal; red/green colorblind readers can't use it). Gloss real jargon. Cut
  the context-assuming term, never the vividness.
- Over-formalizing his casual register (lowercase starts, comma-splices,
  directness) into stiff prose. Fixing SPELLING is NOT over-polish.

MAKE (the moves that read as him):
- **Verdict first, then a short "why" clause.** State the decision, justify in
  one clause, stop. Not "here are the considerations… therefore."
- **Emphasis = ALL-CAPS on a single load-bearing word + parentheticals.** NOT
  bold, NOT italic, NOT balanced triads. One capped word per beat.
- **Parentheticals are his signature** — caveats, scope-cuts, examples,
  downstream-consequence flags, "this actually bit us" asides.
- **Honest about uncertainty** — "I don't know", "we'll test when we get there".
  Never bluff a guess as fact; never weasel.
- **Scope discipline** — name what is explicitly NOT the job and who owns it
  ("That's for regulators").
- **Counterbalanced pairs** — hold both sides of a tension (density ↔
  digestibility, speed ↔ trust, default ↔ escape-hatch); don't flatten to one.
- **Defaults-with-an-escape-hatch** — "start with X, see how it goes".
- **Concrete-reality grounding** — real tools / commands / numbers, not
  abstraction. Build the spine on `recon-gen json apply`, `up_to=<layer>`, real
  counts, not philosophy.
- **Mechanism over cute when they compete** — accuracy wins, but keep accurate
  vividness (puns, "landmine", "memory of a goldfish" all stay).
- **Links carry proof + optional depth** — a backable claim links to where it's
  proven and to optional examples; depth offloads to the link, prose stays dense.
- **Diagrams for structure/relationships** — text-based + LLM-parsable (Mermaid /
  D2 / DOT / ASCII), meaning over polish.

**Register dial (per-SECTION, not just per-doc):** a README can hold an
end-user install section (gloss the jargon) AND a dev section (jargon stays).
- *Specs/design docs:* nested bullets, invariant language, WHO/WHY before WHAT,
  open with intent + scope-disclaimer, just stop at the end.
- *Essay/audience-facing:* "for-you" reasoning (human consequence, not technical
  merit), jargon + a dead-simple gloss, philosophy in ONE plain sentence first,
  recommendations as honest conditionals with the cost stated, warmth via
  SERVICE + self-deprecation (never flattery).

---

## 2. Repo-specific hard constraints (THIS codebase)

These are non-negotiable on top of the lint. A voice win that violates one of
these is a regression.

1. **Preserve every macro / template token.** The handbook renders through
   mkdocs-macros + `HandbookVocabulary`. Jinja `{{ ... }}` / `{% ... %}`, the
   `diagram(...)` macro, and vocabulary substitution tokens are CODE — rewrite the
   prose AROUND them, never the token. Same for QS rich-text XML in per-sheet
   snippets and any `<<$param>>` placeholders.
2. **Persona-neutrality holds.** `tests/docs/test_docs_persona_neutral.py` asserts
   the persona-neutral docs name no specific institution. Do NOT introduce
   Sasquatch / persona names into neutral docs while rewriting. (Also honors
   [[feedback_no_navy_cash_in_codebase]] — never the real-system name anywhere.)
3. **Technical meaning is invariant.** Every fact, number, file path, CLI flag,
   invariant statement, link target survives byte-for-meaning. Voice changes HOW
   it reads, never WHAT it claims. When unsure whether a phrase is decorative or
   load-bearing-fact, keep the fact.
4. **Keep the doc-content test gates green** (see §4). Several tests assert on
   handbook/doc strings; a rewrite that changes an asserted string updates the
   test in the SAME commit (the test's expectation is part of the contract, not
   collateral). No deferring red tests (POLICY 2).
5. **Don't touch chotchki-authored sources** (see §3) — rewriting an exemplar
   corrupts the voice model.
6. **No new Oxford commas, ALL-CAPS not bold** — restated because it's the
   highest-frequency mechanical miss across a large fan-out.

---

## 3. Provenance — touch vs leave

Rule: **Claude-produced prose → rewrite. chotchki-authored prose → leave (it's a
voice source).** When authorship is ambiguous, `git log --follow --format='%an'
<file>` + a read for register settles it before any edit.

| Surface | Disposition | Note |
|---|---|---|
| `README.md` | **REWRITE** | Named in the profile as AI-voiced. Calibration target (DT.1). |
| `docs/reference/quicksight-quirks.md` | **REWRITE** | Named in the profile as AI-voiced. |
| `src/recon_gen/docs/**` handbook prose | **REWRITE** | Claude-produced under guidance. Verify any file that reads like a chotchki SPEC. |
| 37 per-sheet help snippets | **REWRITE** | Claude-produced; render live in dashboards. |
| CLI `--help`, `common/handbook/**` prose | **REWRITE** | Claude-produced. |
| Root `SPEC.md` | **VERIFY FIRST** | If chotchki-authored (likely) → LEAVE. git-log before touching. |
| `src/recon_gen/docs/SPEC.md`, `SPEC_studio.md`, `SPEC_gap_feedback.md` | **VERIFY FIRST** | Could be his SPECs (sources) — LEAVE if so. |
| `docs/audits/**` | **LEAVE** | Dev-facing (operator-confirmed out of scope). |
| `CLAUDE.md` (any) | **LEAVE** | Agent instructions, machine-facing. |
| `PLAN.md` / `PLAN_ARCHIVE.md` | **LEAVE** | Task trackers. |
| `RELEASE_NOTES.md` history | **LEAVE** | Historical record; DT.7 adds a forward style note only. |

---

## 4. Doc-content test-gate map (don't break these)

Tests that assert on doc/handbook content — a prose rewrite must keep them green
(update the asserted expectation in the same commit when it's genuinely the
contract):

- `tests/docs/test_docs_persona_neutral.py` — neutral docs name no persona.
- `tests/unit/test_sheet_handbook_liveness.py` — per-sheet snippets present/render.
- `tests/unit/test_handbook_invariants.py` — invariant prose ↔ registry.
- `tests/unit/test_handbook_l2ft_exceptions.py`, `test_l2_triage_gaps_handbook.py`
- `tests/unit/test_field_spec_handbook.py` — field-spec prose.
- `tests/unit/test_bu2b_registry_anti_drift.py` — registry anti-drift.
- `tests/unit/test_etl_examples.py` — ETL examples in docs execute/match.
- `tests/unit/test_parity_breaks.py` — qs-parity-breaks doc.
- `tests/docs/test_handbook_diagrams.py`, `test_cli_export_screenshot.py`
- App2↔QS handbook parity (per-sheet snippet rendering on both renderers).

Phase-exit gate (DT.8): full mkdocs build clean + all of the above green + link
check. Per-section: run the affected subset after each fan-out batch.

---

## 5. Per-file workflow (fan-out stages DT.3–DT.6)

Pipeline per file, no barrier between stages:
1. **Draft** — rewrite prose against §1 lint, honoring §2 constraints. Preserve
   macros/tokens/facts/links verbatim.
2. **Voice-lint** — adversarial, perspective-diverse: one critic per anti-pattern
   lens (maxim-headers, triads, Oxford, marketing-adjectives, "load-bearing",
   windup). A single critic misses tells (the profile records tells slipping past
   one voice-lint pass) — use several.
3. **Correctness check** — meaning preserved? every fact/number/link/token intact?
   persona-neutral where required? Diff vs original, fact-by-fact.
4. **Gate** — run the affected §4 tests for that surface; green before moving on.

Operator review at each section boundary, not per-file. README (DT.1) is the
exception — full operator red-pen before any fan-out, and his edits tune this
guide (DT.2) before the rest runs.

---

## 6. Folded from chotchki's README red-pen — v2 (2026-06-25, DT.2)

His isolated red-line of the README draft (commit `2425f602`). These are the
corrections his pen made that the lint above didn't catch — apply them to every
fan-out file. Full analysis upstream in `chotchki-voice-profile-evidence.md` v0.13.

- **Keep the first-person SERVICE FRAMING — don't de-market it.** "We help you
  implement", "we hand integrators X" are authentic warmth-via-service, NOT
  marketing-"we" to strip. Strip only tool PUFFERY ("we deliver powerful X").
  I over-fired the de-market reflex (changed "we close the loop" → "it closes
  the loop"); he restored "We help you implement". Note the discriminator: he
  KEPT my cut of a genuinely-vague support platitude ("issues get a real
  response") — so a vague clause can still go; the "we help you" FRAMING cannot.
- **Reader-facing prose gives the REASON, never the internal tag.** "(Phase
  CB.8)" → "since it isn't optimized for analytics". Strip phase ids / commit
  shas / internal bookkeeping from anything a customer reads; state the why.
- **Name by domain MEANING, not implementation.** "hash-locked" → "shape-locked"
  (locked to the L2 shape, not the SHA256 mechanism). CPA-readable terms win.
- **Introduce the doc's core named vocabulary at the hook, glossed inline.** He
  turned an abstract antithesis into two teaching bullets that name + gloss
  L1 / L2. If a doc hangs on named concepts, define them where the reader first
  meets the idea — restructure prose into a definitional scaffold if needed.
- **If you claim an N-way count, name all N parts.** "4-way agreement test" got
  "(against the underlying sql)" added to name the 4th leg.
- **Avoid brittle exact counts in living docs.** "Three runtime environments" →
  "Multiple" (the set churns; QS is on a deprecation path).
- **Don't cut his terse comma-splice verdicts.** "Swap the L2, the language
  follows." / "Iteration is one command." — the voice-lint flags these as
  takeaway-buttons; he KEEPS them. Flag borderline terse verdicts for his pen;
  never cut them outright.
- **CAPS one-per-beat + blunt strategic honesty stays.** "This WILL be
  deprecated … AWS … pricing" — name the real cause, don't soften.

Cross-validation: he KEPT every round-2 voice-lint edit (triad-breaks, Oxford
fixes, de-windup) — the adversarial lint was right; the misses were the
de-market over-fire plus the structural / content / strategy moves above. His
red-pen operates above prose-cadence, so a voice rewrite that only fixes cadence
under-reaches (the [[feedback-voice-rewrite-review-everything]] lesson, sharpened).

---

## 7. Doc-architecture: inline only the essential, link or generate the rest (DT, 2026-06-25)

A doc that REPRODUCES code-derived artifacts inline — directory trees, output /
file listings, dataset counts, constraint tables, config dumps — drifts, because
nothing regenerates them from the source. The DT staleness audit caught exactly
this on the README: the project-structure tree listed two deleted modules, the
`out/` listing undercounted every app's datasets, the forbidden-SQL restatement
had drifted from `Schema_v6.md`. Operator call (2026-06-25): de-drift by design.

Rules for the fan-out:
- **Inline only the small ESSENTIAL orientation** a reader needs in-place (a few
  lines: the key directories, the shape) — not an exhaustive mirror.
- **Link to the source** for anything that's a copy of code structure (the GitHub
  tree, `apps/*/datasets.py`, the authoritative reference doc). Links-as-proof,
  applied to structure — the link can't drift.
- **A diagram beats a wall when the thing is a RELATIONSHIP.** The README's
  file-tree became an L1/L2/L3 + three-renderers Mermaid diagram: a tree shows
  where files sit, the diagram shows how it's built. Text-based + LLM-parsable
  (Mermaid / D2 / DOT / ASCII).
- **Drop counts that drift** (per-app dataset numbers); keep the stable shape
  (filename patterns, "3 per app" — true by construction), cut the hand-kept ints.
- Mermaid renders on GitHub + mkdocs but shows RAW on PyPI — keep a one-line prose
  summary above the diagram as graceful degradation.
