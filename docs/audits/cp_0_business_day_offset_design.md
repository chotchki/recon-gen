# CP.0 — `business_day_offset` per-entity design audit

**Status:** locked 2026-06-07. Inputs: Phase CP placeholder + 5 operator locks (PLAN.md `## Phase CP`) + chotchki's 5-question lock-down 2026-06-07.

## 1. Recap of the 5 phase locks

These are repeated verbatim from PLAN.md so CP.0 has a single source of truth at branch time. If any of these drift, the audit is the canonical reference.

1. **Per-entity, not per-role-with-uniformity-check.** Role-shared accounts CAN declare different offsets; no validator rule enforces uniformity.
2. **Top-level `L2Instance.role_business_day_offsets` DELETED, not deprecated.** Loader rejects the old key with a migration-pointer error.
3. **Editor form: simple integer FieldSpec on each entity form**, position adjacent to `expected_eod_balance`.
4. **AccountTemplate offset applies to all materialized instances.** No per-instance override.
5. **Migration is STRUCTURAL + ACTIVE FEATURE EXERCISE.** Fixtures show non-trivial offsets, fuzz generator emits offsets, test data generator respects them.

## 2. Open-question lock-down (chotchki 2026-06-07)

### 2.1 + 2.2 — Units + sign convention

**LOCK: signed integer hours in [-23, 23] inclusive** (chotchki 2026-06-07: "we should cap the range to -23 to 23, otherwise its nonsensical"). Positive = later EOD, negative = earlier. Closest to timezone-style offsets without committing to real-time zone resolution (no DST, no tz database, no daylight transitions). Anything outside [-23, 23] means "shift by more than a day," which has no operator-readable meaning at the EOD layer.

- Field type on dataclasses: `business_day_offset: int | None = None`.
- **Range enforcement at the dataclass layer** via `__post_init__` on `Account` + `AccountTemplate`: reject `business_day_offset is not None and not (-23 <= business_day_offset <= 23)` with a typed `ValueError` naming the rule. Per [[feedback_invariants_in_types]] — fail at construction, not at "and a test happens to walk the output and flag it."
- Editor form's `<input>` carries `min="-23" max="23"` for client-side hint; server-side `__post_init__` is the truth source.
- ISO 8601 duration (`PT4H`, `PT-2H30M`) deliberately rejected — sub-hour shifts are exotic enough that the simpler integer wins; if a future customer surfaces a 30-min EOD, we re-spike (and likely fold to minutes).

### 2.3 — Default

**LOCK: `None` ⇒ midnight-aligned.** No change from current semantics. The loader fall-through to `None` happens when the YAML key is absent.

### 2.4 — Scope gating

**LOCK: gate by `Account.scope`** — the field is meaningful on `scope: internal` accounts (we track their EOD balance) and meaningless on `scope: external` accounts because **we don't track the external counterparty's balance**, so an EOD offset has no consumer.

(Note: `Account.scope` is the coarse `internal | external` literal on the primitive. The finer-grained 6-value `account_type` taxonomy named in CLAUDE.md / `docs/Schema_v6.md` lives on the transaction-feed contract — `<prefix>_transactions.account_type` — not the L2 Account dataclass. CP gating happens at the coarse `scope` layer where the field exists.)

Implementation consequence:

