# DS.1 — KAT derivations (hand-derived, per written law)

2026-07-02. Every expected value below was derived by arithmetic shown here, from the written laws (`src/recon_gen/docs/L1_Invariants.md` + the signed DS.0 kit) — never read out of implementation code, so a residual bug and a KAT bug can't cancel. Vectors live in `tests/data/kats/*.json`; the runner is `tests/unit/test_ds1_residual_kats.py`. The drift (M1-M3), xor (C1-C3), stuck_pending (T1-T3) and money_trail (D1-D3) derivations are in [`ds_0_invariant_parity.md`](ds_0_invariant_parity.md) §4.3 and are not repeated — this doc derives the vectors DS.1 added, and records the three authoring calls that need eyes.

Reading the money tables: one row per day; calculated = running Σ of Posted legs; residual = reported − calculated. Emits and legs are never added together (the emit is the CLAIM, the legs are the EVIDENCE). All amounts integer cents.

## Money family

**ledger_drift** — law (signed DS.0 shape): parent effective balance = Σ child effective balances + parent's own direct Posted postings through day end.

| vec | parent reported | children (effective) | parent direct legs | calculated | residual |
|---|---|---|---|---|---|
| L1 | 700 (emitted) | 300 + 400 | — | 700 | 0 |
| L2 | 500 (emitted) | 300 + 400 | — | 700 | **−200 — parent short of its children** |
| L3 | 500 (emitted) | 300 | +200 Posted | 500 | 0 — **pins the direct-postings term the handbook prose omits** |

**overdraft** — law: internal account effective balance ≥ 0 on every day, emitted or carried. Residual = min(effective, 0).

| vec | day | reported | residual |
|---|---|---|---|
| O1 | d0 | +100 (emitted) | 0 |
| O2 | d0 | −150 (emitted) | **−150** |
| O3 | d1 | −150 (CARRIED — no emit) | **−150 — a sparse account whose last emit was negative is still overdrawn** |

**expected_eod** — law: where an emitted claim carries an expectation, money equals it. No carry (the expectation binds its emit day).

| vec | reported | expected_eod | residual |
|---|---|---|---|
| E1 | 100 | 100 | 0 |
| E2 | 120 | 100 | **+20** |
| E3 | 120 | none set | no cell |

**limit_breach** — law: per (child account, day, rail, direction), Σ|amount| of Posted legs matching the direction ≤ the L2-resolved cap. Residual = max(0, flow − cap).

| vec | legs | direction | flow | cap | residual |
|---|---|---|---|---|---|
| LB1 | −3000 P, −2000 P | Outbound | 5000 | 10000 | 0 |
| LB2 | −15000 P, −7000 P | Outbound | 22000 | 10000 | **+12000** |
| LB3 | −15000 P, +4000 P | Inbound | 4000 (credits only) | 3000 | **+1000** |
| LB4 | −15000 **Pending**, −2000 P | Outbound | 2000 (Pending doesn't flow) | 10000 | 0 |

## Threshold family

**stuck_unbundled** — law: a Posted leg on a capped rail gets a `bundle_id` before posting + cap; residual = age − cap, strict >.

- U1: posting 2030-01-01T00:00, as_of 2030-02-05T00:00 → age = 35 × 86,400 = 3,024,000s; cap (P31D) = 31 × 86,400 = 2,678,400s → **+345,600**.
- U2: same ages, `bundle_id='b1'` → bundled → no cell.
- U3: age exactly 86,400 = cap → residual 0, NOT stuck (strict >, same boundary rule as T2).

## Cardinality family

**chain_parent** — residual = |distinct claimed parent ids among firing legs| − 1.

- CP1: two Posted legs both claim p-1 → |{p-1}| − 1 = 0.
- CP2: legs claim p-1 and p-2 → |{p-1, p-2}| − 1 = **+1**.
- CP3: Posted claims p-1, **Failed** claims p-2 → the Failed claim is ignored (the written law: "exclude Failed legs — metadata is unreliable on failures") → 0.

**xor_group C4 (existence pin — the DS.3.3 flag)**: a transfer whose only legs are Failed + unknown-status has NO firing-status leg → no cell. This is the default-and-flag composition of the decided status law: all-void transfers are INVISIBLE, not alarmed (today's SQL alarms them with firing_count=0). The DS.3.3 task pins or overturns this explicitly.

**fan_in** — residual = |distinct contributing parents| − expected when set; unset → count − 2 for count < 2, else 0 ('orphan' is the only flaggable case).

- F1: 5 parents, expected 5 → 0. F2: 4 → **−1** (missing). F3: 6 → **+1** (extra).
- F4: 1 parent, expected unset → **−1** (orphan). F5: 3 parents, unset → 0.

**multi_xor** — residual = |distinct fired sibling NAMES| − 1, names counted ONCE regardless of leg count or posting days.

- MX1: one child name fires → 0. MX2: none → **−1** (missed). MX3: two names → **+1** (overlap).
- **MX4 (BORN-DIVERGENT — pins DS.3.3a)**: parent legs at 23:50 d0 + 00:10 d1 (straddling midnight), ONE child name fires → names = {ACH} → **0 under the law**. Today's SQL multiplies by distinct posting days and reads this cell as ('overlap', child_count=2) — the 2,268-cell false-positive class the DS.0 spike characterized.

## Authoring calls needing eyes (operator, at DS.1 review)

1. **ledger_drift children carry forward** — the law sums child EFFECTIVE balances (a quiet child still holds its position), matching the parent side's carry. The signed DS.0 signature says "Σ child_stored" without naming which; effective is the consistent reading. KAT L1-L3 pin it.
2. **xor existence predicate defaulted** (C4 above) — DS.3.3 owns the confirm-or-overturn.
3. **Handbook law-prose gaps fixed in this task**: law 1 gains the carried-day clause, law 2 gains the direct-postings term, law 3 quantifies over the effective (carried) balance — the residuals are now the single home; the prose follows them.
4. **limit_breach direction matches by sign** (Outbound = amount < 0), equivalent to `amount_direction` by the sign↔direction CHECK constraint — one less state column in the law.
