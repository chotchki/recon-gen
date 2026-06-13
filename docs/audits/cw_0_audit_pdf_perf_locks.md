# CW.0 — Audit PDF performance: locked maintainer decisions

**Filed 2026-06-09.** Operator-signed-off the 4 open decisions in
`docs/audits/audit_pdf_perf.md` plus one additional
`pyhanko`-graceful-degrade lock. This doc is the authoritative
reference for the Phase CW implementation; CW.1 through CW.7 cite it.

Companion to the root-cause + fix-design doc
(`docs/audits/audit_pdf_perf.md`) — the design doc owns the *why*
(measurements, Merkle-Damgård proof, per-dialect SQL templates); this
doc owns the *what we picked*. PR-link from each CW.X commit body.

---

## Lock 1 — Drop matview-evidence outright

**Decision.** `_query_matview_evidence` and `hash_matview_rows` are
deleted entirely from the codebase. The audit PDF appendix loses both
the **Matview Evidence** section and the per-matview SHA256 column.

**Rationale.** The matview hash pass burns ~31.5s of the ~52s total
(~60%). It is **not** part of the verifiable `ProvenanceFingerprint`
and `audit verify` never recomputes it. Matviews are deterministic
functions of (base tables + L2 yaml + recon-gen code), all three
already bound in the composite fingerprint — the matview SHAs are
redundant by construction. `current_transactions` (3.27M rows) is
essentially a second copy of `transactions`, so the dataset gets
hashed twice today for zero authoritative gain.

**No `COUNT(*)`-only fallback.** The design doc proposed
`COUNT(*)`-only as a middle ground; operator rejected it. The
appendix section disappears entirely — no "Matview Evidence" header,
no placeholder row, no degraded form. Less surface area to maintain
+ no false-positive "this matview drifted" technical-signal noise.

**Scope.** Provenance-side: delete `hash_matview_rows` from
`common/provenance.py`. CLI-side: delete `_query_matview_evidence`
and `MatviewEvidence` and `_APPENDIX_MATVIEWS` from
`cli/audit/__init__.py`. Renderer-side: delete the matview-evidence
flowable + appendix section from `cli/audit/pdf.py` and
`cli/audit/markdown.py`. Drop the `matview_evidence` parameter from
every renderer signature (it's not optional — it's deleted).

---

## Lock 2 — Streamed (not additive) base-table fingerprint

**Decision.** The authoritative base-table fingerprint
(`transactions` + `daily_balances`) uses the **streamed two-level
SHA-256** approach from the design doc:

1. **In the engine:** per-row SHA-256 over canonicalized columns,
   sorted by `lower(name)` with NULL → `chr(1)` sentinel + per-column
   `coalesce(CAST(col AS VARCHAR), chr(1))`. Result is a stream of
   64-character hex digests.
2. **In Python:** `cur.fetchmany(50_000)` loop +
   `h.update(rh.encode("ascii"))` fold into a single `hashlib.sha256`.
   Bounded memory regardless of row count.

**Order.** Base tables (`transactions`, `daily_balances`) have an
`entry` PK; order by `entry` and stream the per-row hash. The doc's
"keyless `ORDER BY rh`" variant was needed for matviews — since
matview-evidence is deleted (Lock 1), there's no keyless table left.
The `ORDER BY entry` variant is cleaner verify-side (the SQL recipe
in the embedded `verify-provenance.py` references the PK explicitly)
and avoids spending a per-row sort.

**Rejected: additive multiset hash (AdHash).** 0.52s vs 1.32s is
~0.8s saved at the cost of dropping from full SHA-256 to a
subset-sum/lattice-attackable checksum. Audit-grade provenance wins;
0.8s on a single annual report is irrelevant.

**Merkle–Damgård equivalence.** Feeding the per-row hex digests
through `hashlib.sha256().update()` is byte-identical to
`sha256(string_agg(rh ORDER BY entry))`. The design doc proves this
+ measured `71b63340…980a632b` matches between the two on DuckDB.
CW.6 lands an empirical test (`test_streamed_matches_listagg_form`)
that reproduces both forms on a small fixture and asserts equality
— belt-and-braces against a Python-fold bug.

---

## Lock 3 — PG SHA-256 via `pgcrypto` extension (policy departure)

**Decision.** On the Postgres path, the per-row SHA-256 uses
`encode(digest(canon, 'sha256'), 'hex')` from the `pgcrypto`
extension. CW.2's schema-apply emits
`CREATE EXTENSION IF NOT EXISTS pgcrypto;` once before the first
table create.

**Policy departure call-out.** The project's `CLAUDE.md` documents
"no PG extensions" as one of the SQL-portability rules (the rule
exists so banks running locked-down PG installs don't need DBA
intervention to deploy recon-gen). Operator-locked override for
**provenance only**:

