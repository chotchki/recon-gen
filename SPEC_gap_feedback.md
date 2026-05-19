## Enhancement 1: TransferTemplate constraints — multi-Variable legs + leg_rails XOR

### Problem

A real-world flow may have multiple OPERATING MODES with different timing characteristics that share the SAME closure semantic. The natural model is N Variable-direction "closing" rails inside one `TransferTemplate.leg_rails`, mutually exclusive per cycle.

The SPEC blocks this two ways:
- **C1**: "every TransferTemplate contains at most one Variable-direction leg per shared Transfer." Two Variable-direction closing-rail variants in one template fail load.
- **No leg_rails XOR**: chain XOR groups apply only to chained children, not to template legs. So even if C1 didn't block, "exactly one variant fires per cycle" can't be declaratively enforced.

### Where it bites in the Sasquatch example

Imagine SNB wants to extend its `MerchantSettlementCycle` template to support multiple settlement-timing modes per merchant. For example:

- **Auto-settle (T+0)** — high-volume merchants enrolled in intraday clearing, `max_pending_age: PT4H`.
- **Standard settle (T+1)** — most merchants, `max_pending_age: P1D`.
- **Slow settle (weekly)** — small low-volume merchants who batch settle on Fridays, `max_pending_age: P7D`.

The natural model is three Settlement-variant rails — `MerchantCardSaleAutoSettle`, `MerchantCardSaleStandardSettle`, `MerchantCardSaleSlowSettle` — all Variable-direction closing legs, all in `MerchantSettlementCycle.leg_rails`, with exactly one variant firing per (merchant_id, settlement_period) based on merchant config.

Both C1 and the absence of leg_rails XOR block this declaratively. Today integrators settle on a single Settlement rail without per-mode `max_pending_age`, losing operational tightness in the ETL-stuck-Pending dimension, or they fork the template into N separate templates (`MerchantSettlementCycleAuto` / `Standard` / `Slow`) and live with the per-rail-can-be-in-one-template constraint forcing parallel forks across the cardholder-side rails too.

### Proposed fix

Two sub-changes that together unblock the model. Our preferred implementation path:

**(A) Allow multiple Variable-direction legs per template when paired with leg_rails XOR.** Restate C1 as: "a TransferTemplate's `leg_rails` MUST resolve to at most one Variable-direction leg per *firing* — i.e., across legs declared mutually exclusive via XOR, exactly one Variable leg fires per shared Transfer." The "per firing" framing makes (B) a precondition for (A).

**(B) Add per-leg-rail XOR groups inside TransferTemplate.** Mirror the existing chain XOR group mechanism: a new `leg_rail_xor_groups: [[AutoSettle, StandardSettle, SlowSettle]]` field on TransferTemplate, where each inner list is a set of leg_rails of which exactly one fires per Transfer. Validator rule: members must all be in the template's leg_rails.

### Tradeoffs / open questions

