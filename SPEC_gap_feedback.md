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

## Enhancement 8: Soft per-period firing-count bounds on Rail

### Problem

Enhancement 7's `amount_typical_range` fixed per-firing magnitude plausibility but leaves the parallel concern of per-period firing COUNT unaddressed. The auto-scenario seed generator picks count-of-firings-per-rail-per-period using internal heuristics — no operator-declared "typical activity volume" hint per rail. Result: even when per-firing amounts are realistic, aggregate per-period volumes can be orders of magnitude off. A $50 typical card sale repeated 50,000 times per day produces a $2.5M daily aggregate that doesn't match the integrator's mental model for what the fixture's institution actually processes.

The dashboard top-line — daily / monthly aggregates — is what operators scan first when judging plausibility. Per-firing amounts and per-period counts both feed it; AB.5 addressed the former.

### Where it bites in the Sasquatch example

Imagine SNB's L1 Daily Statement after AB.5 ships: `MerchantCardSale` shows $50 typical amount (realistic per E7) but the generator fires 50,000 of them per day across all merchants — implying the bank processes $2.5M/day in card sales. For a small community-bank fixture (the sasquatch positioning), that's an order of magnitude too large; for a larger institution fixture, it might be undersized. Neither matches what the integrator wants the dashboard to look like for their audience.

Same bite across SNB's rails:

- `MerchantCardSale` — real for a small bank: 50-500 per day across all merchants
- `CustomerInboundACH` — real: 50-200 per day across all DDAs
- `InternalTransfer` — real: a few hundred per day
- `MerchantSettlementCycle` firings (template) — real: ~1 per merchant per business day
- `CustomerFeeMonthlySettlement` — real: one per DDA per month (= N firings where N = customer count)
- `InterestAccrual` — real: 1 firing per ledger account per month

Aggregating rails handle their own cadence via the existing `cadence` field; non-aggregating rails have no analogous control surface.

### Proposed fix

Optional firing-count hint on `Rail`:

```yaml
- name: MerchantCardSale
  amount_typical_range: ["5.00", "500.00"]
  firings_typical_per_business_day_range: [50, 500]  # institution-wide daily count
```

For rails with non-daily natural cadences, parallel fields by period:

```yaml
- name: CustomerFeeMonthlySettlement
  firings_typical_per_month_range: [80, 120]  # one per DDA per month
```

Or a generic form: `firings_typical_per_period: {period: business_day | pay_period | week | month, range: [N_min, N_max]}` (single field with a period vocab). Period vocab stays bounded and easy to validate.

**Generator behavior**: when present, the auto-scenario generator picks count-per-period from the declared range using uniform-random sampling. When absent, falls back to the current per-kind heuristic. Composes naturally with Enhancement 7 — count × log-uniform(amount_typical_range) = realistic aggregate-per-period totals.