> The audit fingerprint needs full crypto strength to be regulator-
> defensible. Built-in `md5()` was on the table (parity-not-required
> permits it), but MD5 is publicly broken (collision attacks since
> 2004) and a regulator could reasonably reject an MD5-keyed audit
> seal. `pgcrypto` is in PG core contrib (`postgresql-contrib`
> package on Debian/Ubuntu/RHEL; built-in on Aurora PG; available
> on RDS PG by enabling it in the parameter group). The DBA
> overhead is "run one CREATE EXTENSION once at deploy time" — the
> same shape as installing `pg_stat_statements` for the perf-dump
> path. Scope strictly to provenance; do NOT extend to dataset SQL
> or schema DDL.

**Operator install responsibility.** `recon-gen schema apply` emits
the `CREATE EXTENSION IF NOT EXISTS pgcrypto;` statement, but the
*ability* to execute it requires the connecting role to have
`CREATE` permission on the database (typically the role doing
schema-apply already does, but a hardened deploy might use a
separate "DDL-only" role). If `CREATE EXTENSION` fails on PG, the
audit fingerprint won't compute — schema-apply surfaces the error
loud rather than silently degrading. Document in CW.7's quirks-log
entry.

**Per-dialect SHA-256 functions (the locked canonicalization templates).**

```sql
-- DuckDB: native sha256() over concat_ws-ed canon
SELECT sha256(concat_ws(chr(31),
    coalesce(CAST("col_a" AS VARCHAR), chr(1)),
    coalesce(CAST("col_b" AS VARCHAR), chr(1)),
    /* … all columns, sorted by lower(name) … */
)) AS rh
FROM <prefixed_table>
WHERE entry <= :hwm
ORDER BY entry;

-- PostgreSQL 17+ (requires pgcrypto):
SELECT encode(digest(concat_ws(chr(31),
    coalesce(CAST("col_a" AS VARCHAR), chr(1)),
    coalesce(CAST("col_b" AS VARCHAR), chr(1)),
    /* … */
), 'sha256'), 'hex') AS rh
FROM <prefixed_table>
WHERE entry <= :hwm
ORDER BY entry;

-- Oracle 19c: built-in STANDARD_HASH(...,'SHA256')
SELECT RAWTOHEX(STANDARD_HASH(
    "COL_A" || chr(31) || "COL_B" || chr(31) || /* … */,
    'SHA256'
)) AS rh
FROM <prefixed_table>
WHERE entry <= :hwm
ORDER BY entry;
```

**Per-dialect notes:**

- **NULL sentinel.** Every column is wrapped
  `coalesce(CAST(col AS VARCHAR), chr(1))` so a NULL in any cell
  hashes the same on every dialect. `chr(1)` is the ASCII SOH
  (Start-of-Heading) control byte — can't appear in our schema's
  data types (no binary columns reach the audit fingerprint).
  Originally specced as `chr(0)` (ASCII NUL), changed post-CW.2
  because PG's psycopg driver rejects NUL in text with
  `ProgramLimitExceeded: null character not permitted`. SOH is
  PG/DuckDB/Oracle-safe and serves the same disambiguation
  purpose (NULL vs empty string).
- **Column separator.** `chr(31)` is the ASCII UNIT SEPARATOR
  control code (same one the legacy Python ladder used).
- **Column ordering.** Sorted by `lower(name)` at SQL-emit time so
  the per-dialect physical column order can't drift the hash.
  Quote per-dialect: `"col"` on PG/DuckDB, `"COL"` on Oracle (use
  `common/sql/dialect.py::column_name`).
- **Oracle catch.** Oracle's `concat_ws` is from `LISTAGG` family
  semantics — but `||` chains work cleanly with the same
  `coalesce(...)` shape. Use `||` chained per-column, not a
  `concat_ws` builtin that doesn't exist on 19c.
- **DuckDB `concat_ws` semantics.** Skips NULL args by default; we
  wrap in `coalesce(..., chr(1))` *before* concat_ws so the NULL
  sentinel survives explicitly. Same shape as the PG path —
  symmetry by construction.

---

## Lock 4 — Field name: `provenance_format_version`

