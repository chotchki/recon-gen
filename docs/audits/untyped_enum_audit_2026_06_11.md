# Untyped Enum Audit (2026-06-11)

Survey only — no code changes. Triages the bounded-enum surface that
leaks as bare `str` / `Identifier` across primitives, validator,
loader, seed/spine, schema, dashboards, and audit PDF. Reads as
follow-up triage from the ETL work; pairs with the codebase rule "encode
invariants in the type system, not validation tests"
([[feedback_invariants_in_types]]).

## Methodology

Grepped `src/recon_gen/common/l2/primitives.py`, `validate.py`,
`loader.py`, `schema.py`, `seed.py`, `etl.py`, and `common/spine/*.py`
for the candidate field names the operator called out
(`origin`, `direction`, `transfer_type`, `scope`, `cadence`, `subtype`,
`fan_in`, `account_kind`, `role_kind`, `status`, `supersedes`,
`completion`). For each hit:

- Read the field's primitives declaration (when present).
- Read the loader narrow-helper (or absence thereof).
- Read the schema column declaration to see whether the DB carries a
  `CHECK` constraint (the runtime backstop on the bounded set).
- Spot-checked seed + spine writers to see whether the value gets
  threaded as bare `str` from rail → row.

For consistency with the existing typed enums (`Scope`, `LegDirection`,
`LimitDirection`, `BalanceCadence`, `Period`, `SupersedeReason`), the
desired shape is `TypeAlias = Literal[...]` declared in
`primitives.py` + a corresponding `_load_<name>` narrow-helper in
`loader.py` that membership-checks + emits a `# type: ignore[return-value]`
(the established pattern at lines 244-266).

## Findings

