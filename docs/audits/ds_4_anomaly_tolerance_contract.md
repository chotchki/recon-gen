# DS.4 — the anomaly tolerance contract (the structured waiver)

**Verdict up front: the anomaly detector is no longer "excluded with written rationale" — it is CHECKED, engine against an exact-rational law, with the ONE thing exact checking can't reach (the engine's float z near a bucket edge) converted into an explicit, tested, two-constant contract.** This document is that waiver: what is proven exactly, what is tolerance-checked, what the tolerance is and why those numbers.

Landed 2026-07-02. Law module: `src/recon_gen/common/spine/anomaly_contract.py`. Sweep: `tests/unit/test_ds4_anomaly_tolerance.py`. Found + fixed one real cross-dialect bug on the way in (§4).

## 1. Why exact set-equality couldn't apply — and where exactness ends

Every other invariant in the DS ledger reduces to integer arithmetic, so the enumeration gate compares violation sets EXACTLY. The anomaly matview (`<prefix>_inv_pair_rolling_anomalies`) divides by a sample stddev — an irrational number — so its z-score column can never be exact-compared. That was the DS.0 rationale for exclusion (`MathKind.PROBABILISTIC`, no residual).

The exclusion was too pessimistic by one square root. The pipeline's layers, in order:

| layer | arithmetic | claim class |
|---|---|---|
| pair legs → daily sums → rolling window sums → transfer counts | integer | EXACT |
| per-pair mean, per-pair sample VARIANCE | rational (ℚ) | EXACT via `Fraction` |
| pair_n, min-n floor arm, stddev=0 guard arm | integer / rational equality | EXACT (§3) |
| z², bucket decision | rational! `ABS(z) < k ⟺ z² < k²` | EXACT on the law side |
| the ENGINE's z / mean / stddev columns | float (binary64) | ε-tolerance (§2) |
| the ENGINE's bucket at a band edge | float comparison vs threshold | adjacent-bucket rule (§2) |

The square root never has to be taken: window sums are integer cents, the mean is a ratio of integers, the sample variance is a sum of squared rationals over `n−1`, and z² = (w − mean)²/variance stays in ℚ end to end. Every bucket threshold test squares away too. So the LAW side (`anomaly_reference`) carries ZERO floating-point error — the only approximation anywhere in the contract is the engine's own storage.

## 2. The two constants — and the ordering that makes them sound

- **`ENGINE_ZSQ_EPSILON` = 1e-9 (relative).** Engine z² / mean / variance must land within `ε·(1 + |law|)` of the exact value. Binary64 error on integer-cent histories is ~1e-15 relative; 1e-9 leaves six orders of headroom while still failing loudly on any formula-level divergence (a wrong divisor, a dropped row, a frame regression each move z² by far more than any rounding can).
- **`BAND_EDGE_DELTA` = 1e-6 (z² space).** When the law z² sits within δ of a bucket threshold², the row is BAND-EDGE: the engine may answer with either adjacent bucket and both answers are correct readings of the same law. The SQL's strict `<` puts an exact-threshold z in the upper bucket; a last-bit rounding on another engine legitimately may not. Interior rows (margin > δ) must match buckets EXACTLY.
- **The soundness ordering: δ ≥ 1000·ε, pinned by `test_delta_dominates_epsilon`.** Any row the engine could conceivably misround across a threshold has error at ε scale — three orders inside δ — so it is ALWAYS law-classified band-edge before the engine gets a vote. A row the law calls interior is one the engine CANNOT flip. That ordering is the whole trick; without it "adjacent bucket accepted" would be a hole, with it it's a theorem about the two constants.

The band-edge witness is constructive, not searched: the arithmetic-progression history `{100, 200, 300}` on gap-spaced days puts its endpoints at EXACTLY z = ∓1 (mean 200, sample stddev 100 — all rational, verified as `zsq == Fraction(1)` with no tolerance). Dialect-stable, deterministic, no float hunting.

## 3. The guard arms are exact — deliberately outside the tolerance