**Decision.** The format-version field added to
`ProvenanceFingerprint` is named **`provenance_format_version`**
(verbose-unambiguous form, matches the doc's own naming).

Rejected alternatives:

- `format_version` — too generic; collides with other PDF metadata.
- `version` — confused with `code_identity` field which holds the
  recon-gen package version.
- `schema_version` — would conflict with the existing `schema`
  field in `to_dict()` output (`qsg-audit-provenance-v1`).

**Migration shape.**

- **Pre-CW PDFs** have no `provenance_format_version` field in their
  embedded `to_dict()` blob. `ProvenanceFingerprint.from_dict()`
  treats `missing → 1` (the legacy format).
- **CW PDFs** emit `provenance_format_version: 2` and the streamed
  SHA-256 from Lock 2/3.
- **`audit verify` dispatch.** Reads the embedded version, branches
  on it:
  - **Version 1 (legacy):** use the frozen `legacy_hash_table_rows_v1`
    Python ladder (kept as a deprecated function in
    `common/provenance.py` for verify-only use; not exported via
    `__all__`; no new callsites permitted).
  - **Version 2 (new):** use `hash_table_rows` (the per-dialect SQL
    + streamed Python fold from Lock 2).
- **Schema field stays at `qsg-audit-provenance-v1`.** That string
  identifies the *outer JSON-blob* shape (the dict layout). When
  v2 of the blob layout lands (probably never — the per-key shape
  isn't changing) bump the outer schema string too. The new
  `provenance_format_version` is orthogonal: same dict shape, but
  the per-field SHA values are computed by a different algorithm.

**Surfaces.** `provenance_format_version` shows in three places:

1. The `ProvenanceFingerprint` dataclass field.
2. The PDF `/Subject` JSON metadata blob via `to_dict()`.
3. The Provenance Appendix's per-source breakdown table, as a
   labeled row above the source rows (so an auditor reading the PDF
   can immediately tell which canonicalization algorithm the SHA
   values used).

---

## Lock 5 — `pyhanko`-missing: graceful degrade

**Decision.** When `cfg.audit.signing` is set but `import pyhanko` raises
`ImportError`, `audit apply --execute` emits an **unsigned PDF**
plus a warning to stderr. It does NOT crash, and it does NOT skip
the PDF write.

**Rationale.** Operator-flagged side issue: shipping a hard crash
when the optional extra is missing turned a recoverable
configuration mistake into a load-bearing failure. The audit PDF's
content is still valuable without the digital signature (the
fingerprint is computed and embedded; verifiers can still recompute
+ compare); the digital signature is an additional
auditor-confidence layer, not the only one.

**Event shape.** A `dev_log` event-style stderr line:

```
audit: signing skipped — pyhanko not installed (install
   `recon-gen[prod]` to enable PDF signing). Wrote unsigned PDF
   to <path>.
```

No new in-tree event registry; just `click.echo(..., err=True)` in
the existing pattern. The CLI exit code stays 0 (the PDF wrote
successfully).

**Scope of the wrap.** Only the `import pyhanko` + `sign_pdf_in_
place(...)` call. If `pyhanko` is installed but the actual signing
fails (bad key, expired cert), that still raises — those failures
are operator misconfiguration that should surface loud.

**No new code path when `cfg.audit.signing` is None.** That's already the
no-op skip; unchanged.

---

## Implementation summary (CW.1 — CW.7)

| Leaf | What | Files touched |
|---|---|---|
| **CW.0** | This lock doc | `docs/audits/cw_0_audit_pdf_perf_locks.md` (new) |
| **CW.1** | Drop matview-evidence pass + appendix section | `common/provenance.py`, `cli/audit/__init__.py`, `cli/audit/pdf.py`, `cli/audit/markdown.py` |
| **CW.5** | pyhanko graceful degrade | `cli/audit/__init__.py` |
| **CW.2** | Stream base-table fingerprint (per-dialect SQL + Python fold) | `common/provenance.py`, `common/sql/dialect.py`, `common/l2/schema.py` (pgcrypto) |
| **CW.3** | Bump `provenance_format_version` to 2 | `common/provenance.py`, `cli/audit/pdf.py`, `cli/audit/markdown.py` |
| **CW.4** | Rewrite embedded verify-provenance.py recipe | `cli/audit/pdf.py::_build_verify_recipe_script`, `cli/audit/__init__.py::audit_verify` |
| **CW.6** | Perf regression test + Merkle-Damgård equivalence + format-v1 legacy test | `tests/unit/test_audit_pdf_perf.py` (new), `tests/audit/test_cli_smoke.py` |
| **CW.7** | Sweep — quirks log entries, archive Phase CW | `docs/reference/quicksight-quirks.md`, `PLAN.md`, `PLAN_ARCHIVE.md` |

---

## Open items (deferred / parking)

- **PG `CREATE EXTENSION pgcrypto` permission model.** If a hardened
  deploy uses a non-superuser role for schema-apply, the extension
  create might fail. CW.2 emits the statement at the top of the
  schema script; if the role lacks `CREATE` on the database, the
  whole schema-apply fails loud. Document the requirement; do NOT
  add a fallback to MD5 (Lock 3 — explicitly rejected). Backlog
  candidate: a `recon-gen schema check-prereqs` verb that runs
  `SELECT has_database_privilege(...)` + reports per-precondition
  pass/fail before the destructive run.
- **Legacy v1 fixture.** CW.6 needs a known-fixture v1 PDF to
  regression-test the legacy verify path. If no such PDF exists in
  the repo, generate one from the pre-CW commit + check it in under
  `tests/audit/fixtures/`. If the fixture-generation overhead is
  too high, document the gap in CW.6's commit body and ship without
  it (acceptable risk: the legacy Python ladder is frozen + tiny;
  unit-tested via direct invocation against a synthetic 3-row
  cursor).