- **Dataclass:** field exists on `Account` unconditionally (the dataclass doesn't know the scope semantics). No `__post_init__` rejection — loader accepts the YAML key on any account type.
- **Editor form:** the FieldSpec is unconditional in the spec list but the rendered HTML hides the field-row when `select[name="scope"]` is `external`. Pure CSS via Tailwind v4 `:has()` — same pattern as CO.3's aggregating-dependent field hiding (`assets/input.css` already has 4 rules of this shape). One additional rule, no JS.
- **Validator:** new rule `M.4.4.14a` (or wherever the next slot lands) — reject `Account.business_day_offset is not None` when `Account.scope == "external"`. Validator message names the rule + suggests removing the offset field.
- **AccountTemplate:** templates don't have a scope field directly; templates materialize into accounts whose scope is set per the materialization config. The template-level offset applies to whichever scope the instances land at. If a template materializes into external instances, the template's offset is rejected at validation time the same way as on a singleton account.

### 2.5 — Per-fixture offset choices + fuzz distribution

**DEFERRED to operator pick.** Chotchki 2026-06-07: "I'll defer, just need a mixture."

The implementation can proceed without locking the specific offset values — the dogfood / fuzz tests don't care about specific values, just non-trivial coverage. CP.6 ("update bundled fixtures") will:

- spec_example: at minimum one role-shared account pair with distinct offsets. Strawman shape until operator picks: London-style branch at `+0`, Tokyo-style branch at `+9` for some pre-existing ExternalCounterparty role pair... **WAIT** — Lock 2.4 forbids offsets on external accounts. So for spec_example, pick an internal role like `CustomerDDA` and split it across two scope=dda accounts with `+0` and `+9`. Equivalent demonstration of the per-entity-non-uniformity feature.

- sasquatch_pr: same shape but with role + offset values picked from the persona's existing account inventory. Operator picks.

- heavy_density_v1: bulk-randomize across material accounts. Operator picks the distribution shape.

- fuzz generator: strawman `60% None, 30% 0, 10% uniform in [-12, 12]` — preserves the "majority unset" default + still hits the non-trivial path with reasonable frequency. Operator confirms / adjusts in CP.7.

**CP.6 + CP.7 will surface a pick-this-now prompt** when the audit's strawman values aren't acceptable. Until then the strawman ships.

## 3. Worked YAML shape

### Before (current `_kitchen.yaml` / `spec_example.yaml` shape)

```yaml
role_business_day_offsets:
  CustomerDDA: 0
  GLControl: 0
accounts:
  - id: cust-001
    name: Customer Account 1
    role: CustomerDDA
    scope: dda
```

### After (post-CP)

```yaml
# role_business_day_offsets — REMOVED. Loader rejects the key.
accounts:
  - id: cust-001
    name: Customer Account 1
    role: CustomerDDA
    scope: dda
    business_day_offset: 0           # explicit midnight-aligned

  - id: cust-002-tokyo
    name: Customer Account 2 (Tokyo branch)
    role: CustomerDDA                # same role as cust-001
    scope: dda
    business_day_offset: 9           # 9h later — Tokyo-style EOD

  - id: external-bank-1
    name: External Counterparty
    role: SomeExternalRole
    scope: external
    # business_day_offset: 5         # ← REJECTED by validator M.4.4.14a

  - id: gl-1010-cash-due-frb
    name: Cash & Due From Federal Reserve
    role: GLControl
    scope: gl_control
    # business_day_offset absent ⇒ None ⇒ midnight-aligned (default)
```

## 4. SQL impact

`seed.py:_compute_eod_balances` currently looks up the offset via:

```python
offset_hours = l2.role_business_day_offsets.get(account.role, 0)
```

Post-CP:

```python
# Singleton account
offset_hours = account.business_day_offset or 0
# Template-materialized instance
offset_hours = template.business_day_offset or 0
# external accounts are skipped at a higher level — we don't
# compute EOD balances for them at all, so the offset is never read.
```

The `_compute_eod_balances` rewrite (CP.2) walks `instance.accounts` for singletons + walks `instance.account_templates` + each template's materialized-instance list for template-derived accounts. Both paths read the per-entity offset directly. Delete the `role_offsets` dict-lookup path.

## 5. Editor form (FieldSpec)

```python
# In _ACCOUNT_FIELDS (and _ACCOUNT_TEMPLATE_FIELDS):
FieldSpec(
    name="business_day_offset",
    kind="text",            # numeric coercion via int(s) on POST
    label="Business-day offset (hours)",
    placeholder="0",        # placeholder shows default-state visually
    helper=(
        "Hours from midnight UTC for this account's EOD cutoff. "
        "Positive = later (e.g., +9 for a Tokyo-style EOD); "
        "negative = earlier. Leave blank for midnight-aligned. "
        "External-counterparty accounts ignore this — we don't "
        "track their EOD balance."
    ),
    required=False,
)
```

Position: immediately after `expected_eod_balance` in both `_ACCOUNT_FIELDS` and `_ACCOUNT_TEMPLATE_FIELDS` — semantically adjacent (both EOD-related).

CSS hide rule (`assets/input.css`, alongside the existing 4 CO.3 rules):

```css
/* CP — business_day_offset is meaningless on external accounts
 * (we don't compute their EOD balances). Hide the field-row when the
 * scope select is set to external so operators don't see a
 * knob that won't have an effect. Validator M.4.4.14a rejects the
 * field-set + scope=external combo as a belt-and-suspenders
 * gate if the field-row gets populated via a script / non-form path.
 */
form:has(select[name="scope"] option[value="external"]:checked)
  div:has(> label[for="field-business_day_offset"]) {
  display: none;
}
```

## 6. Migration shape

All 4 existing fixtures (spec_example, sasquatch_pr, _kitchen, heavy_density_v1) carry empty `role_business_day_offsets` per the pre-phase probe. So:

- CP.1-CP.5 land without breaking byte-identity on any fixture (no offset values move from the old shape to the new).
- CP.6 (update fixtures) intentionally introduces non-trivial offset values per Lock 5; byte-identity breaks here for the affected fixtures, intentionally.
- CP.8 re-locks the affected fixtures' seed + semantic-lock files; commit message documents the expected delta.

## 7. Anti-drift coverage (CP.8 — generalized fuzz test)

The new generalized test (Axis 1 / Axis 2 of CP.8) catches both halves of the lock-5 contract:

- **Axis 1** asserts the fuzz generator EVENTUALLY populates `business_day_offset` across the seed pool (CP.7 contract).
- **Axis 1** also asserts the fuzz generator EVENTUALLY leaves it None (the majority case — anti-regression against "fuzz generator always populates everything").
- The validator `M.4.4.14a` (no offset on external) is exercised by a unit test of its own; the fuzz coverage gate doesn't need to know about scope-conditional rejection.

## 8. Sub-cell impact summary

| Sub-cell | Lock-down impact |
|---|---|
| CP.1 | `int | None = None` field on Account + AccountTemplate; `__post_init__` enforces `-23 <= offset <= 23` when not None. |
| CP.2 | Read offset from `account.business_day_offset` (singletons) or `template.business_day_offset` (template path). |
| CP.3 | Delete `L2Instance.role_business_day_offsets` + M.4.4.14. **Add `M.4.4.14a`** — reject offset on external. |
| CP.4 | FieldSpec as above + CSS hide rule on scope=external. |
| CP.5 | Drop `role_business_day_offsets_yaml` textarea from `/l2_shape/instance/`. |
| CP.6 | Bundled fixtures get non-trivial offsets; operator picks specific values. Strawman: split a role across two scope=dda (or similar internal scope) accounts with distinct offsets. |
| CP.7 | Fuzz emits offsets per `60% None, 30% 0, 10% uniform in [-12, 12]` (strawman; operator confirms). |
| CP.8 | Generalized fuzz coverage Axes 1 + 2 catches the field automatically; no field-specific test. |
| CP.9 | Re-lock; byte-identity expected to break per CP.6. |
| CP.10 | Dogfood gate asserts spec_example + sasquatch_pr round-trip their offsets. |
| CP.11 | Demo-surface verification — open dashboard, confirm offset-bearing account shows shifted EOD. |
| CP.12 | Sign-off + sweep. |

## 9. Done-when (CP.0 audit only)

This audit is done when:
- All 5 phase locks restated (§1 ✅).
- 5 open questions resolved with operator-locked answers (§2 ✅, with CP.6/CP.7 strawmans pending operator confirmation).
- Worked YAML shape documented (§3 ✅).
- SQL impact spelled out (§4 ✅).
- FieldSpec + CSS hide rule shape locked (§5 ✅).
- Migration plan documented (§6 ✅).
- Anti-drift coverage shape confirmed (§7 ✅).
- Sub-cell impact summary written (§8 ✅).

CP.1 can fire as soon as this audit lands.