| Field | Declared Type | Valid Values | Classification | Hours (est.) | Notes |
|---|---|---|---|---|---|
| `Account.scope` / `AccountTemplate.scope` | `Scope` = `Literal["internal","external"]` | `internal`, `external` | **fully typed** | 0 | Established pattern — schema CHECK at `schema.py:1939`; `_load_scope` at `loader.py:244` |
| `Rail.origin` / `source_origin` / `destination_origin` | `Origin: TypeAlias = str` | `InternalInitiated`, `ExternalForcePosted`, `ExternalAggregated` (+ open per SPEC) | **bare str leakage** (deliberate open) | 0 — keep open | SPEC explicitly calls Origin open at L1; integrators extend. Top of primitives.py spells this out at line 73-76. Don't tighten. |
| `LegDirection` (rail leg) | `Literal["Debit","Credit","Variable"]` | `Debit`, `Credit`, `Variable` | **fully typed** | 0 | Loader `_load_leg_direction` at `loader.py:252` |
| `LimitDirection` (cap watch perspective) | `Literal["Outbound","Inbound"]` | `Outbound`, `Inbound` | **fully typed** | 0 | `_load_limit_direction` at `loader.py:261` |
| `BalanceCadence` | `Literal["sparse","explicit_daily"]` | as named | **fully typed** | 0 | `_load_balance_cadence` at `loader.py:599` |
| `Period` (firings_typical_per_period) | `Literal["business_day","pay_period","week","month"]` | as named | **fully typed** | 0 | |
| `SupersedeReason` | `Literal["Inflight","BundleAssignment","TechnicalCorrection"]` | as named | **partially typed** | 2-3 | Type alias exists at `primitives.py:168`. Writers thread it as `supersedes: str \| None` everywhere (`seed.py:4470`, `seed.py:4609`, `spine/_emit_helpers.py:140`, `spine/supersession.py:140` writes the literal `"TechnicalCorrection"` as bare str). No `_load_supersedes` helper. **Fix:** annotate `_txn_row{_tuple,_to_sql}` `supersedes: SupersedeReason \| None`; pyright catches the call sites for free. |
| **`status`** (Transaction.Status) | bare `str` (default `"Posted"`) | `Posted`, `Pending`, `Failed` (per Schema_v6 / `etl.py:213`) | **bare str leakage** (closed enum, not typed) | 3-4 | Schema has NO CHECK constraint (`schema.py:1944` — `vc50 NOT NULL` only). Writers: `seed.py:4468/4607`, `etl.py:174`, every spine generator passes bare `"Posted"`. **Fix:** add `TransactionStatus: TypeAlias = Literal["Posted","Pending","Failed"]` to `primitives.py`; annotate writers; add `_load_status` helper to `loader.py`; add CHECK constraint to schema.py for symmetry with `amount_direction`. |
| **`amount_direction`** (Transaction.AmountDirection) | bare `str` everywhere downstream | `Debit`, `Credit` | **bare str leakage** (closed enum, schema-CHECKed) | 2-3 | Schema CHECKs (`schema.py:1942`); `etl.py:239` derives `"Credit" if money_cents > 0 else "Debit"` and passes bare; spine writers (~12 files) pass bare `"Credit"`/`"Debit"`. **Fix:** add `AmountDirection: TypeAlias = Literal["Debit","Credit"]` to `primitives.py`; reuse in `etl.py`, `_emit_helpers.TX_COLS`, all spine emit kwargs. (Distinct from `LegDirection` — that includes `"Variable"` which is a rail-declaration sentinel; `AmountDirection` is the row-realized accounting entry.) |
| **`account_scope`** (denormalized on transaction/balance rows) | bare `str` in spine emitters | `internal`, `external` | **partial Literal** (primitives is `Scope`; spine drops to `str`) | 1-2 | Schema CHECK present (`schema.py:1939`). Spine drops to bare `str`: `spine/chain_completion.py:66`, `spine/failed_transaction.py:51`, `spine/supersession.py:57`, `spine/inv_fanout.py:132`. **Fix:** thread `Scope` through every spine generator's `account_scope` kwarg + `inv_fanout.sender_scope`. |
| `direction` (write_transaction kwarg in seed/etl) | bare `str` | `Debit`, `Credit` | **bare str leakage** — same root as `amount_direction` | folded into row above | `seed.py:4462/4600` + `spine/plant_adapter.py:421/1014`. Subset of the `AmountDirection` fix. |
| `posting` (write_transaction kwarg) | bare `str` (ISO timestamp) | free-form ISO 8601 | **NOT a bounded enum** | 0 | Free-form timestamp literal; not an enum. |
| `TransferType: TypeAlias = str` (legacy alias) | bare `str` | open enum extending L1 | **deliberate open / legacy** | 0 — delete in Z.B.5 sweep | Comment at `primitives.py:90` flags this as Z.B-deprecated; new code uses `RailName`. Already on the cleanup roadmap. |
| `ChainChildSpec.fan_in` | `bool` | True/False | **fully typed** | 0 | Already a bool, not str. |
| `RailKind` (seed.py internal classifier) | `StrEnum` at `seed.py:1450` | 15 values; internal-only | **fully typed** | 0 | Module-private; classifier is heuristic against `transfer_type` substrings. |
| `account_kind` / `role_kind` (seed classifier) | bare `str` keys into `_STARTING_BALANCE_BY_ROLE_KIND` dict | `customer_dda`, `merchant_dda`, `internal_gl`, `concentration`, `internal_suspense`, `external`, `other` | **bare str leakage** — internal heuristic, low blast radius | 1 | `seed.py:1651::_classify_role` returns bare `str`; the dict at `seed.py:1624` is `dict[str, ...]`. **Fix:** module-local `_RoleKind: TypeAlias = Literal[...]` mirroring the dict keys. Confined to seed; no downstream impact. |
| `CompletionExpression` / `CadenceExpression` | `TypeAlias = str` | open template-vocab (`business_day_end+Nd`, `intraday-Nh`, …) | **NOT a closed enum** — regex-validated | 0 | Validator regex at `validate.py:268`. Open-ish vocabulary; Literal not the right shape. Leave alone. |
| `Origin` rail-level (open) | `TypeAlias = str` | open per SPEC | **NOT closing** | 0 | SPEC explicitly leaves this open at L1; only the v1 set is named. |
| `InvestigationPersona.role` | bare `str` | `convergence_anchor`, `counterparty_bank`, `operations_account`, `shell_entity` (current Sasquatch set) | **bare str leakage** — handbook template gates on specific values | 1 | Docstring at `primitives.py:670` already declares open enum. Lowest priority — only the handbook templates consume; failure mode is "section hides", not data corruption. Could close to `Literal[...]` since the four values are the only ones the templates check, but that locks integrator personas. **Recommend: leave open.** |