Aggregating rails: their `cadence` field already governs firing frequency (one firing per cadence-period). Field is N/A on aggregating rails (matches Enhancement 7's aggregator scope-out).

**Optional runtime extension** (separate flag): SHOULD-constraint surfacing real-data periods where firing count is way outside the declared band as a `volume_anomaly` matview row. Useful for early-warning surveillance ("today's transfer count is 10× yesterday — what changed?"); separable from the generator-side fix and add as a follow-on if integrators want it.

**Validator**: `min ≤ max`, both non-negative integers. Period enum bounded.

### Tradeoffs / open questions

- **Period vocabulary** — multiple period-specific fields (`firings_per_business_day_range`, `firings_per_week_range`, ...) is more readable but adds N fields per supported period. Generic single-field form (`firings_typical_per_period: {period, range}`) is more compact and extensible. Lean generic, with `period: business_day` as default if only `range` is supplied.
- **Scope per-rail vs per-account** — default scope is "rail-wide aggregate per period." Per-account scoping (e.g., "each customer DDA gets 1-3 ACH inbounds per business day") is a useful extension for cap-shaped patterns; defer to v2.
- **Distribution within the count range** — uniform-random by default. Lognormal or operator-declared variants for over-dispersed data could be a refinement field analogous to Enhancement 7's planned `amount_distribution`. Defer.
- **Interaction with Enhancement 7** — fully independent. Generator picks count, then per-firing amount independently. Composes naturally; the win compounds (realistic amounts × realistic counts = realistic aggregates).
- **Backward compat** — optional field, default unset. Pre-Enhancement-8 yamls byte-equivalent through load + dump.

**Our preferred implementation path: adopt as stated, generator-only first cut.** Parallels Enhancement 7's adoption shape — one optional field on Rail (and TransferTemplate, for cycle-scope rails), default unset, log-uniform-or-uniform sampler keyed off the declared range. The optional `volume_anomaly` runtime matview as a follow-on if/when integrators ask for surveillance signals.

---

## How these proposals interrelate

Enhancements 2 (N:1 fan-in chains) and 3 (template-as-chain-child) compose naturally: if 3 lands first, two-template chains become expressible; if 2 then lands, the N:1 batching pattern becomes structurally enforceable. Enhancement 1 is independent — it tightens up template's `leg_rails` semantics for the per-mode-XOR case, separate from the chain plumbing. Enhancement 4 is entirely independent of the chain/template plumbing — it only touches the LimitSchedule / OutboundFlow surface. Enhancement 5 is a follow-on to Enhancement 2 — once `fan_in` ships chain-level, the mixed-cardinality bite surfaces and per-child relocation becomes the natural next move. Enhancement 6 is independent of the others — it brings the existing chain.md "exactly one MUST fire" contract under runtime enforcement, and Enhancement 5 needs to skip per-child fan-in entries to play nicely with it (a small CTE-level interaction, not a schema-level coupling). Enhancements 7 and 8 are entirely independent of the chain / template / fan-in surface — both are Rail-level optional fields plus generator code paths; they sit next to Enhancement 4 in operator vocabulary (all three are "bounds you declare on a rail") but their scopes are non-overlapping (E4 = per-day aggregate caps, E7 = per-firing magnitude, E8 = per-period firing count). E7 and E8 compose: count × magnitude = realistic per-period aggregate.

A staged adoption order that minimizes churn:

1. **Enhancement 4** first (inbound caps) — adds one optional field (`direction`) + one new theorem (`InboundFlow`). No interaction with chains, templates, or aggregating rails. Easy first win.
2. **Enhancement 3** (template-as-chain-child) — small, well-scoped, no schema-level addition. Just clarification + new validator rule.
3. **Enhancement 1** (multi-Variable + leg_rails XOR) — adds one field (`leg_rail_xor_groups`) and relaxes one constraint (C1). Self-contained.
4. **Enhancement 7** can land any time after the alpha train has bandwidth — one optional field on Rail + a generator code path. No interaction with the chain / template / fan-in machinery. Easy second win after Enhancement 4. The optional runtime SHOULD-constraint matview is a follow-on.
4a. **Enhancement 8** lands naturally right after Enhancement 7 — same generator-only first-cut shape, one optional field, parallel to E7's adoption. Together they let the fixture's per-period aggregates match operator intuition (count × magnitude = volume).
5. **Enhancement 2** next (N:1 fan-in) — bigger conceptual change. Ships `fan_in` as a chain-level flag.
6. **Enhancement 6** can land any time after Enhancement 3 — it's purely a runtime check that brings the chain.md contract under enforcement; no SPEC schema change. Reasonable to bundle with Enhancement 5 since the per-child `fan_in` interaction wants the CTE skip baked in.
7. **Enhancement 5** last — relocates `fan_in` per-child once Enhancement 2's chain-level shape proves bite-prone in mixed-cardinality flows. One-cycle deprecation window on the chain-level field.

---

## Generator implementation gaps surfaced during integrator phase-2 integration testing

Distinct category from the Enhancements above. Enhancements are SPEC additions — new fields or relaxed constraints the SPEC should grow. The items below are existing-behavior bugs / gaps in the generator implementation: code that doesn't deliver what the SPEC + release notes already promise, OR code calibrated for one fixture that doesn't generalize. No SPEC change needed; just upstream code fixes.

Surfaced when an integrator exercises a phase-2-style coverage sweep against an L2 instance that diverges from sasquatch_pr's exact shape (different chain topologies, different naming conventions, different plant-kind distributions). The sasquatch_pr fixture passes its locked tests because the tests cover sasquatch_pr's specific shape — but the surface area is wider than the fixture, and the gaps below land on any L2 with a structurally different layout.

Each item is reproduce-able against sasquatch_pr (or spec_example) by tweaking the fixture into the relevant shape; the description below cites the closest existing sasquatch_pr / spec_example structure as the integrator-visible reproduction target.

### Implementation Gap A: Picker Rail-parent restriction blocks 7 plant kinds for template-heavy L2s

**Severity:** gap (cumulative blocker for AB.2 + AB.4 + AB.6 plants against template-parent chains)

#### Problem

Three pickers in `auto_scenario.py` filter the chain set to **Rail-parent** chains only, silently omitting any chain whose parent resolves to a TransferTemplate. The docstring on `_pick_two_template_chain_inputs` (line 1718) acknowledges the design choice:

> "The parent MUST resolve to a Rail (not a TransferTemplate) — two-template chains where BOTH ends are templates are valid but produce nested-firing semantics out of scope for the AB.2 plant scaffold."

Same restriction in `_pick_fan_in_chain_inputs` (line 1807) and `_pick_multi_xor_chain_inputs` (line 1844). Cumulative effect: 7 plant kinds never auto-derive for any L2 whose chains are template-parented.

| Picker | Plant kinds blocked |
|---|---|
| `_pick_two_template_chain_inputs` | `TwoTemplateChainPlant`, `ChainParentDisagreementPlant` |
| `_pick_fan_in_chain_inputs` | `FanInChainPlant`, `FanInChainMissingParentPlant`, `FanInChainExtraParentPlant` |
| `_pick_multi_xor_chain_inputs` | `MultiXorMissedPlant`, `MultiXorOverlapPlant` |

#### Where it bites in the Sasquatch example

sasquatch_pr's `MerchantSettlementCycle` template post-AB.6.6 has both:
- A multi-children XOR group (`[MerchantPayoutACH, MerchantPayoutWire, MerchantPayoutCheck, MerchantWeeklyPayoutBatch]` — the last child carrying `fan_in: true`)
- AB.6's `_multi_xor_violation` matview wired for the runtime check

The plants land cleanly because the existing test fixture's `MerchantSettlementCycle` is reachable from a Rail-parent chain via `ReconciliationLeg → MerchantSettlementCycle` (the singleton-Required chain from spec_example). The fan_in plants land via `MerchantSettlementCycle` itself acting as parent of `MerchantWeeklyPayoutBatch`, BUT the picker rejects it because `MerchantSettlementCycle` is a Template.

Concrete reproduction shape for an integrator:

```yaml
chains:
  # Template-parent chain — picker omits
  - parent: MerchantSettlementCycle  # template, not rail
    children:
      - name: SomeChildTemplate
        fan_in: true
        expected_parent_count: 5
```

Result: `auto_scenario.default_scenario_for(instance).omitted` reports `"FanInChainPlant: no Chain declares fan_in=True (AB.4)"` — the message is doubly misleading: the YAML DOES declare `fan_in: true`, the picker just won't accept the parent shape.

For integrators with template-heavy L2 designs (any flow that uses TransferTemplate as a cycle aggregator AND chains downstream of it), this can block most of the AB.2/AB.4/AB.6 plant surface.

#### Proposed fix

Extend each of the 3 pickers to support TransferTemplate parents. Synthesize parent firings via the template's first leg_rail using `_pick_account_id_for_role_expr` (already used elsewhere in `default_scenario_for` to resolve template-instance accounts). Reuses an existing helper; one ~20-line change per picker.

Update docstrings to drop the "out of scope" language. Locked-seed determinism for the existing sasquatch_pr / spec_example fixtures should hold (the fixtures use Rail-parent chains as their primary plant target; the Template-parent path is new and additive).

#### Tradeoffs / open questions

- **Test coverage cost** — needs ~3 new unit-test cases per picker covering Template-parent shape. Not big; mirrors the AB.6 test pattern.
- **Locked-seed regen** — adding sasquatch_pr / spec_example fixture shape with a Template-parent chain (e.g., add `BulkAccrualSettlement` chained off `MerchantSettlementCycle`) is the natural validation; would regen seed hashes for that fixture.
- **Picker omit-reason messages need sharpening** — the current "no Chain declares fan_in=True" message is wrong in the Template-parent case. Should say "no Chain with fan_in=True AND a parent the picker supports".

---

### Implementation Gap B: AB.2 template-as-chain-child emit doesn't write `transfer_parent_id` for some shapes

**Severity:** bug (major — breaks AB.2's runtime check + L2FT chain_orphans against affected chain shapes)

#### Problem

The v11.2.0 release notes for AB.2 promised:

> "Seed — two-template chain firings. New `_emit_chain_child_template_legs` replaces the pre-existing silent-skip path at seed.py:2442-2446. Generates ONE shared transfer_id per chain invocation, iterates the child template's leg_rails, calls the existing `_emit_chain_child_leg` once per leg with `shared_transfer_id=` and `template_name=` injected. **All emitted rows share transfer_id, template_name, and parent_transfer_id**."

The emitted rows DO share `transfer_id` and `template_name`. Empirically, `transfer_parent_id` is NULL on emitted rows for at least one chain shape (template-parent + template-child, observed against an L2 with this structure).

Cascading effects:

- `<prefix>_chain_parent_disagreement` matview filters `transfer_parent_id IS NOT NULL` → never fires for affected chain shapes regardless of plant input. The AB.2 audit promise ("chain hierarchy is structurally enforced for template-as-child shapes") doesn't deliver for these shapes.
- L2FT `chain_orphans` dataset matches children via `transfer_parent_id IN (parent's transfer_ids)` → never matches → false-positive orphan count = parent firing count.

#### Where it bites in the Sasquatch example

Reproduction target: an integrator declares a chain `MerchantSettlementCycle → MerchantWeeklyPayoutBatch` (both templates) — the canonical AB.6.6 mixed-cardinality shape. After `data apply --execute`, query:

```sql
SELECT template_name,
       COUNT(*) AS total,
       SUM(CASE WHEN transfer_parent_id IS NOT NULL THEN 1 ELSE 0 END) AS with_parent
FROM <prefix>_transactions
WHERE template_name = 'MerchantWeeklyPayoutBatch'
GROUP BY template_name;
```

Expected per v11.2.0 release notes: `with_parent = total`. If `with_parent = 0`, the bug is present.

(Sasquatch_pr's existing Rail-parent template-child chain `CustomerFeeAccrual → InternalTransferCycle` passes the check — `InternalTransferCycle` legs DO populate `transfer_parent_id`. The bug appears specific to Template-parent + Template-child shapes; the rail-as-parent path works correctly.)

#### Proposed fix

Trace `_emit_chain_child_template_legs` (seed.py:2925) execution for Template-parent chains. Two candidate causes:

1. The function isn't being invoked for Template-parent chains (the chain-emit machinery only calls it for Rail-parent shapes). Add the Template-parent code path.
2. The function IS invoked but the parent_transfer_id isn't being threaded through to `_emit_chain_child_leg` for this shape. Trace argument-passing.

Add a unit test that asserts `transfer_parent_id` is non-NULL on every chain-child template's leg row, parameterized across {Rail-parent + Rail-child, Rail-parent + Template-child, Template-parent + Rail-child, Template-parent + Template-child}.

Likely paired with Implementation Gap A's picker fix — same architectural blind spot ("Template-parent chains were out of scope at AB.2 design time"). Worth fixing both together.

#### Tradeoffs / open questions

- **Sasquatch_pr / spec_example locked seeds** stay byte-equivalent until a Template-parent chain is added to either fixture. Adding one (per Gap A's locked-seed regen note) is the natural validation point.
- **Backward compat** — fixing this populates `transfer_parent_id` where it was previously NULL. The AB.2 chain_parent_disagreement matview will start firing for chains where it was previously empty; L2FT chain_orphans dataset will start counting children correctly (orphan counts will drop where they were false-high).

---

### Implementation Gap C: `emit_baseline_chains` doesn't enforce multi-children XOR semantics

**Severity:** bug (baseline emits chain.md-violating activity; AB.6 matview correctly catches it as false-positive exceptions on healthy baselines)

#### Problem

`docs/concepts/l2/chain.md` prose:

> "Two or more children = XOR alternation. Exactly one of the listed children MUST fire per parent invocation."

`emit_baseline_chains` doesn't honor this contract. It can emit parent firings with zero matching children (missed) OR with two matching children (overlap). AB.6's runtime `<prefix>_multi_xor_violation` matview correctly catches both shapes; the operator-facing dashboard then renders them as exceptions on what should be a "healthy" baseline.

Pre-AB.6 this was latent — multi-children chains were untracked at runtime. AB.6's matview exposed the existing baseline-emit gap.

#### Where it bites in the Sasquatch example

Sasquatch_pr's `MerchantSettlementCycle` template chains to `[MerchantPayoutACH, MerchantPayoutWire, MerchantPayoutCheck, MerchantWeeklyPayoutBatch]` — a 4-children chain (3 non-fan_in + 1 fan_in). Per chain.md, exactly one non-fan_in child must fire per parent firing (the fan_in child is exempt — its semantics are governed by `<prefix>_fan_in_disagreement`).

Reproduction: fresh `data apply --execute` against sasquatch_pr, then:

```sql
SELECT parent_rail_or_template_name, disagreement_kind, child_count, COUNT(*)
FROM <prefix>_multi_xor_violation
WHERE parent_rail_or_template_name = 'MerchantSettlementCycle'
GROUP BY 1, 2, 3;
```

Expected on a healthy baseline: 0 rows for MerchantSettlementCycle (the picker enforces XOR per firing; baseline obeys). Actual (against L2 instances with multi-children chains): some count > 0 with `disagreement_kind` ∈ {missed, overlap}.

`AB.6.5.spec` added a dedicated `BulkAccrualSettlement → [BulkAccrualSettleACH, BulkAccrualSettleWire]` chain to spec_example specifically for plant coverage. That test passes because the plants explicitly synthesize the violations. The bug is in the *baseline* emit path — the routine activity that should fire chain children correctly.

#### Proposed fix

Add deterministic per-firing child-pick to `emit_baseline_chains`. Pattern matches AB.3's `_xor_suppressed_members` for `leg_rail_xor_groups`:

```python
def _baseline_xor_child_pick(
    chain: Chain, parent_transfer_id: str, base_seed: int,
) -> Identifier:
    """Pick exactly one child for this parent firing, deterministic."""
    if len(chain.children) == 1:
        return chain.children[0].name  # singleton-required
    non_fan_in = [c for c in chain.children if not c.fan_in]
    pick_seed = base_seed ^ zlib.crc32(
        f"{chain.parent}|{parent_transfer_id}".encode()
    )
    rng = random.Random(pick_seed)
    return rng.choice(non_fan_in).name
```

For every parent firing of a multi-children chain, pick exactly one non-fan_in child to fire; suppress siblings for that firing. fan_in children fire independently per AB.4 semantics (parent contributes regardless of XOR pick for non-fan_in siblings).

Rename-resilient (keys on `transfer_id`, not chain children's names). Same RNG-derivation pattern AB.3 already validates.

#### Tradeoffs / open questions

- **Locked-seed regen** — sasquatch_pr / spec_example seeds regenerate when the per-firing pick lands. The change is structural enough to bump the locked SHA-hashes; expected and documented.
- **Interaction with fan_in (Enhancement 5 per-child shape)** — the picker should skip fan_in children when picking the XOR target. fan_in children fire on their own AB.4 logic; XOR pick targets the multi-children Z.A grammar non-fan_in subset.
- **Backward compat** — pre-fix L2 instances see a drop in multi_xor_violation matview rows (the baseline noise floor goes from "some count > 0" to 0 on healthy baselines). Plants still fire violations on demand.

---

### Implementation Gap D: Rail kind classifier substring vocabulary tuned for sasquatch_pr; degrades for other L2s

**Severity:** gap (per-rail count + amount realism degrades for any L2 not following sasquatch_pr's CamelCase substring conventions)

#### Problem

`_classify_rail` (seed.py:1703) substring-matches on `rail.name.lower()` against a fixed vocabulary:

```
"return" | "cardsale" | "externalcard" | "payout" | "fee" | "inbound" |
"deposit" | "outbound" | "withdrawal" | "concentration" | "internal" |
"charge" | "subledger"
```

Falls to OTHER on no match. The vocabulary is calibrated for sasquatch_pr's CamelCase patterns (`CustomerInboundACH`, `MerchantCardSale`, `MerchantPayoutACH`, `CustomerFeeMonthlySettlement`, etc.). For integrators using different naming conventions — SOP-derived names, abbreviated forms, legacy snake_case, domain-specific vocabulary — most rails fall to OTHER. The OTHER bucket has `daily_target_per_unit=1.0, scaling_kind="system"`: 1 firing per business day system-wide, regardless of operational reality.

Additionally, the existing `"inbound"` pattern over-matches: any rail name containing `"inbound"` is bucketed as CUSTOMER_INBOUND (4/customer/day × customer_count). System-wide inbound rails (e.g., a single ACH batch from a payroll provider that fans out to N customers) get the per-customer scaling wrongly applied — produces ~80 firings/day where the real cadence is 1 per pay period.

#### Where it bites in the Sasquatch example

Sasquatch_pr itself doesn't trigger Implementation Gap D — its rail names are the calibration set, so every rail matches a non-OTHER pattern.

The integrator-visible reproduction is a rail-renaming exercise. Imagine an integrator working from operational documentation that names rails differently — e.g., renaming sasquatch_pr's `MerchantCardSale` to `RetailerSwipe` to match their domain vocabulary. `RetailerSwipe` matches none of the patterns → OTHER → 1/day system-wide instead of `8/merchant/day × merchant_count` (CARD_SALE).

Sample integrator rails likely to land in OTHER and produce wrong volumes:
- `RetailerSwipe`, `PointOfSalePosting`, `MerchantClearingDebit` (should be CARD_SALE-equivalent)
- `RefundChit`, `ReturnAuthorization`, `CardholderCredit` (should be CARD_SALE-refund-shape)
- `MonthlyServiceCharge`, `MaintenanceFee` (should be CUSTOMER_FEE; "fee" only matches if it's a substring — `MaintenanceFee` has it, `MonthlyServiceCharge` doesn't)
- `BatchACHCredit` (system-wide-scaled, not per-customer-scaled — but contains "credit" which isn't in the vocab → OTHER)

Net effect: any L2 instance whose rails don't follow sasquatch_pr's CamelCase substring conventions sees its dashboard's per-period volumes orders of magnitude off from operational reality.

#### Proposed fix

Two interventions, both small, in parallel:

1. **Broaden the substring vocabulary.** Add patterns that catch common alternative naming. Preserve existing-match order so locked seeds for sasquatch_pr stay byte-identical — append new patterns AFTER existing ones in the substring chain. Candidate additions:

| New substring | Maps to | Rationale |
|---|---|---|
| `"sale"` | CARD_SALE | More general than `"cardsale"`; catches `*Sale` patterns |
| `"swipe"` | CARD_SALE | POS-system terminology variant |
| `"refund"` | CARD_SALE | Refunds mirror sales in count + amount shape |
| `"chit"` | CARD_SALE | Refund-chit terminology |
| `"settlement"` | MERCHANT_PAYOUT | Settlement = per-merchant per-period scaling |
| `"voucher"` | MERCHANT_PAYOUT-like or new VOUCHER kind | Voucher batches are periodic per merchant |
| `"interest"` | new INTEREST kind or AGGREGATING_MONTHLY-like | Monthly accrual cadence |
| `"emit"` | AGGREGATING_MONTHLY-like | Voucher / batch emit per cycle |
| `"cash"` | new CASH kind | Cash-handling at branch / NCAO |

2. **Tighten the `"inbound"` pattern's over-match.** Either require `"inbound"` NOT also contain `"payroll"` / `"batch"` (route those to a new system-wide PAYROLL_BATCH kind), OR add `"payroll"` / `"batch"` as higher-priority patterns that win the matcher first.

Both fixes are short-term mitigations. The long-term answer is Enhancement 8 (operator-declared `firings_typical_per_*_range`), which short-circuits the classifier for any rail that declares it. Worth doing both — the classifier fix unblocks the immediate per-period plausibility for integrators who haven't yet declared E8 ranges; E8 is the universal opt-in.

#### Tradeoffs / open questions

- **New `_RailKind` granularity** — adding new kinds (INTEREST, CASH, VOUCHER, PAYROLL_BATCH) means new `_RailKindParams` entries with calibrated `daily_target_per_unit` + amount mu/sigma. Operator-visible knob proliferation; lean conservative.
- **Locked-seed determinism** — sasquatch_pr / spec_example seeds stay byte-identical as long as the new patterns are appended AFTER the existing ones (so they don't capture rails the existing chain already classified). Validated via locked-seed regression tests.
- **Test coverage** — add unit tests per new pattern showing the substring matches the expected `_RailKind`.

---

### Implementation Gap E: Trainer modules missing AB.1–AB.6 plant kinds

**Severity:** gap (bit-rot; Studio chrome shows incomplete per-node badges for post-AB.0 plant kinds)

#### Problem

Two trainer modules — `trainer.py::plants_per_node` and `trainer_timeline.py::_scenario_to_timeline` — were calibrated against the original 9 plant kinds and haven't been extended as AB.1 through AB.6 landed new plant kinds.

`trainer.py::plants_per_node` covers: drift, overdraft, limit_breach, stuck_pending, stuck_unbundled, supersession, failed, transfer_template, inv_fanout. **Missing:**

- `inbound_cap_breach_plants` (AB.1)
- `two_template_chain_plants` + `chain_parent_disagreement_plants` (AB.2)
- `xor_variant_missed_firing_plants` + `xor_variant_overlap_plants` (AB.3)
- `fan_in_chain_plants` + `fan_in_chain_missing_parent_plants` + `fan_in_chain_extra_parent_plants` (AB.4)
- `multi_xor_missed_plants` + `multi_xor_overlap_plants` (AB.6)

11 plant kinds added to `ScenarioPlant` since AB.1 → not surfaced in Studio's per-node badges.

`trainer_timeline.py::_scenario_to_timeline` covers even fewer (6 of the original 9). Same bit-rot shape.

#### Where it bites in the Sasquatch example

Post-v11.6.0, sasquatch_pr's auto-scenario emits the full plant surface — inbound_cap_breach (AB.1), two_template_chain + chain_parent_disagreement (AB.2 from the `CustomerFeeAccrual → InternalTransferCycle` chain), xor_variant_missed_firing + overlap (AB.3 from the spec_example `SettlementTimingCycle` chain, and from sasquatch_pr's MerchantSettlementCycle 2-group setup), fan_in_chain trio (AB.4 from `MerchantSettlementCycle → MerchantWeeklyPayoutBatch`), and multi_xor_missed + overlap (AB.6 from MerchantSettlementCycle's 4-children XOR group).

An integrator opening Studio's diagram view against sasquatch_pr sees per-node plant-count badges for drift / overdraft / limit_breach / stuck_pending / stuck_unbundled / supersession / failed / transfer_template / inv_fanout — but ZERO badges for the 11 newer plant kinds. The chrome silently undercounts; integrators may incorrectly read "no plants here" on nodes that actually carry AB.x plant rows.

Reproduction: open Studio against sasquatch_pr, inspect the trainer chrome's per-node badges, cross-reference against `default_scenario_for(sasquatch_pr).scenario` — the badge surface is missing the plant kinds the scenario actually carries.

#### Proposed fix

Extend `plants_per_node` to iterate the missing plant tuples. For chain-shaped plants (AB.2 chain_parent_disagreement, AB.4 fan-in trio, AB.6 multi_xor pair), pick a per-plant binding to a topology node:

- AB.2 chain_parent_disagreement → chain-child template node (the disagreement is OBSERVED on the child)
- AB.4 fan-in trio → child template node (the parent_count check is keyed on the child)
- AB.6 multi_xor pair → chain-parent rail or template node (the XOR violation is observed AT the parent firing)

Similar update for `_scenario_to_timeline` in `trainer_timeline.py`. Both functions follow the same iteration pattern; the change is mechanical once the per-plant binding is decided.

Update the `PlantKind` enum / typedef stub at `trainer.py:38` so it enumerates every supported kind authoritatively (currently it's a `str` alias with a comment listing 9 kinds — rotted).

#### Tradeoffs / open questions

- **Test coverage** — add unit tests per new plant kind showing the expected per-node count. Mirrors existing `plants_per_node` tests.
- **Studio chrome visual** — the SVG `data-trainer-kinds` attribute needs to support the new kind names. Likely no schema change needed; the attribute is comma-joined strings.
- **Backward compat** — pre-fix Studio sessions don't render the missing badges. Post-fix they will. No yaml change required.

---

### Implementation Gap F: Picker determinism is first-by-name (observation)

**Severity:** observation / known design choice — not actionable upstream unless reframed

#### Problem

Every per-plant-kind picker in `auto_scenario.py` selects the first matching rail by sorted name. For an L2 instance with N rails of a given shape, only 1 gets exercised by the auto-scenario; the other N-1 stay uncovered.

`densify_scenario` replicates the picked rail across days via `days_ago` stride; it doesn't expand to OTHER rails in the kind.

#### Where it bites in the Sasquatch example

Sasquatch_pr has multiple rails declaring `max_unbundled_age` (the bundled-aging-watch surface). The auto-scenario picks ONE for the `StuckUnbundledPlant`; the other N-1 don't surface stuck-unbundled exceptions even though they're equally eligible.

For an integrator running a phase-2-style coverage exercise asking "does every rail × stuck_unbundled cell surface?", the auto-scenario answers "no" for N-1 of N rails. Explicit plants for the remaining N-1 are required.

This is by design per existing pattern, but integrators may not realize the auto-scenario only covers one rail per kind — could be a documentation gap.

#### Proposed fix

Two flavors, either / both:

1. **Documentation** — add a "the auto-scenario picks one rail per plant kind by design" note to the seed.md concept doc. Operator-facing surface; clarifies the limitation.

2. **`coverage_mode` flag** — add a `mode` value (or separate `coverage_scenario_for` entry) that iterates ALL rails per kind, producing one plant per rail. Useful for integrator coverage tests; default behavior stays single-rail per kind for the existing demo-readability use case.

Phase 2 (integrator side) prototyped per-rail explicit plants via a runnable test suite (`phase2_coverage_tests.py`) — it bypasses the picker entirely and INSERTs plant rows directly. Pattern works; could be folded upstream as a `recon-gen audit l2 capability` CLI surface if integrators want it.

#### Tradeoffs / open questions

- **Scope** — coverage-mode auto-scenarios would produce N× more plants per kind, which densifies the dashboard significantly. Could overwhelm the visual surface for large L2 instances; should probably be an opt-in scope, not the default.
- **Locked-seed determinism** — adding a new mode is additive; existing modes stay byte-equivalent.

---

### Implementation Gap G: MultiXor plant emitter writes the chain-parent name into `rail_name` for Template-parent chains

**Severity:** bug (regression introduced when Gap A's picker fix shipped — Template-parent chains became reachable but the plant emitter's `rail_name` assignment didn't account for them)

**Status: RESOLVED — AJ.2 / v11.9.0.** The multi_xor plant emitter resolves a Template child's `rail_name` to the child template's first leg_rail (a real declared Rail) instead of the chain-parent template name (`seed.py:5166`). A template-heavy fixture confirms `unmatched_rail_name = 0`.

#### Problem

Once the MultiXor pickers accept Template-parent chains (Gap A fix), the MultiXorOverlapPlant / MultiXorMissedPlant emitters fire against those chains — but the emitter sets the planted row's `rail_name` to the chain-PARENT's name. For Rail-parent chains (the only shape the fixtures exercised pre-fix) the parent name IS a valid rail name, so the bug was invisible. For Template-parent chains the parent name is a TransferTemplate name, which matches no declared Rail → the planted row surfaces on the `unmatched_rail_name` (rail-conformance) exception as a false positive.

#### Where it bites in the Sasquatch example

After the Gap A picker fix, sasquatch_pr's `MerchantSettlementCycle` (a Template) becomes a valid MultiXor plant target — it's the parent of the 4-children XOR-and-fan-in group (`MerchantPayoutACH / Wire / Check / MerchantWeeklyPayoutBatch`). A MultiXorOverlapPlant fired against it would emit a row with `rail_name='MerchantSettlementCycle'` (the template name) → a spurious `unmatched_rail_name` row.

Reproduction recipe: add a Template-parent multi-children chain to spec_example (Gap A's regression fixture, `BulkAccrualSettlement` chained off `MerchantSettlementCycle`, is the natural candidate), run `data apply --execute`, query the `unmatched_rail_name` dataset → the chain-parent template name appears as a non-conformant posting on a healthy seed.

#### Proposed fix

The MultiXor plant emitters should set `rail_name` to an actual leg_rail of the FIRED child template, never the chain-parent's name. When the chain parent is a Template, guard against the template name leaking into the `rail_name` column. Add a Template-parent variant to the plant-emitter unit tests — the fixtures preferred Rail parents, so this path is untested.

#### Tradeoffs / open questions

- Low blast radius (one spurious row per affected plant per seed) but it false-positives the rail-conformance check — the highest-signal L1 invariant ("a posting matching no rail is always wrong"). A false positive there is disproportionately corrosive to operator trust.
- Pairs naturally with Gap A's fix — same code-path family, same "Template parents weren't exercised" root cause.

---

### Implementation Gap H: scenario/plant emitters fire chain-parent rails standalone without a chain child → false `multi_xor_violation` (and `chain_orphans`) — sibling of Gap I

**Severity:** bug (false-positive on a runtime exception matview; plant scaffolding contaminates the XOR-cardinality check). Note: this gap was *originally filed* as "baseline template-firing path bypasses the XOR child-pick" — that hypothesis is **disproven** by the empirical re-diagnosis below; the residual is plant-side, not baseline.

**Status: PARTIALLY RESOLVED — AJ.3 / v11.9.0.** The seed-side fix landed: `_emit_plant_chain_completion` (`seed.py:5258`) emits the XOR/fan-in child for a plant firing that is also a chain parent (matview stays production-honest). **But it was wired into only 3 of 5 plant emitters** — the XOR-variant missed/overlap and limit-breach plants — and NOT the two broad-mode coverage helpers `_emit_transfer_template_rows` (`seed.py:5686`) / `_emit_rail_firing_rows` (`seed.py:5882`). On a template-heavy fixture those two still fire chain-parent legs without a child, so childless `multi_xor_violation` rows persist (and bleed into `chain_orphans`). **Remaining fix: extend the same `_emit_plant_chain_completion` call to those two emitters** — mechanical, identical to the 3 that already have it. Keep the baseline `childless=0` guard.

#### Problem (re-diagnosed)

The baseline emitter (`tr-base-tmpl-*`) composes correctly after AG.1/AG.2: every baseline multi-children-chain parent firing gets exactly one XOR/fan-in child (`childless=0`). The original repro keyed off a `tr-rail%` prefix that predates the AG.1 seed refactor, which is why it no longer reproduces against the baseline path.

The real residual lives in the **scenario / broad-mode plant helpers**, not the baseline:

- `_emit_rail_firing_rows` (`seed.py:5882`, `tr-rail-*`)
- `_emit_transfer_template_rows` (`seed.py:5686`, `tr-tt-*`)

When the rail or template a plant fires *happens to be a multi-children chain parent*, these helpers emit the parent leg but **do not emit the chain child** — they only set `transfer_parent_id` when the plant explicitly carries one. The `multi_xor_violation` matview then reads the parent as a "missed" XOR violation, and the same firing would surface in `chain_orphans` too. This makes it the **sibling of Gap I**: a runtime exception check that can't distinguish a plant's incidental chain-parent firing from a genuinely-missed child.

Two observations narrow the fix:

- **Density-invariant** — `--seed-density 1.0` vs `5.0` produce identical childless counts. Not a scale effect.
- **Not a window-edge / settlement-lag effect** — the healthy chain children are same-day (lag 0); the apparent clustering near the window edge just reflects where the plant helpers emit, not truncation.

#### Where it bites in the Sasquatch example

A fixture that reuses a rail or template as both a standalone-fired entity AND a multi-children chain-parent leg (e.g. `MerchantSettlementCycle`'s `MerchantCardSale` leg) trips this. The baseline (`tr-base-*`) firings of that chain parent are clean; the *plant* firings of the same rail/template (`tr-rail-*` / `tr-tt-*`) surface as `multi_xor_violation` missed-firings on a healthy seed. The count scales with how many rails the fixture reuses as chain-parent template legs, which is why single-purpose fixtures barely show it (they surface only the deliberate `tr-mxor-*` XOR-plant rows, which are correct-by-design).

Reproduction recipe: fresh `data apply --execute`, then break the childless rows down by emit-path —
`SELECT parent_transfer_id, parent_rail_or_template_name FROM <prefix>_multi_xor_violation WHERE child_count = 0`.
The `tr-base-*` baseline firings are absent (they compose); the surviving childless parents are `tr-rail-*` / `tr-tt-*` plants (incidental) plus `tr-mxor-*` plants (deliberate).

#### Proposed fix — SEED-SIDE (not a dataset filter)

The fix belongs in the plant emitters, **not** in the exception surfaces. `multi_xor_violation` and `chain_orphans` run against `<prefix>_transactions` — i.e. real customer ETL data in production, where there is no `tr-base-*`/plant prefix and a chain-parent firing with no child is a **genuine** violation. (The `origin` column is posting-origin — `InternalInitiated` / `ExternalForcePosted` / `ExternalInitiated` — and carries no plant-vs-baseline marker; the only such signal is the demo-only `tr-base-*` transfer_id prefix.) Filtering those datasets on origin/prefix would be demo-only logic leaking into a production-correct invariant: it would no-op on real data, or worse, train operators that the check has demo-shaped escape hatches. The childless rows are not really false positives — the plant helpers are genuinely firing a chain-parent leg with no child, which is a real (if unintended) violation. So the demo seed must stop manufacturing them.

In the broad-mode/scenario plant helpers, when the picked target is a multi-children chain parent, route the firing through the existing baseline XOR child-pick so the planted firing carries its child (preferred — preserves the ability to plant *other* invariants, e.g. stuck_pending/drift, on a rail that happens to be a chain parent). Skipping chain-parent targets entirely is the simpler fallback where a picker never needs them.

Keep the structural guard regardless: assert that baseline chain-parent firings stay `childless=0`, locking in what AG.1/AG.2 fixed.

#### Tradeoffs / open questions

- Distinct from Gap I: N:1 is a real structural property of the L2, so making `chain_orphans` fan_in-aware is *production-correct* and stays a dataset fix. Gap H's residual is demo-seed contamination of a production-correct invariant, so it stays a seed fix. They are siblings in symptom (a chain-parent firing reads as missing a child) but the correct fix layer differs.
- The original "baseline paths don't compose" framing was a measurement artifact (stale `tr-rail%` repro predating the AG.1 refactor); the original instinct ("route the firing through the XOR child-pick") was right about the *mechanism* but wrong about the *layer* — it applies to the plant helpers, not the baseline, which already composes. Retain the baseline `childless=0` regression guard regardless.

---

### Implementation Gap I: L2FT `chain_orphans` dataset isn't fan_in-aware — over-counts N:1 chains as orphans

**Severity:** bug (false-positive on a runtime exception dataset; pre-existing, predates the Gap A–F wave)

**Status: RESOLVED — AJ.4 / v11.9.0.** `build_exc_chain_orphans_dataset` CASE-guards fan_in edges: for `fan_in = 1` the orphan count is parent firings absent from the `_transfer_parents` anti-join (genuine "cycle never batched"), not the naive `parent − child` subtraction (`l2_flow_tracing/datasets.py:1136`). Production-correct. On a template-heavy fixture the structural false-positives collapse; the only residual is (a) Gap H's plant bleed-through — clears once Gap H's two emitters are completed — and (b) a small number of window-edge baseline cycles posted past the snapshot anchor (legitimately not-yet-batched). The dataset fix itself is complete.

#### Problem

The L2FT `chain_orphans` dataset (`apps/l2_flow_tracing/datasets.py::build_exc_chain_orphans_dataset`, line 1105) computes `orphan_count = GREATEST(parent_firing_count - child_firing_count, 0)` for `Required` (singleton-children) chains. A `fan_in: true` chain IS singleton-children (one child = the batch template), so it's labeled Required and gets the naive subtraction. But fan-in is N:1 — N parent firings converge on far fewer shared child Transfers — so `parent_count - child_count` is large and positive for perfectly healthy fan-in activity. Every fan-in chain reads as a pile of orphans on the L2FT exceptions sheet.

The `<prefix>_fan_in_disagreement` matview (the N:1 fan-in runtime check) IS fan_in-aware on the child side and correctly surfaces only genuine violations. But the L2FT `chain_orphans` DATASET predates fan-in awareness and still does the naive 1:1 subtraction.

#### Where it bites in the Sasquatch example

sasquatch_pr's `MerchantSettlementCycle → MerchantWeeklyPayoutBatch` is a fan-in chain (N merchant cycles converge on a weekly batch). The chain_orphans dataset would compute `(# merchant cycles) - (# weekly batches)` as the orphan count — a large false positive proportional to the batching ratio, on a healthy seed.

Reproduction recipe: fresh `data apply --execute`, open the L2FT exceptions sheet (or query the chain_orphans dataset SQL), and observe the fan-in chain showing orphan_count ≈ (parent firings − batch Transfers) despite every parent being correctly batched.

#### Proposed fix (AB.4.8)

For `fan_in: true` chains, the chain_orphans dataset should compute PARENT-side participation correctly: count parent firings whose `transfer_id` does NOT appear in any child's `<prefix>_transfer_parents` set (the genuine "cycle closed but never assigned to a batch" orphan), rather than the naive `parent_count - child_count`. The `transfer_parents` matview already derives the multi-parent set; the dataset just consumes it for fan_in chains.

Simpler alternative: skip fan_in chain children entirely in the chain_orphans dataset and let `fan_in_disagreement` own all fan-in cardinality detection — but that loses the parent-side "cycle not in any batch" orphan, so the precise computation is preferable.

#### Tradeoffs / open questions

- Same architectural move as the multi_xor matview skipping fan_in entries (cardinality is `fan_in_disagreement`'s job) — applied to the L2FT chain_orphans dataset.
- This is the largest residual chain-orphan noise chunk for any fan-in-heavy L2 once the Gap B template-as-child false positives are cleared.

---

### Implementation Gap J: `firings_typical_per_period` (E8) can't scale an atomic multi-leg flow modeled as separate 1-leg rails in a TransferTemplate

**Severity:** gap (E8 is unusable — produces false drift — for a common, legitimate modeling pattern: a balanced multi-leg transfer whose legs are separate 1-leg rails joined by a TransferTemplate).

**Status:** surfaced applying E8 to a template-heavy L2 (most flows modeled as templates).

#### Problem

The baseline seed has two firing paths:

- **Per-rail loop** (`seed.py` ~line 1309, `for rail in sorted(instance.rails …)`): fires every rail independently, reading the count from `_pick_firings_count(rail, …)` — i.e. **the rail's** `firings_typical_per_period`. No skip for rails that are template legs (only the operator `skip_rails` / `only_rails` filters apply).
- **Template-firing loop** (`seed.py` ~line 2890+, the AG.1 helper): fires a template **as a unit** — all leg_rails together, one shared transfer_id, balanced — reading the count from `_pick_firings_count(template, …)`. But it runs **only for templates that appear as a Chain `parent`** (`parent_template_names` is derived from `instance.chains`).

So for an atomic multi-leg flow modeled as two (or more) **1-leg rails** joined into one TransferTemplate with `expected_net = 0` (legs must net to zero), the **only** firing path is the per-rail loop, which fires each leg **independently with its own count**. Consequences of declaring E8:

- Declare E8 on **one** leg → that leg's count diverges from its siblings → the template's shared Transfer is imbalanced → false `drift` / `ledger_drift`.
- Declare the **same** E8 band on **all** legs → still diverges: each rail samples its own per-period count from its own RNG stream (`base_seed ^ crc32(rail.name)`), so the legs land on different counts; and a leg shared with other flows (e.g. a sweep-side leg) also receives paired emissions from those flows, pushing it past its own band.

Net: there is **no way to set a coupled, scaled firing count** for such a template. The legs only stay balanced at the per-kind heuristic (which happens to give matched legs the same count) — i.e. you can't scale the flow at all without breaking it.

This is not an exotic shape. Modeling an atomic transfer as separate 1-leg rails (rather than one `TwoLegRail`) is the right choice whenever the legs need distinct rail identities — different accounts/roles, different bundling (one leg consumed by an aggregator, the other not), or different aging/`max_unbundled_age` treatment. E8 silently doesn't work for any of them.

#### Where it bites in the Sasquatch example

A template like `CardLoad` whose legs are two 1-leg rails — `CardLoadCardholderCredit` (posts to the cardholder account, unbundled) and `CardLoadSweepDebit` (posts to a sweep account, consumed by an EOD aggregator) — joined with `expected_net = 0`. Setting `firings_typical_per_period` on either leg (or both) drifts the pair; the flow can only run at the heuristic default. The single-leg-into-aggregator flows (a sale rail whose counter-leg is a pool aggregator) and 2-leg *standalone* rails (one `TwoLegRail` emitting both legs atomically per firing) both scale fine — it's specifically the **multi-1-leg-rail template** that has no lever.

Reproduction recipe: take any TransferTemplate whose `leg_rails` are ≥2 SingleLegRails with `expected_net = 0`; add `firings_typical_per_period: [N, M]` to one leg; `data apply --execute` + `data refresh --execute`; observe the leg's transfer count diverge from its sibling and new `ledger_drift` rows appear on a previously-clean baseline.

#### Proposed fix

Let `firings_typical_per_period` on a **TransferTemplate** drive a coupled unit-firing count for **any** template that declares it — not just chain-parent templates. The unit-firing machinery already exists (the AG.1 template-firing helper emits all leg_rails together, balanced, from `_pick_firings_count(template, …)`); the fix is to (a) run it for every template carrying E8, and (b) have the per-rail loop **skip leg_rails of any template that fires as a unit**, so those legs aren't also emitted independently (which would both double-count and re-introduce the divergence). Validator W1 should then also accept `firings_typical_per_period` on a TransferTemplate (today E8 is documented on rails).

Smaller alternative if template-level E8 is out of scope: have the per-rail loop, when emitting a rail that is a leg of an `expected_net = 0` template, derive its count from a single per-template draw shared across that template's legs (so sibling legs stay matched even when each is declared independently). The template-level knob is cleaner and also lets the operator express "this whole flow fires N times per period" directly.

#### Tradeoffs / open questions

- The two-path baseline (per-rail loop + chain-parent template loop) already double-emits chain-parent template legs today (legs fire in the per-rail loop *and* as part of the unit firing). Fixing E8 via the "skip leg_rails of unit-firing templates" route would also tidy that up.
- Doesn't affect single-leg→aggregator flows or 2-leg standalone rails — those scale correctly via rail-level E8. Scope is precisely the multi-1-leg-rail balanced template.

---

### Recommended filing order for the implementation gaps

Original wave (A–F) — all landed in Phase AG (v11.7.0):

1. **Gap B (transfer_parent_id missing)** — ✅ RESOLVED (AG.1). Broadest blast radius; cleared false-positive chain orphans.
2. **Gap C (baseline multi-XOR not enforced)** — ✅ RESOLVED (AG.2); the baseline path composes. The remaining childless `multi_xor_violation` rows are plant-origin, not baseline — see Gap H (re-diagnosed).
3. **Gap A (picker Rail-parent restriction)** — ✅ RESOLVED (AG.3); see Gap G for the regression it surfaced.
4. **Gap D (classifier substring + over-match)** — ✅ RESOLVED minimal (AG.4 — PAYROLL_BATCH + overmatch guard); vocabulary breadth deferred (Enhancement 8 is the universal fix).
5. **Gap E (trainer modules bit-rot)** — ✅ RESOLVED (AG.5 — per-node badges; timeline left toggle-scoped by design).
6. **Gap F (picker first-by-name)** — ✅ ADDRESSED (AG.6 — docs note).

Second wave (G–I) — surfaced by post-AG integration re-testing; not yet filed upstream:

7. **Gap G (MultiXor plant rail_name leak)** — regression from Gap A; file with Gap A's family; Template-parent plant-emitter test.
8. **Gap H (plant emitters fire chain-parent rails standalone without a child)** — re-diagnosed: NOT a baseline compose gap (baseline composes after AG.1/AG.2); the residual is demo-seed contamination of a production-correct invariant. **Seed-side fix** (route plant chain-parent firings through the XOR child-pick) — NOT a dataset filter, since `multi_xor_violation`/`chain_orphans` run against real customer data where a childless chain-parent firing is genuine. Sibling of Gap I in symptom but the fix layer differs (I is a production-correct dataset fix). Keep a baseline `childless=0` guard.
9. **Gap I (L2FT chain_orphans not fan_in-aware)** — pre-existing; the AB.4.8 dashboard-wiring step. Largest residual chain-orphan noise once Gap B cleared the template-as-child false positives.

G and H share a root cause with the original wave: Template-parent / template-heavy L2 shapes weren't in the fixture set, so the AG fixes were validated against Rail-parent shapes and the Template-parent composition slipped through. A single "template-heavy L2" regression fixture (one template that is a chain parent, baseline-fired, AND a MultiXor plant target) would catch both G and H — worth adding upstream as a structural guard against this whole class. I is independent (a pre-AG dataset that never learned about fan-in); it's bundled here because it's the dominant residual now visible.
