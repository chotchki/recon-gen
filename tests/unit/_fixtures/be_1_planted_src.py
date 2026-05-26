"""Planted "src-side" fixture for BE.1's no-test-src-sql-duplication smoke test.

This file simulates a ``src/recon_gen/`` module that defines a long SQL
literal. The paired ``be_1_planted_test.py`` simulates a ``tests/`` file
that copies the same literal — the smoke test points
``NoTestSrcSqlDuplicationCheck.src_root`` at this directory and asserts
the visitor finds the duplication.

Per BE.0 D8: the planted-fixture pattern protects against silent lint
death once BE.4's sweep clears the real corpus to 0 hits. If the
visitor regresses (regex breakage, AST-walker traversal bug, indexing
bug), the smoke test goes red even if the actual lint reports 0.

Both fixture files are excluded from the production lint's
``check.files`` scope so they never themselves trip the rule —
``_fixtures/`` is filtered out in ``_build_checks()``.
"""

# A long SQL-shaped string literal (>= 100 chars after whitespace
# normalization). The exact text doesn't matter for the smoke test;
# only that the fingerprint matches the paired test-side fixture.
PLANTED_SRC_SQL = (
    "SELECT account_id, account_name, account_role, account_scope, "
    "amount_money, amount_direction, status, posting, transfer_id, "
    "rail_name, origin FROM example_transactions WHERE status = 'Posted'"
)
