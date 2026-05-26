"""Planted "test-side" fixture for BE.1's no-test-src-sql-duplication smoke test.

The literal below is byte-for-byte the same SQL string as
``be_1_planted_src.py``'s ``PLANTED_SRC_SQL`` (with whitespace
normalization). The smoke test invokes
``NoTestSrcSqlDuplicationCheck.find_smells`` on this file (with
``src_root`` pointed at ``_fixtures/``) and asserts one hit.

See ``be_1_planted_src.py`` for the rationale.
"""


def _planted_duplicate() -> str:
    """Returns the duplicated SQL — same fingerprint as
    ``PLANTED_SRC_SQL`` in ``be_1_planted_src.py``."""
    return (
        "SELECT account_id, account_name, account_role, account_scope, "
        "amount_money, amount_direction, status, posting, transfer_id, "
        "rail_name, origin FROM example_transactions WHERE status = 'Posted'"
    )
