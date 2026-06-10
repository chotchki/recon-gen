## TL;DR

A **7-day** audit report spends **~51 of ~52 seconds in two pure-Python hash passes**, regardless of the reporting window. Two independent fixes, in priority order:

1. **Delete the matview-evidence pass (~31.5s, ~60%).** It hashes 9 matviews for a *rendered appendix table only* — it is **not** part of the verifiable `ProvenanceFingerprint` and `audit verify` never recomputes it. Matviews are deterministic functions of the base tables + L2 + code (all already fingerprinted), so it's redundant by construction. Drop it (or render `COUNT(*)` only).
2. **Speed up the one authoritative pass — the base-table fingerprint** (`transactions` + `daily_balances`, the real `entry`-keyed source tables). Full-dataset coverage there is *correct and intended* (bind to all source rows, not a 7-day slice). Replace the `fetchall()` + per-cell Python loop + tuple `sort()` with: per-row `sha256` **in the engine** → `ORDER BY rh` in-engine → stream digests → fold via `h.update()` (byte-identical to hashing the concatenation; Merkle–Damgård). **~19.7s → ~1.3s**, full SHA256 strength, dodges Oracle's `LISTAGG` cap.

End-to-end: **~52s → ~2.3s**, with the only full-table hashing over the two real source tables. The provenance scheme isn't frozen, so #2 is a free format rev.

---

## Evidence (measured, per-phase wall-clock)

`audit apply --execute`, default 7-day period, demo on DuckDB, broken down by phase:

| phase | wall-clock |
|---|---:|
| all 9 report queries + daily-statement walks, combined | **~0.5s** |
| `compute_provenance` — hashes `transactions` + `daily_balances` | **19.7s** |
| `_query_matview_evidence` — hashes 9 matviews | **31.5s** |
| reportlab render | sub-second (tables are tiny: drift=5, overdraft=1350) |
| **total** | **~52s** |

So **~98% of the run is two hash passes**, and neither is scoped by `--period` — both scan the whole dataset every time. (Aside: the `--execute` path also hard-crashes at the signing step if the optional `pyhanko` dep is missing — separate venv issue, not perf.)

## Root cause

Both helpers in `common/provenance.py` materialize the full table in Python and hash cell-by-cell:

- **`hash_table_rows`** (provenance, authoritative fingerprint): `SELECT * ... WHERE entry <= hwm` → `fetchall()` → per-row `b"\x1f".join(canonical_value(c) for c in row)` + `sha256.update`. On `transactions` that's **3.27M rows × ~20 cols ≈ 65M `canonical_value()` calls** in Python. The function's own comment already flags *"could OOM at very large scale — switch to fetchmany."*

- **`hash_matview_rows`** (sidecar matview evidence): same, **plus** it builds a list of 3.27M canonical tuples and calls `canonical_rows.sort()` — **a Python sort of 3.27M tuples** — before hashing. The docstring asserts *"Matviews are bounded (~tens to hundreds of rows in practice)"*; that premise is simply false for `current_transactions` (3.27M), `current_daily_balances`, and `stuck_unbundled` (122K). `current_transactions` is essentially a second copy of the `transactions` base table, so the dataset gets hashed **twice**.

The Python sort and the per-cell `canonical_value` ladder are the hot loops; the DB query itself is fast.

## The 31.5s matview pass is redundant — delete it, don't optimize it

`_query_matview_evidence` is **not part of the provenance guarantee**. The authoritative `ProvenanceFingerprint` is `{transactions_hwm/sha, balances_hwm/sha, l2_yaml_sha, code_identity}` — base tables + external inputs only. The provenance module's own docstring says it: *"Hash the base tables + external inputs, NOT matviews."* `audit verify` recomputes exactly those four and **never touches a matview**. The matview SHAs flow only into a rendered appendix table.

So the 31.5s buys an informational sidecar that nothing verifies against — and it's redundant by construction: every matview is a deterministic function of (base tables + L2 + recon-gen code), all three already bound in the fingerprint. `current_transactions` (3.27M rows) is ≈ a second copy of `transactions`; hashing it again proves nothing the base-table hash + code identity don't already cover. (`config_kv` — the `_kv` table — is likewise derived: it's built from the L2 YAML at schema-apply, so it's bound by `l2_yaml_sha`, and correctly absent from the row-data hash.)