## Recommendation

Fix **top 4 closing-enum offenders** as one targeted phase, in order
(highest blast radius first):

1. **`amount_direction`** (`AmountDirection: Literal["Debit","Credit"]`)
   — touches every row of `<prefix>_transactions`, every spine generator
   (~12 files), `etl.write_transaction`, dashboards' Debit/Credit
   dropdowns, audit PDF's L1 Conservation invariant table. Biggest fan-out
   and the schema already enforces the CHECK; the type system is just
   catching up to what the DB already guarantees. ~2-3h.

2. **`status`** (`TransactionStatus: Literal["Posted","Pending","Failed"]`)
   — same fan-out as amount_direction, but **schema has no CHECK** (gap
   surfaced by this audit). Fix the type system AND add the CHECK in the
   same change. ~3-4h.

3. **`account_scope`** (reuse existing `Scope`) — partial-typed already;
   just need to thread `Scope` through 4 spine generators' kwarg
   annotations + `inv_fanout.sender_scope`. Schema CHECK exists. ~1-2h.

4. **`supersedes`** (use existing `SupersedeReason`) — type alias exists
   but unused at writers. Annotate the two `_txn_row*` helpers + spine
   supersession writer. ~2-3h.

**Total scope: ~8-12 hours** for the four-row sweep. All four follow the
same pattern as the existing `Scope` / `LegDirection` / `LimitDirection`
trio (Literal in primitives.py + `_load_*` narrow helper + annotated
emit signatures), so the change shape is well-rehearsed and the
diff-review fatigue is low per offender.

**Leave open:**

- `Origin` — SPEC explicitly open; integrators extend.
- `TransferType` legacy alias — already on the Z.B.5 deletion roadmap;
  don't tighten what we're about to delete.
- `CompletionExpression` / `CadenceExpression` — regex-validated open
  vocabulary, not a closed Literal candidate.
- `InvestigationPersona.role` — handbook-template-driven; locking
  forecloses integrator-specific persona sets for a small "wrong section
  hides silently" failure mode.

**Module-local cleanup (not part of the main sweep, opportunistic):**

- `_classify_role` → `_RoleKind` Literal alias. Confined to seed.py;
  ~1h; do as part of any seed-touching phase.

## Open questions for operator

1. **Schema CHECK on `status` is missing.** Found this incidentally —
   `transactions.amount_direction` has a CHECK enforcing `IN ('Debit',
   'Credit')` but `status` is just `vc50 NOT NULL`. Add the CHECK as
   part of the typing sweep, or split into a separate schema-tightening
   phase? My recommendation: fold into the typing sweep — same conceptual
   change (close the enum). Risk: any existing data carrying a non-canonical
   status value (unlikely given seed/spine all pass `"Posted"`/`"Pending"`/`"Failed"`)
   would block the migration.

2. **`amount_direction` vs `LegDirection` distinction.** `LegDirection`
   includes `"Variable"` (the closing-leg sentinel that lives only on
   the rail declaration); `amount_direction` on the realized row is
   only ever `"Debit"`/`"Credit"`. The Variable rail-leg gets resolved
   to a concrete direction at posting time by the containing
   TransferTemplate. Proposed: keep them as **two distinct Literals** —
   the type system catches "wrote `Variable` to a transactions row" at
   the call site instead of via DB CHECK rejection. Confirm shape?

3. **Origin open-vs-closed.** SPEC pins `{InternalInitiated,
   ExternalForcePosted}` as v1 with `ExternalAggregated` mentioned in
   `etl.py:212`. Current code reaches for all three but the L1 Dashboard
   filter dropdown derives from observed values at query time (no
   compile-time enumeration). Confirm: keep `Origin` open as stated, or
   close to those three and document integrators-extend-via-the-SPEC-
   mechanism rather than the type alias?

4. **Scope of the `_RoleKind` cleanup.** This is internal heuristic
   (seed-only); doesn't affect any consumer outside seed.py. Worth doing,
   or leave alone until next seed-touching phase organically picks it up?
