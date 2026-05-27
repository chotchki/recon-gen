"""Planted "src-side" fixture for BE.2's no-inline-production-constants smoke test.

Simulates a ``src/recon_gen/`` module that defines an UPPER_SNAKE
module-level string constant. The paired ``be_2_planted_test.py``
asserts against the constant's value as an inline literal — the
canonical "test inlines a production constant" drift class that
BE.2's approach-3 check catches.

Per BE.0 D8: planted fixtures protect against silent lint death.
If approach 3's AST walker regresses (assert traversal breaks,
constant-name regex breaks, value-index lookup breaks), the smoke
test goes red even when the staged-disabled check would silently
miss real drift.
"""

# A module-level UPPER_SNAKE constant — exactly the shape approach 3
# scans for. Value length 3-200 chars (per spike scope).
PLANTED_PROD_CONSTANT = "be_2_planted_sentinel_value"

# A private (_UPPER) constant too — the check scans both public and
# private. Mirrors how src/ defines internal sheet names like
# _DRIFT_NAME / _OVERDRAFT_NAME.
_PLANTED_PRIVATE_PROD_CONSTANT = "be_2_planted_private_sentinel"