**Recommendation: drop the matview content-hashing outright** (≈60% of total runtime, for zero loss of binding strength). If the appendix should still show matviews as evidence, render **`COUNT(*)` only** (cheap) or state that consistency is checked by recompute-and-compare at verify time — a stored content hash only proves the matview hasn't changed since generation, which the PDF signature already covers. That leaves **one** hash pass: the authoritative base-table fingerprint, which the streamed scheme below makes ~1.3s.

## Why the current combine doesn't scale (and what to do instead)

The goal is one SHA256 over **all** rows, but with two constraints: (a) don't pull full rows into Python (that per-cell loop is the current bottleneck), and (b) don't concatenate in SQL on Oracle — `LISTAGG` has a hard ~32 KB result cap (`ORA-01489`) and cannot build a string over millions of row-hashes. `sha256(string_agg(per_row_sha256 ORDER BY …))` satisfies (a) but fails (b).

### Recommended: streamed two-level SHA256 — **measured 1.32s on 3.27M rows**

Do the expensive part (canonicalize + per-row SHA256) **in the engine**, columnar, then stream only the fixed-size digests to Python and fold them into one hash incrementally. Because SHA256 is Merkle–Damgård (block-based), feeding digests via repeated `h.update()` is **byte-identical** to hashing their concatenation — so no giant string is built, and nothing is concatenated in SQL, so **Oracle's `LISTAGG` cap never applies**. This is a genuine SHA256 over the row-set: **full collision resistance, no caveats.**

Per dialect (parity across dialects NOT required — a report is generated and verified against the *same* DB, so each engine may use its own native SQL + its own `CAST … AS VARCHAR` rendering; the only requirement is determinism for a given DB):

```sql
-- DuckDB (measured); Postgres/Oracle identical shape, swap the hash fn + concat
SELECT rh
FROM  (SELECT sha256(concat_ws(chr(31),
                 coalesce(CAST("account_id" AS VARCHAR), chr(0)),
                 /* … all columns, sorted by lower(name); chr(0) NULL-sentinel … */
              )) AS rh
       FROM <prefixed_table>          /* + WHERE entry <= :hwm for base tables */)
ORDER BY rh;                          -- deterministic order, in-engine sort (NOT listagg)
```
```python
h = hashlib.sha256()
while (rows := cur.fetchmany(50_000)):
    for (rh,) in rows:
        h.update(rh.encode("ascii"))   # == sha256 of the sorted concatenation
fingerprint = h.hexdigest()
```

- **`ORDER BY rh`** gives determinism without needing a natural key (matviews have none). Ties only occur on byte-identical rows, which contribute identical bytes either way ⇒ tie-order is irrelevant ⇒ result is invariant to scan order.
- **Verified:** 1.32s, deterministic across runs, and reproduces `sha256(string_agg(rh ORDER BY rh))` **exactly** (`71b63340…980a632b`) — i.e. the Python fold and the all-SQL concat are provably the same hash.
- **Per-dialect hash fn:** DuckDB `sha256` (built-in) ✓; Oracle `RAWTOHEX(STANDARD_HASH(canon,'SHA256'))` (built-in) ✓; **Postgres has no built-in SHA256** — `digest()` needs `pgcrypto`. Given the "no PG extensions" stack constraint, decide: require `pgcrypto`, or use built-in `md5()` on the PG path (128-bit per-row; fine here since the combine is a real hash-tree and the PDF is also pyHanko-signed). Parity-not-required makes the per-dialect choice free.
- **Cost trade vs the alternative below:** streams 3.27M fixed-size digests through Python (~3.27M cheap `update()` calls). Still ~20× faster than today and bounded-memory via `fetchmany`.
- **Why the per-row `sha256` (it adds no strength — the final fold is already a full SHA256 over the rows):** it's a *compressor*. It shrinks each row to 32 bytes before anything crosses the DB→Python boundary. Measured on `current_transactions`: per-row-hash pulls **209 MB** vs **1.16 GB** for streaming full canon (5.5×). On a local embedded DuckDB that's only ~0.25s (1.43s vs 1.68s) — but **pg/Oracle are client-server, so that 1.16 GB goes over a socket** and the gap widens. It also gives a cheap fixed-width sort key: keyless matviews without it must `ORDER BY` the long canon string (**2.53s, ~80% slower**). Note the compression *requires* a crypto hash — if the per-row value is the only form of the row reaching the digest, a non-crypto hash would let two distinct rows collide.
  - **Simpler variant for base tables only:** they have an `entry` PK, so `ORDER BY entry` + stream full canon + fold (no per-row hash) is a valid one-level "sha256 of the sorted rows" — cleaner verify story, at 5.5× transfer. Matviews (no PK) should keep the per-row hash.

