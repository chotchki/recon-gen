# BX persona audit — full NUKE recommendation

> **Status:** AUDIT v2 — REVISED 2026-05-30. v1 recommended PARTIAL
> KEEP (trim 3 fields, keep institution + gl_accounts). Two operator
> follow-up questions sharpened the verdict to **full NUKE of
> `DemoPersona`**: promote `institution_name` + `institution_acronym`
> to top-level `L2Instance` fields alongside the existing top-level
> `description`; remove the `persona` field, `DemoPersona` dataclass,
> the `/l2_shape/persona/` editor route, the `_sasquatch_pr_vocabulary`
> intercept, and the entire stakeholder / merchant / gl_accounts /
> flavor surface. Landable as standalone Phase BXa, independent of
> the broader BX cold-read implementation work.

## Headline

`DemoPersona` is **doubly dead**:

1. The Sasquatch handbook vocabulary at `common/handbook/vocabulary.py:402-441`
   **hardcodes** its own `stakeholders` + `merchants` tables —
   `persona.stakeholders` + `persona.merchants` are NEVER read at any
   callsite.
2. The hardcoded values themselves are **never substituted into any
   docs page**. Across the entire `src/recon_gen/docs/` markdown
   corpus, `{{ vocab.stakeholders }}` / `{{ vocab.merchants }}` /
   `{{ vocab.gl_accounts }}` / `{{ vocab.flavor }}` are referenced
   exactly ONCE — in
   `walkthroughs/customization/how-do-i-brand-my-handbook-prose.md`
   which only DOCUMENTS that the substitutions exist; it doesn't
   USE them.

The only persona fields that drive any rendered output:
- `institution[0]` (name) — substituted in ~20 handbook + per-role
  markdown pages, plus Investigation app landing prose, plus audit
  PDF header
- `institution[1]` (acronym) — same substitution scope, plus the
  routing gate that picks the Sasquatch vocab

Those two fields belong on `L2Instance` directly, not nested inside a
persona block that wraps zero other live data.

## 1. Substitution audit — definitive

`grep -rnE '{{ vocab\.<field> }}' src/recon_gen/docs/` results:

| `vocab.<field>` | Substitution count in docs/ | Files |
|---|---|---|
| `institution.name` | ~16 callsites | index.md, all 5 for-your-role pages, all 5 handbook pages, customization.md |
| `institution.acronym` | ~6 callsites | for-your-role/{compliance-analyst,executive,integrator,etl-engineer,operator}.md |
| `stakeholders` | **0 callsites in actual prose** (1 doc mentioning the variable exists) | how-do-i-brand-my-handbook-prose.md only |
| `merchants` | **0 callsites in actual prose** (1 doc mentioning) | same |
| `gl_accounts` | **0 callsites in actual prose** (1 doc mentioning) | same |
| `flavor` | **0 callsites in actual prose** (1 doc mentioning) | same |

Plus the two non-docs consumers:
- `apps/investigation/app.py:207-212` reads `persona.institution[0]`
- `cli/audit/__init__.py:205-208` reads `persona.institution[0]`

## 2. Architectural smell — production code carries demo-flavor data

Even the institution-name routing path is suspect: `_sasquatch_pr_vocabulary()`
(production code in `common/handbook/vocabulary.py:344-466`) hardcodes
"Federal Reserve Bank" / "Payment Gateway Processor" / "Big Meadow
Dairy" / "Bigfoot Brews" / etc — strings that belong to one bundled
demo fixture, not to the shared codebase. Per the project layering
memory `[[project_design_north_stars]]` (L1 = persona-blind, L3 =
persona/customer flavor), having Sasquatch-specific strings in
`common/handbook/` is L3 leaking into L2/common.

The fix that "moves the Sasquatch strings into the example" gets
trivial once we observe nothing reads those strings anyway — there
are no values to move. Just delete the intercept.

## 3. Existing structure on `L2Instance`

From `common/l2/primitives.py:587-588`:

```python
# Top-level institution-level prose. Read by handbook templates as
# the "what is this institution" introductory paragraph.
description: str | None = None
```

The top-level `description` field is already there, doing exactly
what the persona block claims to wrap. **Promoting `institution_name`
+ `institution_acronym` to top-level fields next to `description`
collapses the conceptual ceremony.**

