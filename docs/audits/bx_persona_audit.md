# BX persona audit — keep, kill, or partial?

> **Status:** AUDIT COMPLETE 2026-05-30. Discovery cell triggered by
> operator suspicion ("docs/audits/bx_cold_read_v1b_author.md
> P1.4 — operator: 'this needs the research on where Persona is
> even used... if its just in the docs its probably better to remove
> and replace with neutral filler'"). Output: evidence-led
> recommendation + migration plan. Drives BX.0.7's cell enumeration.

## Headline

**PARTIAL KEEP.** Keep `institution` (name + acronym) + `gl_accounts`
+ `flavor[1]` (region) + `flavor[2]` (legacy_entity). **Nuke**
`stakeholders` + `merchants` + `flavor[0]` (customer name, never
read). Those three field tuples are written to YAML, parsed by
the loader, serialized back, rendered in the editor form — and
then **ignored at every callsite** because the Sasquatch
vocabulary builder hardcodes its own stakeholders + merchants
that override anything the persona block carries.

## 1. Model + form surface

### 1.1 Definition

- `src/recon_gen/common/persona.py:22-59` — `DemoPersona` frozen
  dataclass.
- `src/recon_gen/common/l2/primitives.py:44, 612` —
  `L2Instance.persona: DemoPersona | None`.

**Fields:**

| Field | Type | Purpose (as documented) |
|---|---|---|
| `institution` | `tuple[str, ...]` | (name, acronym, region, legacy_entity) |
| `stakeholders` | `tuple[str, ...]` | Upstream-counterparty display strings |
| `gl_accounts` | `tuple[GLAccount, ...]` | GL account labels (code, name, note) |
| `merchants` | `tuple[str, ...]` | Merchant DDA display names |
| `flavor` | `tuple[str, ...]` | Free-form persona strings (customer name, region, legacy entity) |

### 1.2 Plumbing

- **Loader:** `src/recon_gen/common/l2/loader.py:506-577`
  (`_load_persona`), called from line 1447 in `load_instance`.
- **Serializer:** `src/recon_gen/common/l2/serializer.py:337-358`
  (`_dump_persona`), called from line 87 in `serialize_l2`.
- **Editor form:** `src/recon_gen/common/html/_studio_editor_routes.py`
  - `_PERSONA_INSTITUTION_FIELDS` lines 3038-3044
  - `_persona_form_to_dict` lines 3047-3113
  - `_persona_dict_from_instance` lines 3116-3130
  - `_render_persona_form` lines 3132-3220
  - Singleton textarea fallback lines 2996-3031

## 2. Per-callsite ledger

| File:line | Fields read | Behavior | Classification |
|---|---|---|---|
| `common/handbook/vocabulary.py:211-214` | `institution[1]` (acronym) | Gate routing — Sasquatch vs neutral vocab | LOAD-BEARING |
| `common/handbook/vocabulary.py:394-400` | `institution[0]`, `institution[1]`, `flavor[1]`, `flavor[2]` | Populate `InstitutionVocabulary` (name + acronym + region + legacy_entity) | LOAD-BEARING |
| `common/handbook/vocabulary.py:402-412` | — | **HARDCODES** stakeholders ("Federal Reserve Bank", "Payment Gateway Processor"); never reads `persona.stakeholders` | n/a (proves stakeholders is dead) |
| `common/handbook/vocabulary.py:414` | `gl_accounts` | Pass-through to `HandbookVocabulary.gl_accounts` for narrative prose | LOAD-BEARING |
| `common/handbook/vocabulary.py:415-441` | — | **HARDCODES** merchants ("Big Meadow Dairy", "Bigfoot Brews", etc); never reads `persona.merchants` | n/a (proves merchants is dead) |
| `common/handbook/vocabulary.py:442` | `flavor` | Pass-through to `HandbookVocabulary.flavor` (mostly used for region + legacy_entity above; `flavor[0]` is never re-read) | PARTIAL load-bearing |
| `apps/investigation/app.py:207-212` | `institution[0]` (name) | Render "the {institution_name} shared base ledger" in Investigation landing prose; fallback to neutral when absent | LOAD-BEARING |
| `cli/audit/__init__.py:205-208` | `institution[0]` (name) | Render institution name in audit PDF header/footer; fallback to `cfg.deployment_name` | LOAD-BEARING |
| `common/html/_studio_editor_routes.py:3038-3220` | entire persona object | Editor form UI plumbing | LOAD-BEARING (infrastructure) |
| `common/l2/loader.py:506-577` | YAML persona block | Parse + validate | LOAD-BEARING (infrastructure) |
| `common/l2/serializer.py:337-358` | persona object | Dump back to YAML | LOAD-BEARING (infrastructure) |
| `tests/unit/test_persona.py:36-82` | all fields | Smoke (round-trip + defaults) | DEAD (test-only) |
| `tests/unit/test_investigation_getting_started_persona.py:50-56` | `institution[0]` | Assert Sasquatch renders name in Investigation prose | DEAD (test-only) |
| `tests/docs/test_docs_persona_neutral.py:122-176` | implicit handbook render | Assert spec_example handbook persona-blind | DEAD (test-only) |
| `tests/audit/test_persona_clean.py:61-97` | implicit audit render | Assert spec_example audit PDF persona-blind | DEAD (test-only) |
| `tests/data/test_seed_persona_clean.py:156-249` | implicit seed render | Assert spec_example seed SQL persona-blind | DEAD (test-only) |

### Field-by-field verdict

| Field | Status | Evidence |
|---|---|---|
| `institution[0]` (name) | KEEP — load-bearing | Investigation app + audit PDF + handbook vocab |
| `institution[1]` (acronym) | KEEP — load-bearing | Sasquatch vocab routing gate + handbook acronym substitution |
| `institution[2-3]` | implicit via `flavor[1,2]` mapping — covered below |
| `stakeholders` | **NUKE** | Sasquatch vocab hardcodes "Federal Reserve Bank" / "Payment Gateway Processor"; `persona.stakeholders` never read by any code path |
| `gl_accounts` | KEEP — load-bearing | `vocabulary.py:414` pass-through to `HandbookVocabulary.gl_accounts` for prose |
| `merchants` | **NUKE** | Sasquatch vocab hardcodes 5 merchants; `persona.merchants` never read by any code path |
| `flavor[0]` | **NUKE** | Never read by any non-test code |
| `flavor[1]` (region) | KEEP — defaultable | Populates `InstitutionVocabulary.region` (optional handbook field; renders only `{% if vocab.institution.region %}`) |
| `flavor[2]` (legacy_entity) | KEEP — defaultable | Populates `InstitutionVocabulary.legacy_entity` (same pattern) |

## 3. Surface tally

- **15 total callsites** including infrastructure + tests
- **6 LOAD-BEARING + 4 infrastructure + 5 test-only**
- Of the LOAD-BEARING set, **3 are doc-flavor** (Investigation prose,
  audit header, handbook substitution) and **3 are routing /
  selection** (vocab gate + GL pass-through + institution rendering)
- **Dashboard apps that read persona:** Investigation only.
  L1 / L2FT / Executives never touch `persona`.
- **Audit PDF:** institution name only.
- **CLI:** indirect — persona block surfaces only via handbook +
  Investigation + audit PDF render.

## 4. Recommendation: PARTIAL KEEP

### Survive (the 4 actually-used surfaces)

1. `institution` — (name, acronym) tuple. Used by handbook +
   Investigation + audit PDF.
2. `institution`'s optional positions 3-4 (region, legacy_entity)
   — currently smuggled in via `flavor[1]` and `flavor[2]`. **Promote
   them to named institution fields** so the API is honest.
3. `gl_accounts` — tuple of `GLAccount(code, name, note)`. Used by
   handbook prose.
4. The editor form sections for institution (Name, Acronym, Region,
   Legacy entity) + GL accounts grid.

### Die

1. `stakeholders` field — never read. Sasquatch vocab hardcodes
   its own list. Operator-entered values silently discarded.
2. `merchants` field — same as stakeholders. Hardcoded; ignored.
3. `flavor` field — `flavor[0]` is never read; `flavor[1]/[2]`
   should move into named `institution` fields.
4. Editor form sections for Stakeholders, Merchants, Flavor.
5. `_load_persona` / `_dump_persona` parser+serializer cases for
   the dead fields.

### Migration steps

1. **`DemoPersona` dataclass shape change** —
   `src/recon_gen/common/persona.py`:
   - Add named `region: str | None = None` + `legacy_entity: str | None = None` fields
   - Remove `stakeholders` / `merchants` / `flavor`
   - Keep `institution: tuple[str, str]` (now strictly name + acronym, 2-tuple)
   - Keep `gl_accounts: tuple[GLAccount, ...]`

2. **Vocab callsite update** — `common/handbook/vocabulary.py:394-400`:
   - Replace `persona.flavor[1] if len(persona.flavor) > 1 else None`
     with `persona.region`
   - Same for `legacy_entity`
   - Remove `flavor=persona.flavor` at line 442 (no longer needed)

3. **Loader update** — `common/l2/loader.py:506-577`:
   - Strip stakeholders / merchants / flavor parsing
   - Add region + legacy_entity parsing
   - Keep error messages helpful for the kept fields

4. **Serializer update** — `common/l2/serializer.py:337-358`:
   - Same shape change as loader

5. **Editor form update** — `common/html/_studio_editor_routes.py`:
   - Remove Stakeholders / Merchants / Flavor form sections
     (~80 LOC)
   - Add Region + Legacy entity to the Institution section

6. **Test update**:
   - `tests/unit/test_persona.py` — drop tests for removed fields;
     add tests for new region/legacy_entity fields
   - Other persona tests (docs / audit / seed) unaffected

7. **L2 YAML migration** — `tests/l2/sasquatch_pr.yaml`:
   - Remove stakeholders / merchants / flavor blocks under
     `persona:`
   - Promote `flavor[1]` to `region` field
   - Promote `flavor[2]` to `legacy_entity` field
   - Locked-seed regen if seed depends on persona shape
     (it shouldn't — `tests/data/test_seed_persona_clean.py`
     asserts persona-blind seeds)

**Impact:**

- Editor form shrinks ~60% (5 sections → 2: Institution + GL Accounts)
- Loader + serializer halve
- `DemoPersona` goes from 5 fields → 4 (institution name + acronym
  promoted to explicit names; region + legacy_entity added; 3
  dead fields removed)
- Sasquatch vocab unchanged at runtime — it was always going to
  hardcode its stakeholders + merchants anyway

## 5. BX cell mapping (operator-facing)

This audit reshapes the BX cells the cold-read enumerated:

| Cold-read item | Original ask | Post-audit verdict |
|---|---|---|
| P1.4 (rename Stakeholders → Correspondents) | Vocabulary rename | **MOOT** — field removed |
| P3.5 ("surfaces as:" pointers on Stakeholders/Flavor/Merchants) | Tooltips per field | **MOOT** — fields removed; Institution + GL Accounts get pointers in the side-panel (BTa.1 dependency) |
| P3.6 (Add stakeholder button styled as button) | Form polish | **MOOT** — field removed |

**New BX cell needed:**

- **BX.<N> Persona surface trim** (~2-3h): execute the migration
  above. Net code-removal (~150 LOC). Adds region + legacy_entity
  as named institution fields. Updates loader/serializer/form/tests
  + the `tests/l2/sasquatch_pr.yaml` fixture.

This cell **replaces** the original P1.4 + P3.5 + P3.6 in
BX.0.7's enumeration.

## 6. Risks + open questions

- **Test fixture mutation** — `tests/l2/sasquatch_pr.yaml` carries
  stakeholders + merchants today. Removing them is fine
  semantically (vocab ignores them) but the YAML diff is a
  visible change in the fixture. Locked-seed re-lock NOT
  expected (persona is persona-blind per
  `tests/data/test_seed_persona_clean.py`).
- **`spec_example.yaml`** carries persona = None already (per
  the explore agent's findings). No fixture impact there.
- **Other operator-owned L2 YAMLs** (the customer's actual
  institution YAMLs) — if any carry stakeholders + merchants +
  flavor, the loader will need to gracefully degrade. Options:
  (a) silent-drop unknown fields (loose loader), (b) hard-fail
  with migration prose pointing at the audit. **Recommend (a)** —
  the loader already silently-drops unknown YAML keys per
  `_load_persona`'s "permissive" stance.
- **Documentation hygiene** — handbook prose currently references
  `flavor` strings; review the rendered handbook after the cell
  lands to confirm no broken templates.

## 7. Closing

The persona surface is **75% dead UI** masquerading as
configuration. The cold-read flagged it accurately. PARTIAL KEEP
is the right call: preserve the 4 actually-used surfaces
(institution name + acronym + GL accounts + region/legacy_entity)
and delete the 3 cosmetic field tuples that operators fill out
but the code throws away.