The min-n floor (`pair_n < INV_MIN_HISTORICAL_WINDOWS`) is an integer comparison on both sides. The stddev=0 guard is exact too, which is less obvious: float stddev is zero iff every window in the pair is equal (equal integers survive AVG exactly, deviations of integer cents cannot underflow to zero), which is exactly when the ℚ variance is zero. So the contract asserts LITERAL zeros — engine z = 0, bucket = '0-1 sigma' — on guard rows, no ε. The sweep covers below-floor (n=2), the single-window COALESCE lane (n=1) and the flat pair (stddev 0 at n ≥ floor, proving the arm split).

## 4. The bug the contract caught on arrival: DuckDB quantized the whole z pipeline

The matview cast `pair_mean` / `pair_stddev` through bare `NUMERIC`. On PG that's arbitrary precision; on DuckDB **bare `NUMERIC` means `DECIMAL(18,3)`** — mean, stddev, and therefore z_score and the bucket CASE (which read those columns) were silently quantized to THREE DECIMALS on the local-default engine only. Same SQL text, divergent semantics across dialects, invisible to every existing test because nothing compared the engine against an exact reference.

The sweep landed red-first at ε=1e-9 (`pop_mean 166667/1000 vs law 500/3` — the truncation in the flesh), then the emitter moved both casts to `DOUBLE PRECISION`. That's the honest convergence point, not a DuckDB-only arm: PG's arbitrary-precision NUMERIC and DuckDB's DECIMAL(18,3) were never the same behavior; binary64 on all three dialects is (~1e-15 relative everywhere, inside ε on every lane). Precision went UP on DuckDB (3 decimals → ~15 significant digits) and the z formula is unchanged, so bucket assignments only move for rows within ~1e-5 of a threshold — the planted anomaly scenarios are engineered far from edges (spike z ≈ 4.36 vs threshold 4) and the semantic locks pin buckets only, so no lock movement is expected; the chain verifies.

## 5. The claim-ledger row (what DS.4 actually waives)

| layer | claim |
|---|---|
| window sums, transfer counts, pair_n | **PROVEN-on-D** — exact equality on the swept domain |
| guard arms (floor, stddev=0) | **PROVEN-on-D** — exact on both sides (§3) |
| mean, variance, z² | **TESTED-ε** — within 1e-9 relative of the exact ℚ value |
| bucket, ε-interior rows | **PROVEN-on-D given ε** — exact match, engine provably can't flip (§2) |
| bucket, band-edge rows (z² within 1e-6 of a threshold²) | **WAIVED to adjacent-bucket** — either flanking bucket accepted, and this is the ENTIRE waiver |

D here is the packed pair domain in the sweep: all five buckets interior, an exact-threshold row, both guard arms, negative z, multi-transfer days, a two-sender-leg transfer (the join's cross-product lane), dense two-day windows, supersession and status filtering. Pair-keyed packing shares one database soundly — every matview aggregate partitions per pair (the DS.3.4 packing-contract argument).

What stays out of scope, on purpose: nothing probabilistic is being asserted ABOUT the z-scores (no distributional claim, no false-positive-rate claim — the detector's statistical merit is a design question, not a law). The contract claims only that the SQL computes the declared formula. Per-dialect replay of the tolerance cells on PG + Oracle rides the DS.6 boundary lane.

## 6. Placement decisions

- The law lives in `common/spine/anomaly_contract.py`, NOT `residuals.py` — the residual kit is division-free by construction (the DST.1 z3 lane symbolically executes it; the op-whitelist AST lint enforces it) and z² needs division. `MathKind.PROBABILISTIC` keeps its "no residual" marker; the enum comment names this module as the owner.
- The sweep is its own module, not a 14th enumeration domain — the gate's comparator is exact violation-set equality and forcing a tolerance comparator through it would distort the harness for one detector.
- `INV_MIN_HISTORICAL_WINDOWS` went public (was underscore-private) — it's a cross-module contract parameter now, same precedent as `MONEY_TRAIL_DEPTH_CAP`.