## 4. Recommendation: full NUKE + promote

### Survive on `L2Instance` (top-level fields)

| Field | Type | Today's source |
|---|---|---|
| `description` | `str \| None` | already top-level — unchanged |
| `institution_name` | `str \| None` | promoted from `persona.institution[0]` |
| `institution_acronym` | `str \| None` | promoted from `persona.institution[1]` |
| `investigation_personas` | `tuple[InvestigationPersonaSpec, ...]` | **NEW field** — promoted from the hardcoded table inside `_sasquatch_pr_vocabulary` (lines 360-391). Sasquatch fixture YAML grows an `investigation_personas:` block carrying the 6 curated entries (Juniper Ridge, Cascadia Trust Bank, Cascadia—Operations, Shell Company A/B/C). Other operator L2s default to empty tuple → existing `{% if vocab.demo.investigation.layering_chain %}` gates in the docs hide the walkthroughs that depend on these curated names. |

> **Why investigation_personas survives (added on follow-up grep):**
> `{{ vocab.demo.investigation.anchor.name }}`,
> `{{ vocab.demo.investigation.layering_chain[0].name }}`,
> `{{ vocab.demo.investigation.anomaly_pair_sender.name }}` are
> substituted **~20 times across `docs/handbook/investigation.md` +
> `docs/walkthroughs/investigation/what-does-this-accounts-money-network-look-like.md`**. The curated narrative IS load-bearing.
> Different from stakeholders / merchants which were also hardcoded
> in production code but are NEVER substituted in any rendered page.

### Die

- `L2Instance.persona` field
- `DemoPersona` dataclass + `common/persona.py` module
- `_PERSONA_INSTITUTION_FIELDS` + `_persona_form_to_dict` +
  `_persona_dict_from_instance` + `_render_persona_form` in
  `common/html/_studio_editor_routes.py`
- The `/l2_shape/persona/` editor route
- `_load_persona` in `common/l2/loader.py`
- `_dump_persona` in `common/l2/serializer.py`
- `_has_sasquatch_persona` + `_SASQUATCH_PERSONA_ACRONYM` in
  `common/handbook/vocabulary.py`
- `_sasquatch_pr_vocabulary` + the routing-gate dispatch
- `StakeholderVocabulary` + `MerchantVocabulary` + `GLAccount` types
  in `common/handbook/vocabulary.py` (no longer referenced after the
  intercept goes)
- `tests/unit/test_persona.py` (smoke for the deleted dataclass)
- `tests/audit/test_persona_clean.py` (replaced by simpler
  "no L2 fixture leaks into spec_example renders" check)
- `persona:` blocks in `tests/l2/sasquatch_pr.yaml` (lifted to
  top-level)
- The `how-do-i-brand-my-handbook-prose.md` walkthrough's
  description of the doomed substitution variables — either cut
  or rewritten to reflect that custom prose substitution is a
  PR-templates-yourself path

### Reshape

- `common/handbook/vocabulary.py::vocabulary_for(l2_instance)` —
  the routing dispatch goes; `_neutral_vocabulary_for` renames to
  `vocabulary_for_l2` and becomes THE builder. Reads
  `instance.institution_name` / `instance.institution_acronym`
  directly. Empty `HandbookVocabulary.stakeholders` /
  `.merchants` / `.gl_accounts` / `.flavor` tuples are emitted
  (the `HandbookVocabulary` shape stays — it's the typed surface
  for the rendered docs; the FIELDS just no longer carry data
  because nothing renders them).
- Even cleaner alternative: drop the four unused fields from
  `HandbookVocabulary` entirely. Lock in BXa.0.

### `/l2_shape/instance/` editor singleton — UPGRADE

The cold-read flagged the raw YAML textarea on `/l2_shape/instance/`
as a P1 ("a consultant won't survive this"). With persona gone, the
singleton becomes a clean 3-field structured form:

```
Institution name      [______________________]
Institution acronym   [____]
Description           [Edit | Preview]
                      [_________________________________________]
                      [_________________________________________]
                      [_________________________________________]
```

