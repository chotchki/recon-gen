"""DS.3.4 — the PROVEN-on-D enumeration harness.

For every database state in a finite boundary-derived domain, the REAL
engine (unmodified ``emit_schema`` + ``refresh_matviews_sql``, real
refresh order, real UNIQUE-index contracts) must produce exactly the
violation set ``{cells : residual(state) != 0}`` where the residuals
are the DS.1 laws in ``recon_gen.common.spine.residuals`` — never a
re-implementation.

Package layout:

- ``harness.py`` — DB machinery (fresh in-memory DuckDB per run, real
  config populate via ``serialize_l2``), the cell packer with
  per-family packing contracts, arrow bulk loader, violation-set
  comparator, statement-timeout guard, packed-vs-isolated lemma.
- ``domains/`` — one module per detector building its exhaustive
  domain from L2-RESOLVED comparison values (the BoundaryProfile).

The pytest entry is ``tests/unit/test_ds35_enumeration_gate.py``; this
package carries no test files of its own.
"""