### Alternative: additive multiset hash — 0.52s, but checksum-grade only

If the last ~0.8s ever matters (it shouldn't for an audit report), the per-row SHA256 can instead be reduced to 4×64-bit lanes and **`SUM`med** across rows (commutative ⇒ no sort, single scalar out, no Python streaming). DuckDB POC measured **0.52s**, deterministic, shuffle-invariant:

```sql
SELECT count(*),
       sum((('0x'||substr(rh, 1,16))::UBIGINT)::HUGEINT) AS l0,  -- '0x..'::HUGEINT
       sum((('0x'||substr(rh,17,16))::UBIGINT)::HUGEINT) AS l1,  -- rejects 0x; cast
       sum((('0x'||substr(rh,33,16))::UBIGINT)::HUGEINT) AS l2,  -- UBIGINT then widen
       sum((('0x'||substr(rh,49,16))::UBIGINT)::HUGEINT) AS l3   -- so SUM can't wrap
FROM  (SELECT sha256(<canon>) AS rh FROM <prefixed_table>);
-- fold: sha256(count || l0 || l1 || l2 || l3)
```
**Security caveat — read before choosing this.** This is the additive *AdHash* multiset hash (Bellare–Micciancio 1997), **not** equivalent to SHA256 over the table. Order-independence is bought with linearity, and collision-resistance then reduces to a **subset-sum / lattice** problem attackable well below 256 bits (Wagner's generalized birthday); provable AdHash needs a ~1600-bit modulus. It is near-certain against accidental drift / casual tampering (SHA256 avalanche) but **weak against an attacker with DB write access** crafting a sum-colliding multiset. Use `SUM` not `bit_xor` (XOR cancels duplicate rows). The streamed approach above avoids all of this for ~0.8s more — prefer it unless you have a specific reason.

## Scope & impact

- **Dialects:** pg / ora / duck. (sqlite is dead per the project; no fallback needed.)
- **`_query_matview_evidence` / `hash_matview_rows`: removed** (or downgraded to `COUNT(*)`-only). Not authoritative, not verified — see "delete it, don't optimize it" above. This alone is the ~60% win.
- **`hash_table_rows`** (the authoritative base-table fingerprint, `WHERE entry <= hwm`) converts to the streamed pattern over `transactions` + `daily_balances`. `canonical_value()` + the `\x1f/\x1e` Python row-streaming are retired (canonical is now a per-dialect SQL expression; only digests cross into Python).
- **Compat:** changes emitted fingerprint *values* → bump a `provenance_format_version` field, tagged in PDF metadata + appendix. Old PDFs keep their old tag.
- **Verify recipe:** the embedded `verify-provenance.py` becomes "connect to the DB, run this SQL, fold the digests" — simpler and more honest than re-implementing `canonical_value` in pure Python, and exactly what the "DB must exist for verification" stance already assumes.
- **Projected end-to-end:** ~52s → **~2.3s** (queries 0.5s + base-table hash ~1.3s + reportlab ~0.5s; matview evidence dropped). The two real source tables are the only full scans.

## Open decisions for the maintainer
1. **Drop the matview-evidence appendix, or keep it as `COUNT(*)`-only?** Recommend drop (it's non-authoritative and redundant); keep only if the rendered matview row-counts have standalone value to auditors.
2. **Streamed (recommended, full-strength) vs additive (0.52s, checksum-grade)** for the base-table fingerprint. Recommend streamed — no security caveat, and base-table hashing is now the only pass so a couple seconds is irrelevant.
3. **Postgres SHA-256:** require `pgcrypto`, or use built-in `md5()` on the PG path? (parity-not-required permits the latter.)
4. **`provenance_format_version`** bump + appendix/metadata tagging — confirm the field name/placement.
```
```
