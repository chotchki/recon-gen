"""Planted "test-side" fixture for BE.2's no-inline-production-constants smoke test.

The planted call-sites below inline the literal values of
``PLANTED_PROD_CONSTANT`` and ``_PLANTED_PRIVATE_PROD_CONSTANT``
from ``be_2_planted_src.py`` across both the ``ast.Assert`` scan
shape AND the ``ast.Call`` arg/keyword shape — the smoke test
invokes ``NoInlineProductionConstantsCheck.find_smells`` on this
file and asserts 4 hits (one per inline literal across both
shapes).

Why both shapes: the original BE.2 scope was just ``ast.Assert``,
which silently missed visual titles passed to driver verbs
(``driver.wait_loaded("Matview Status")`` etc — caught by CI as
test_qs_table_rows_well_formed in 2026-05-27). The extended scope
adds ``ast.Call`` args + keywords. The fixture exercises both.

See ``be_2_planted_src.py`` for the rationale.
"""


def _planted_assert_public() -> bool:  # pyright: ignore[reportUnusedFunction]: invoked by AST walker, not Python
    """Inline-assert against the public planted constant — should
    trip the lint with a message naming PLANTED_PROD_CONSTANT in
    be_2_planted_src.py."""
    actual = "be_2_planted_sentinel_value"
    assert actual == "be_2_planted_sentinel_value", (
        "public planted constant mismatch"
    )
    return True


def _planted_assert_private() -> bool:  # pyright: ignore[reportUnusedFunction]: invoked by AST walker, not Python
    """Inline-assert against the private planted constant — should
    trip the lint naming _PLANTED_PRIVATE_PROD_CONSTANT."""
    actual = "be_2_planted_private_sentinel"
    assert actual == "be_2_planted_private_sentinel", (
        "private planted constant mismatch"
    )
    return True


def _noop(*args: object, **kwargs: object) -> None:
    """Stub call target — exists so the planted Call shapes below
    compile + walk through the visitor; the body never runs (the
    enclosing _planted_call_* functions are AST-fixtures, not
    invoked at runtime).
    """
    del args, kwargs


def _planted_call_arg() -> None:  # pyright: ignore[reportUnusedFunction]: invoked by AST walker, not Python
    """Inline the public planted constant as a positional CALL arg
    — should trip the lint (extended scope covers ast.Call args)."""
    _noop("be_2_planted_sentinel_value")


def _planted_call_keyword() -> None:  # pyright: ignore[reportUnusedFunction]: invoked by AST walker, not Python
    """Inline the private planted constant as a keyword CALL arg —
    should trip the lint (extended scope covers ast.Call keywords)."""
    _noop(value="be_2_planted_private_sentinel")
