"""Planted "test-side" fixture for BE.2's no-inline-production-constants smoke test.

The two assertions below inline the literal values of
``PLANTED_PROD_CONSTANT`` and ``_PLANTED_PRIVATE_PROD_CONSTANT``
from ``be_2_planted_src.py`` — the smoke test invokes
``NoInlineProductionConstantsCheck.find_smells`` on this file and
asserts 2 hits (one per planted-inline-literal).

The check scans EVERY string literal inside an ``ast.Assert``
node, including nested expressions inside the assertion message —
that's why the planted contract uses both an `assert` test arg
AND an assertion message string.

See ``be_2_planted_src.py`` for the rationale.
"""


def _planted_assert_public() -> bool:
    """Inline-assert against the public planted constant — should
    trip the lint with a message naming PLANTED_PROD_CONSTANT in
    be_2_planted_src.py."""
    actual = "be_2_planted_sentinel_value"
    assert actual == "be_2_planted_sentinel_value", (
        "public planted constant mismatch"
    )
    return True


def _planted_assert_private() -> bool:
    """Inline-assert against the private planted constant — should
    trip the lint naming _PLANTED_PRIVATE_PROD_CONSTANT."""
    actual = "be_2_planted_private_sentinel"
    assert actual == "be_2_planted_private_sentinel", (
        "private planted constant mismatch"
    )
    return True