- **Resolution at posting time.** Which leg-rail variant fires is determined by metadata at posting (merchant config). The library doesn't compute this — ETL chooses. Library validates that exactly one fired by the template's `Completion` deadline.
- **Interaction with PostedRequirements.** The auto-derived TransferKey-as-PostedRequirements rule still applies to all leg_rails (XOR'd or not). Each variant must declare matching MetadataKeys per existing R12.
- **Smaller alternative (deferred):** per-firing max_pending_age via metadata expression. More general than (A)+(B) but breaks the load-time-known nature of the watch. Skip unless a real integrator needs it.

---

## Enhancement 2: Chains can't express N:1 fan-in into shared TransferTemplate Transfers

### Problem

L1's `Transfer` entity has a single-valued `Parent?: Transfer` field. L2 chain semantics state that every child-rail firing produces a Transfer whose `Parent` is the matching parent firing's Transfer.

When the chain's child is a leg rail of a `TransferTemplate` (multiple firings join one shared Transfer via lookup-or-create on a TransferKey), and N distinct parent firings each want to be the Parent of that shared Transfer, the model breaks: the shared child Transfer has a single Parent slot, but N parents want to claim it.

### Where it bites in the Sasquatch example

Imagine SNB extends its merchant-acquiring service to support **batched ACH payouts** — a single ODFI ACH origination file that pays out N settled merchants in one batch. The natural model:

- N merchants each have their own `MerchantSettlementCycle` Transfer (per `(merchant_id, settlement_period)`)
- Each cycle closes via its `MerchantCardSale` leg-firings + the XOR-selected payout rail
- An ETL job groups multiple cycles into one batch (via a mapping `merchant_id` → `batch_id`)
- A new template `MerchantPayoutBatch` with `transfer_key: [batch_id]` would group N merchants' `MerchantPayoutACH` firings into ONE shared batch Transfer
- That shared batch Transfer's *Parent* would naturally be... all N merchant-cycle Transfers

Today integrators have to drop the chain structure entirely. The cycle→batch relationship becomes metadata-only (`batch_id` on each leg, ETL discipline ensures the mapping). No L1-structural enforcement that every closed merchant cycle gets covered by exactly one batch, or that a batch's contributions match its expected merchant set. The batch template's own `expected_net=0` verifies internal consistency (sum of merchant drains = external ACH amount), but the *cycle → batch linkage itself* is outside the SPEC's structural primitives.

### Proposed fix

Three options, in order of structural ambition:

**(A) Add `Transfer.ParentSet?: Set[Transfer]`** — multi-valued parent. L1 invariants like "this Transfer has at least one parent of type X" or "Σ parents' contributions equals this Transfer's amount" become expressible. Cleanest from a SPEC-design standpoint, biggest schema impact.

**(B) Add a chain semantic flag**: `parent: ..., child: ..., fan_in: true` — chains marked `fan_in` allow many parent firings to share one child Transfer. ETL provides multiple `parent_transfer_ids` per child firing; validator checks all parents resolve. Less schema impact than (A); just adds an alternative chain mode.

**(C) Don't fix in SPEC — document as a known limitation.** Batch grouping patterns remain metadata-only correlations. Integrators write dashboard-level checks ("for every closed cycle of type X, exactly one batch row of type Y references it via batch_id"). Pragmatic if N:1 batching is rare across the integrator population.

**Our preferred implementation path: (B)** — minimal schema impact, expresses the intent declaratively, doesn't force every chain to think about multi-parent semantics. (A) is overkill if N:1 patterns are uncommon; (C) leaves real recon work on integrators that fits naturally into L1's structural model.

### Tradeoffs / open questions

- **How common is N:1 in practice?** If batched payouts are the only meaningful example, (C) is defensible. If consolidated payouts, multi-source batch settlements, or similar patterns recur across integrators, (B) starts paying for itself.
- **Validator complexity**: most chain semantics today assume 1:N. (B) introduces a parallel code path for N:1. Worth doing if it eliminates a real integrator burden; not worth it for one edge case.
- **ETL implications**: ETL needs to provide N parent_transfer_ids per fan-in child firing. Manageable, but more state to track. With (C), integrators already do this via metadata anyway.
- **Audit trail**: the cycle → batch trace currently requires joining via metadata. With (A) or (B), it's a structural query. Auditors may have a preference here.
- **Interaction with XOR groups**: if a fan-in child is also in an XOR group, what does that mean? Probably: each parent's firing picks exactly one XOR variant, but multiple parents can pick the SAME variant (all converging on one shared Transfer). Worth pinning down.

---

## Enhancement 3: Chain `child` field — does it accept TransferTemplate, or Rail only?

### Problem

SPEC explicitly states: "Chain `parent` accepts both Rail and TransferTemplate." Symmetric treatment of `child` is **not** explicitly stated. Worked examples and existing instances chain to Rails as children.

If `child` is Rail-only, integrators with two-template chains have to choose one of the child template's leg_rails as the chain target, which is awkward and asymmetric. If `child` accepts TransferTemplate, the chain semantics need a clear rule for which leg_rail's firing sets the resulting Transfer's `Parent`.

### Where it bites in the Sasquatch example

Imagine SNB extends `InternalTransferCycle` to chain into a downstream **settlement-batch** template — `InternalTransferBatch` — that groups multiple internal-transfer cycles together for end-of-day netting through a clearing account. The natural model:

```yaml
chains:
  - parent: InternalTransferCycle    # template
    child: InternalTransferBatch     # template — is this valid?
    required: true
```

Both parent and child are TransferTemplates. SPEC allows the parent side; the child side is ambiguous. If template-as-child is rejected, the workaround is to chain to one of the batch template's leg_rails (e.g., the clearing-credit leg), which works mechanically but loses the conceptual clarity of "the cycle's outcome chains into the batch as a whole."

Same pattern surfaces in any scenario where one template's outcome leads to another template's firing — voucher batches downstream of per-merchant settlement cycles, payout batches downstream of clearing cycles, etc.

### Proposed fix

Allow `child: TransferTemplate` symmetric to `parent: TransferTemplate`. Semantics:

- When the FIRST leg_rail firing of the child template (per the template's lookup-or-create) creates the shared Transfer, that Transfer's `Parent` is set to the chain parent's Transfer (resolved via the firing's `parent_transfer_id` metadata).
- Subsequent leg_rail firings of the same template join the existing Transfer via lookup-or-create. Parent is NOT rewritten — already set.
- Validator rule: if `child` is a TransferTemplate, every leg_rail of that template MUST accept a `parent_transfer_id` metadata key (auto-derived).

**Our preferred implementation path**: adopt the proposal as stated. Symmetric treatment of parent and child cleans up the conceptual model and unlocks cleanly-expressed two-template chains.

### Tradeoffs / open questions

- **First-firing-wins is the natural rule** for setting Parent. If two leg_rails fire simultaneously (race), the storage layer's uniqueness constraint on `(template_name, transfer_key_values)` resolves the race; the winning leg's `parent_transfer_id` becomes the Transfer's Parent.
- **What if subsequent leg_rail firings claim a DIFFERENT parent_transfer_id?** Ambiguity. Could either (a) reject the firing as an L1 Conservation-style violation, or (b) silently ignore the conflict (first-firing-wins on Parent too). Lean toward (a) — surfaces the ETL bug rather than hiding it.
- **Symmetric with `parent: TransferTemplate` semantics**: when the chain parent is a template, the chain matching uses the template's Transfer (the shared one). Same mental model on the child side keeps the SPEC consistent.
- **Workaround if not adopted**: chain to a specific leg_rail of the child template. Works mechanically; loses the conceptual clarity of "Template A → Template B as a whole."

---

## Enhancement 4: Limit Breach invariant should support inbound caps

### Problem

L1's Limit Breach invariant evaluates `OutboundFlow` = `Σ |CurrentTransaction(Account = c, Transfer.TransferType = t, Amount.Direction = Debit, Status = Posted, ...).Amount.Money|` — DEBIT-side flow only. Real-world per-account caps come on both **inbound (credit)** and **outbound (debit)** flows, and the L1 invariant has no way to express the inbound case.

This blocks integrators from expressing legitimate inbound-cap policies declaratively. They either drop the cap from the L2 model entirely (enforcing it upstream at the ETL or at the originating system), or use a less appropriate workaround.

### Where it bites in the Sasquatch example

Imagine SNB wants to add a **daily inbound ACH cap of $20,000 per customer DDA** as an AML-flag threshold — any DDA receiving more than $20K/day of inbound ACH credits surfaces on the Exceptions sheet for manual review (potential structuring, unexpected deposits, etc.). Symmetric to the existing $12K outbound ACH cap.

The natural declaration:

```yaml
limit_schedules:
  - parent_role: DDAControl
    rail: CustomerInboundACH
    cap: 20000.00
    direction: Inbound        # <-- doesn't exist today
    description: |
      AML-flag threshold on inbound ACH credits per customer DDA.
      Exceeding cap surfaces on Today's Exceptions sheet for review.
```

Today this won't fire. Even if the LimitSchedule is declared, the underlying `OutboundFlow` theorem filters on `Amount.Direction = Debit`, so inbound credits never count toward the breach check. The integrator's only recourse: either enforce the inbound cap upstream at the ETL boundary (operationally workable but not visible in the L1 dashboard) or omit it from the model and live without that recon signal.

Same shape applies to other plausible cases:
- New-account inbound caps (lower until customer verification completes)
- Counterparty inbound caps (don't accept >$X/day from any single external entity)
- Merchant inbound settlement caps (flag unusual card-settlement volume)

### Proposed fix

Generalize the Limit Breach invariant (and the `OutboundFlow` theorem) to support both directions. Two flavors:

**(A) Parametric direction on LimitSchedule.** Add an optional `direction: {Outbound, Inbound}` field on `LimitSchedule` (default `Outbound` for backward compat). Add a parallel `InboundFlow` theorem mirroring `OutboundFlow` but filtering on `Amount.Direction = Credit`. Limit Breach picks the matching theorem at evaluation.

**(B) Single flow theorem, signed.** Replace `OutboundFlow` with a directionless `NetFlow` (signed sum of `Amount.Money`), and let the cap on `LimitSchedule` carry a sign. Less idiomatic but requires fewer new theorems.

**Our preferred implementation path: (A)** — the explicit-direction shape mirrors how operators talk about caps ("outbound cap", "inbound cap") and keeps cap values positive (more readable in YAML). The `Outbound` default preserves backward compatibility for existing L2 instances.

### Tradeoffs / open questions

- **Aggregate vs per-transaction.** The existing Limit Breach is per-day aggregate per child. Inbound cap is the same shape, just other direction. No new aggregation primitive needed.
- **Backward compat.** `direction: Outbound` default preserves existing semantics for any L2 instance not yet specifying.
- **Theorem proliferation.** Option (A) adds one theorem (`InboundFlow`). Option (B) replaces one with `NetFlow` and changes the cap semantics. (A) is more code but less conceptual churn; (B) is more elegant but introduces signed caps which are less intuitive in YAML.
- **OutboundFlow theorem references in existing code.** Option (A) leaves all existing references intact (since `OutboundFlow` stays). Option (B) touches every reference site to handle signed flows. (A) has lower migration cost.

---

## Enhancement 5: Chain `fan_in` should be per-child, not chain-level

### Problem

Enhancement 2's preferred implementation path put `fan_in: bool` on Chain — a chain-level flag governing every child of that chain row. Once shipped, this collides with mixed-cardinality downstreams: some children of a single parent are 1:1 (each parent firing produces its own child Transfer), others are N:1 (N parent firings converge on one shared batch Transfer). A chain-level flag can't carry both contracts.

Validator C8a tightens this further: `fan_in=true` requires every child to resolve to a TransferTemplate, so even mixed-rail-and-template children break the flag's all-or-nothing semantics regardless of cardinality.

### Where it bites in the Sasquatch example

Imagine SNB extends `MerchantSettlementCycle` with the Enhancement-2 `MerchantPayoutBatch` flow alongside the existing per-merchant payout vehicles. High-volume merchants enroll in batched ACH (N cycles → 1 batch); low-volume merchants keep individual ACH / wire / check (1 cycle → 1 payout). The natural model is one chain off `MerchantSettlementCycle` with mixed-cardinality children:

```yaml
chains:
  - parent: MerchantSettlementCycle
    children:
      - MerchantPayoutACH         # 1:1 (low-volume merchants)
      - MerchantPayoutWire        # 1:1
      - MerchantPayoutCheck       # 1:1
      - MerchantPayoutBatch       # N:1 (high-volume merchants batched)
    fan_in: true                  # ← doesn't work for the 1:1 children
```

`fan_in: true` false-positives the 1:1 payout vehicles on `<prefix>_fan_in_disagreement` as `parent_count < 2` orphan-only rows (their parent set is always 1 — one merchant cycle, one payout). `fan_in: false` drops `MerchantPayoutBatch`'s structural audit entirely; the cycle → batch linkage falls back to metadata-only correlation.

Splitting into two chain rows off the same parent (one `fan_in: false` for the 1:1 vehicles, one `fan_in: true` for the batch) makes Chain B's singleton-Required false-positive every low-volume merchant cycle as orphan (their `transfer_id` is never in any `MerchantPayoutBatch`'s parent set).

The integrator's fork: drop the batched flow from chain hierarchy (lose AB.4 audit value) or split into per-merchant-tier templates (one cycle template per merchant tier — substantial duplication of cardholder-side rails for an enforcement gain).

### Proposed fix

Move `fan_in` (and `expected_parent_count`) from Chain-level to per-child. Children become a heterogeneous list — bare-Identifier OR mapping:

```yaml
chains:
  - parent: MerchantSettlementCycle
    children:
      - MerchantPayoutACH                  # bare-Identifier — defaults fan_in=false
      - MerchantPayoutWire
      - MerchantPayoutCheck
      - name: MerchantPayoutBatch          # mapping — fan-in flag per child
        fan_in: true
        expected_parent_count: 10
```

The chain-level `fan_in` field deprecates (one-cycle grace warning on load; serialize per-child going forward). Validator C8a-c become per-child rules: `fan_in=true` on a child requires that child to resolve to a TransferTemplate; `expected_parent_count` carries meaning only on per-child fan-in entries. The L1 `<prefix>_fan_in_disagreement` matview already keys per-child Transfer; only the chain CTE / topology source needs to read `fan_in` from the per-child mapping.

### Tradeoffs / open questions

- **Schema heterogeneity** — chain children list becomes `Identifier | mapping`. Loader, serializer, Studio editor card, and topology renderer all need to handle both shapes. Manageable but touches more surface than the bare-flag shape.
- **Backward compat** — pre-Enhancement-5 `fan_in=true` chains migrate to per-child mappings (mechanical translation: lift the chain-level fan_in down to each child entry). `fan_in=false` chains stay bare-Identifier; byte-equivalent through load + dump.
- **`expected_parent_count` follows** — moves per-child alongside `fan_in`; the validator rule stays "set only when this child carries fan_in=true."
- **C8a's "no rail children with fan_in" rule simplifies** — per-child means rail children are simply non-fan-in; the chain row can still mix template fan-in children and rail 1:1 children freely.

**Our preferred implementation path: adopt as stated.** Per-child shape mirrors how operators reason about each downstream branch's cardinality contract; chain-level was a conceptual shortcut that breaks once mixed-cardinality patterns surface in real integrations.

---

## Enhancement 6: L2 Flow Tracing chain-orphan dataset should enforce multi-children XOR semantics

### Problem

`docs/concepts/l2/chain.md` (the operator-facing semantic contract) states:

> Two or more children = XOR alternation. Exactly one of the listed children MUST fire per parent invocation. Used for branching cycles (e.g. an ACH return MUST fire as one of "NSF", "stop-pay", "duplicate" — not zero, not two; …).

But the runtime check in `src/recon_gen/apps/l2_flow_tracing/datasets.py::_declared_chains_cte` labels multi-children rows `required = 'Optional'`:

```python
is_required = len(c.children) == 1
required_label = "Required" if is_required else "Optional"
```

And `build_exc_chain_orphans_dataset` filters `WHERE e.required = 'Required'`. Multi-children chains never produce orphan exception rows regardless of which children fire or how many. The chain.md contract is doc-aspirational; runtime enforcement covers only the singleton-Required case.

### Where it bites in the Sasquatch example

Sasquatch's existing `MerchantSettlementCycle → [MerchantPayoutACH, MerchantPayoutWire, MerchantPayoutCheck]` is a 3-way XOR per chain.md prose — "exactly one MUST fire." Today's L2FT dashboard surfaces nothing if zero payouts fire for a settled merchant; the cycle closes (Conservation+Timeliness pass on the cycle itself), nothing downstream fires, and the operator's "every merchant gets paid" mental model has no exception row to drive triage. Same shape for any multi-children XOR in any L2 instance: `InternalTransferCycle` outcome branches, hypothetical ACH-return-reason XOR, etc.

The chain.md prose explicitly named "an ACH return MUST fire as one of NSF/stop-pay/duplicate — not zero, not two" as the canonical example of the contract. That contract is invisible at runtime.

### Proposed fix

Extend the L2FT chain-orphan dataset to enforce multi-children semantics. Two new violation kinds projected into `<prefix>_todays_exceptions` alongside the existing `chain_orphan` (singleton-Required) branch:

- **`multi_xor_missed`** — parent fired AND zero children of this chain row fired with `parent_transfer_id` pointing at this parent. The "zero of N fired" case (the chain.md "not zero" rule).
- **`multi_xor_overlap`** — parent fired AND ≥2 children of this chain row fired pointing at this parent. The "two of N fired" case (the chain.md "not two" rule).

Implementation mirrors AB.3.3's `_xor_group_violation` matview shape: per-chain children inlined as CTE rows (VALUES on PG/SQLite, UNION-ALL-of-SELECT-FROM-DUAL on Oracle) + LEFT JOIN against `<prefix>_current_transactions` grouped by parent_transfer_id with `HAVING child_count <> 1`. Studio editor and topology layers don't change — chain definition is the source of truth; renderer is unaffected. Audit PDF gains a per-invariant table for the new check_type.

Alternative implementation: relabel multi-children rows in `_declared_chains_cte` to drop `'Optional'` — but the chain-orphan matview's existing query is per-edge (one row per parent×child pair), not per-parent-firing-counting-firings-across-children. The aggregate shape that catches "exactly one" needs a new query branch; just relabeling the existing rows wouldn't fire the right exceptions.

### Tradeoffs / open questions

- **Two violation kinds, not one** — `missed` and `overlap` surface different ETL bug shapes (downstream never fired vs downstream fired twice via duplicate metadata) and route to different analyst remediation. Worth keeping distinct on the L2 Exceptions sheet.
- **Performance** — adds one CTE branch with `COUNT(DISTINCT child_template_name_or_rail_name) GROUP BY parent_transfer_id` on `<prefix>_current_transactions`. Analogous to AB.3.3's xor_group_violation matview which already aggregates by transfer_id; same cost profile.
- **Backward compat** — pre-fix L2 instances see new exception rows on Today's Exceptions where they didn't before. The signal was always supposed to be there per chain.md; the surfacing is the change. No yaml migration needed; the contract was already declared by chain shape.
- **Interaction with Enhancement 5** — once chains carry per-child `fan_in`, the multi-XOR check needs to skip per-child fan_in=true entries (their cardinality contract is enforced by `<prefix>_fan_in_disagreement`, not by the multi-XOR "exactly one" rule). Bake the skip into the CTE.

**Our preferred implementation path: adopt as stated.** The chain.md contract was written first and the runtime check was implemented as scaffolding for the singleton case; multi-children enforcement is the missing scaffolding step, not a SPEC change.

---

## Enhancement 7: Soft per-firing magnitude bounds on Rail

### Problem

The auto-scenario seed generator picks `Transaction.amount` per firing using internal heuristics — no operator-declared "typical magnitude" hint per rail. Synthesized amounts can be orders of magnitude off from real-world expectations: $100K card swipes, $0.42 wire transfers, $1 ACH originations sitting next to each other on the same dashboard. The plausibility ceiling on the dashboard drops to a level where reviewers stop trusting the visualization before they reach a real exception.

This is generator-facing primarily. The runtime side has no signal for "this posted Transaction's magnitude is way outside this rail's typical range" either — a SHOULD-constraint the SPEC could expose, but the immediate pain is the synthesized-data side undermining demo / training credibility.

### Where it bites in the Sasquatch example

Imagine an SNB integrator hands the demo to a non-technical reviewer — a new ops analyst, a compliance officer, a training audience — for the first hands-on walkthrough. The reviewer opens the L1 Daily Statement and sees a $73,294.18 `MerchantCardSale` next to a $0.42 `CustomerInboundACH` next to a $14,892,403.91 `InternalTransfer`. Their first reaction isn't "let me figure out which of these is an exception." It's "this isn't a real banking dataset, why am I looking at this?" The dashboard's training value collapses before the cross-tool agreement story even starts.

For SNB's rails specifically (real-world ranges in financial flows of this shape):

- `MerchantCardSale` — $5–$500 typical per swipe
- `CustomerInboundACH` — $50–$5,000 typical per item
- `CustomerOutboundACH` — $50–$10,000 typical per item
- `InternalTransfer` (DDA → DDA) — $20–$50,000 typical
- `CustomerFeeMonthlySettlement` — $0.25–$25 typical
- `InterestAccrual` — fractions of a cent to single dollars
- `ExternalCardSettlement` (aggregating) — thousands per daily batch

Without operator-declared bounds, the generator has no signal for what "credible" means per rail and produces uniform-random amounts that don't cluster anything like real financial activity.

### Proposed fix

Optional soft per-firing magnitude bound on `Rail`:

```yaml
- name: MerchantCardSale
  ...
  amount_typical_range: [5.00, 500.00]
```

Magnitude is absolute — the bound applies to `abs(amount)`. Direction is determined elsewhere (per `leg_direction` for fixed rails; per containing template's `ExpectedNet` closure for Variable rails).

**Generator behavior**: when present, the auto-scenario generator picks amounts from the declared range using a log-uniform default distribution (most financial flows cluster at the low end of their typical band; log-uniform reproduces that pattern). When absent, falls back to current heuristics.

**Optional runtime extension** (separate flag — integrators opt in): SHOULD-constraint surfacing real-data Transactions whose magnitude falls outside the declared bound as a `magnitude_anomaly` matview row. RFC 2119 SHOULD — not a load-time rejection, not a hard runtime fail. Useful for early-warning anomaly detection alongside the existing per-day `LimitSchedule` aggregate caps (different scopes, complementary signals — `LimitSchedule` catches "today's total ACH inbound > $20K AML threshold"; `magnitude_anomaly` catches "this single $50K wire is way outside the typical $50–$10K band, flag for review").

**Validator**: `min < max`, both positive. Restrict the field to non-aggregating rails — aggregator amounts derive from bundled children, so the per-firing bound's meaning is fuzzy; deferred to a future iteration if integrators want a sanity-check field on aggregators too.

### Tradeoffs / open questions

- **Distribution shape** — log-uniform default is reasonable for financial flows. Could extend to `amount_distribution: {median, sigma_log}` for operators with tightly-peaked flows (e.g. monthly payroll always ≈ $2500 ± $50 — range would say `[2450, 2550]` which is fine, but lognormal with median + sigma is more honest). Out of scope for v1; add the optional refinement field if integrators ask.
- **Aggregating rails** — scoped out for v1 per above. The aggregator's amount is downstream of its bundled children's amounts; per-firing bound's meaning needs more design thought.
- **Interaction with Enhancement 4 (inbound caps) and `LimitSchedule` generally** — different scopes. `LimitSchedule` = per-account, per-day, per-rail aggregate cap (hard runtime, matview violation on breach). `amount_typical_range` = per-firing typical band (soft generator, optional soft runtime). Both can coexist on the same `(rail, parent_role)` and surface complementary signals (aggregate cap breach vs single-firing magnitude anomaly).
- **Currency mixing** — single-currency instances assumed for v1; multi-currency instances would need per-rail currency respected by the generator's range picker. Out of scope.
- **Backward compat** — optional field, default unset. Pre-Enhancement-7 yamls byte-equivalent through load + dump.

**Our preferred implementation path: adopt as stated, generator-only first cut.** Add the optional `magnitude_anomaly` SHOULD-constraint matview as a follow-on if integrators want runtime anomaly surfacing — two-step rollout keeps the schema change small (one optional field) and lets the demo-plausibility win land first.

---

## How these proposals interrelate

Enhancements 2 (N:1 fan-in chains) and 3 (template-as-chain-child) compose naturally: if 3 lands first, two-template chains become expressible; if 2 then lands, the N:1 batching pattern becomes structurally enforceable. Enhancement 1 is independent — it tightens up template's `leg_rails` semantics for the per-mode-XOR case, separate from the chain plumbing. Enhancement 4 is entirely independent of the chain/template plumbing — it only touches the LimitSchedule / OutboundFlow surface. Enhancement 5 is a follow-on to Enhancement 2 — once `fan_in` ships chain-level, the mixed-cardinality bite surfaces and per-child relocation becomes the natural next move. Enhancement 6 is independent of the others — it brings the existing chain.md "exactly one MUST fire" contract under runtime enforcement, and Enhancement 5 needs to skip per-child fan-in entries to play nicely with it (a small CTE-level interaction, not a schema-level coupling). Enhancement 7 is entirely independent of the chain / template / fan-in surface — it's a Rail-level optional field plus a generator code path; it sits next to Enhancement 4 in operator vocabulary (both are "bounds you declare on a rail") but their scopes are non-overlapping (E4 = per-day aggregate, E7 = per-firing magnitude).

A staged adoption order that minimizes churn:

1. **Enhancement 4** first (inbound caps) — adds one optional field (`direction`) + one new theorem (`InboundFlow`). No interaction with chains, templates, or aggregating rails. Easy first win.
2. **Enhancement 3** (template-as-chain-child) — small, well-scoped, no schema-level addition. Just clarification + new validator rule.
3. **Enhancement 1** (multi-Variable + leg_rails XOR) — adds one field (`leg_rail_xor_groups`) and relaxes one constraint (C1). Self-contained.
4. **Enhancement 7** can land any time after the alpha train has bandwidth — one optional field on Rail + a generator code path. No interaction with the chain / template / fan-in machinery. Easy second win after Enhancement 4. The optional runtime SHOULD-constraint matview is a follow-on.
5. **Enhancement 2** next (N:1 fan-in) — bigger conceptual change. Ships `fan_in` as a chain-level flag.
6. **Enhancement 6** can land any time after Enhancement 3 — it's purely a runtime check that brings the chain.md contract under enforcement; no SPEC schema change. Reasonable to bundle with Enhancement 5 since the per-child `fan_in` interaction wants the CTE skip baked in.
7. **Enhancement 5** last — relocates `fan_in` per-child once Enhancement 2's chain-level shape proves bite-prone in mixed-cardinality flows. One-cycle deprecation window on the chain-level field.
