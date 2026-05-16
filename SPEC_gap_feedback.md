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

## How these proposals interrelate

Enhancements 2 (N:1 fan-in chains) and 3 (template-as-chain-child) compose naturally: if 3 lands first, two-template chains become expressible; if 2 then lands, the N:1 batching pattern becomes structurally enforceable. Enhancement 1 is independent — it tightens up template's `leg_rails` semantics for the per-mode-XOR case, separate from the chain plumbing. Enhancement 4 is entirely independent of the chain/template plumbing — it only touches the LimitSchedule / OutboundFlow surface.

A staged adoption order that minimizes churn:

1. **Enhancement 4** first (inbound caps) — adds one optional field (`direction`) + one new theorem (`InboundFlow`). No interaction with chains, templates, or aggregating rails. Easy first win.
2. **Enhancement 3** (template-as-chain-child) — small, well-scoped, no schema-level addition. Just clarification + new validator rule.
3. **Enhancement 1** (multi-Variable + leg_rails XOR) — adds one field (`leg_rail_xor_groups`) and relaxes one constraint (C1). Self-contained.
4. **Enhancement 2** last (N:1 fan-in) — bigger conceptual change. Worth doing only if the integrator population has multiple N:1 patterns to justify the chain-validator complexity.