Markdown preview on description per the existing BF.9 pattern.
Replaces both `/l2_shape/persona/` AND the YAML-textarea singleton.
**This is BX P1.1 "Instance singleton structured form" collapsed
into the persona-nuke cell.**

## 5. Phase BXa scope (standalone, independent of broader BX)

This audit is well-scoped + evidence-led + uncouples from the rest
of BX (no dependency on the cold-read implementation cells, the
BTa side-panel, or the L2 author cold-read v2). Land as **Phase
BXa**:

- **BXa.0** — REPLAN (~30-60 min). Lock the field-shape decision
  (drop the unused `HandbookVocabulary` fields entirely vs keep them
  as empty tuples for the docs-substitution promise). Lock the
  fixture-migration order (sasquatch_pr.yaml first, locked-seed
  spot-check, then code).
- **BXa.1** — Schema + vocab refactor (~2-3h). Drop `persona` from
  `L2Instance`, add `institution_name` + `institution_acronym` top-
  level fields, update loader / serializer / validator. Delete
  `_sasquatch_pr_vocabulary` + `_has_sasquatch_persona` + routing
  gate. Rename `_neutral_vocabulary_for` → `vocabulary_for_l2`.
  Update Investigation app + audit PDF + handbook vocab to read
  the new top-level fields. Migrate `tests/l2/sasquatch_pr.yaml`
  in the same commit (per `[[feedback_no_compat_shims]]`).
  Spot-check locked-seeds unchanged (persona was already persona-
  blind per `tests/data/test_seed_persona_clean.py`).
- **BXa.2** — Editor singleton rebuild (~2-3h). Replace
  `/l2_shape/persona/` route + `/l2_shape/instance/` YAML-textarea
  with a single structured `/l2_shape/instance/` form (name +
  acronym + description with markdown preview). Drop `_render_persona_form`
  + `_persona_form_to_dict` + `_persona_dict_from_instance` +
  `_PERSONA_INSTITUTION_FIELDS`. Update + rewrite
  `how-do-i-brand-my-handbook-prose.md`. Update browser-dogfood test
  for the new instance form. **Closes BX P1.1.**
- **BXa.3** — Verify + close (~30 min). Full unit + a manual
  /l2_shape/instance/ + diagram render check; tick BXa, archive
  Phase BXa to PLAN_ARCHIVE.md.

**Total estimate:** ~5-7h.

**Dependencies:** none. Can fire in parallel with BTa, in parallel
with the rest of BX, before or after any of them. The only
side-effect on other phases: BX's P1.1 cell collapses into BXa.2,
so BX.0.7 (REPLAN-with-triage) lands one fewer cell.

## 6. Risks

- **Persona-aware tests** — five test files mention persona
  (test_persona.py / test_investigation_getting_started_persona.py /
  test_docs_persona_neutral.py / test_persona_clean.py /
  test_seed_persona_clean.py). Most are smoke for the dataclass
  surface; they need updating or deletion. Pre-existing
  test_docs_persona_neutral.py (which asserts spec_example renders
  zero persona tokens) becomes simpler — just spec_example renders
  zero Sasquatch tokens.
- **Operator-owned L2 YAMLs** with persona blocks — the loader
  should gracefully drop unknown keys (it already does for unknown
  top-level keys). Add a one-line note to the migration commit
  message.
- **`HandbookVocabulary` shape change** (if we drop the unused
  fields) — the customization walkthrough page becomes more
  honest about what's actually substitutable; no runtime impact.
- **Sasquatch flavor in handbook prose** — the handbook templates
  don't `{% if vocab.stakeholders %}` gate anything currently
  (verified via grep). No behavior change.

## 7. Closing

The persona surface is the cleanest "75% dead UI + 100% misleading
about its purpose" we've seen this cycle. The fix collapses
3 future BX cells (P1.4 rename, P3.5 surfaces-as pointers, P3.6
add-stakeholder button) into a single phase BXa that's net
code-removal + one structured-form upgrade. The architectural
benefit (no more L3-leaking-into-L2 in vocabulary.py) is genuine.
The user-facing benefit (no more raw-YAML instance singleton) is
also genuine. Land BXa standalone whenever convenient — it
doesn't block BTa, BX, or any release.
