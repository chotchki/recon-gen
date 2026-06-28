"""Unit tests for the runner's ``--only`` plumbing (DL.3.3 + DL.3.4).

DL.3.3: ``_normalize_only_expr`` rewrites visible-name substrings
carrying spaces (``"Leaf Account or Parent Account"``) into a
pytest-tokenizer-safe form (``"(Leaf and Account) or (Parent and
Account)"``) so the operator can paste-copy visual / parametrize-id
strings into ``--only`` without hand-translating them to underscore
identifiers.

DL.3.4: when ``--only`` filters out all items in an earlier-layer
pytest invocation, xdist crashes with ``assert not crashitem`` →
exit=3 (INTERNAL_ERROR). The existing rc=5→0 layer-skip arm only
handled the no-xdist no-items shape. The fix extends the arm to
also accept rc=3 when ``--only`` is set so the chain can advance to
the layer the operator's filter targeted.
"""

from __future__ import annotations

import pytest

from recon_gen._dev import runner as r


# ---------------------------------------------------------------------------
# DL.3.3 — _normalize_only_expr.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Passthrough cases — already tokenizer-safe.
        (None, None),
        ("", ""),
        ("   ", "   "),  # whitespace-only short-circuits via .strip() guard
        ("foo", "foo"),
        ("foo or bar", "foo or bar"),
        ("foo and bar", "foo and bar"),
        ("not foo", "not foo"),
        ("a and b and c", "a and b and c"),
        # Operator-only deduplication.
        ("foo  or  bar", "foo or bar"),
        # Space-bearing identifier — single token.
        ("Leaf Account", "(Leaf and Account)"),
        ("Single Word", "(Single and Word)"),
        # Space-bearing identifiers joined by operators (the DL.3.3
        # canonical case).
        (
            "Leaf Account or Parent Account",
            "(Leaf and Account) or (Parent and Account)",
        ),
        # Mixed — operators interleave with multi-word tokens.
        ("Leaf Account or Parent", "(Leaf and Account) or Parent"),
        ("not Leaf Account", "not (Leaf and Account)"),
        # Three-word multi-token.
        (
            "Leaf Account Drift or Parent",
            "(Leaf and Account and Drift) or Parent",
        ),
        # Parens — preserved; inner multi-word wraps separately so the
        # outer paren isn't lost.
        (
            "(Leaf Account) or (Parent Account)",
            "((Leaf and Account)) or ((Parent and Account))",
        ),
        # More complex composition.
        (
            "foo or Leaf Account Drift and not bar",
            "foo or (Leaf and Account and Drift) and not bar",
        ),
    ],
)
def test_normalize_only_expr(raw: str | None, expected: str | None) -> None:
    """The normalizer's transformation table."""
    assert r._normalize_only_expr(raw) == expected


def test_normalize_only_expr_output_is_pytest_safe() -> None:
    """The canonical DL.3.3 case round-trips through pytest's -k
    tokenizer cleanly.

    pytest's ``-k`` parser rejects identifiers containing whitespace
    with ``at column N: expected end of input; got identifier``. The
    normalizer's output must parse without raising — we don't run a
    test session, just probe the parser via ``pytest.mark.skipif`` /
    ``_pytest.mark.expression.Expression.compile``.
    """
    from _pytest.mark.expression import Expression  # noqa: PLC0415 — internal pytest API

    raw = "Leaf Account or Parent Account"
    normalized = r._normalize_only_expr(raw)
    assert normalized is not None
    # Raw form raises — this is the bug we're working around.
    with pytest.raises(Exception):  # noqa: BLE001 — _pytest's ParseError doesn't subclass anything stable
        Expression.compile(raw)
    # Normalized form parses cleanly. No assertion on the resulting
    # match function — that's covered by pytest's own test suite.
    Expression.compile(normalized)


def test_options_from_args_normalizes_only() -> None:
    """``_options_from_args`` wires the normalizer in.

    Operator pastes a visible-name string into ``--only``; the
    ``RunOptions.only`` field that downstream layers read carries the
    tokenizer-safe form.
    """
    import argparse  # noqa: PLC0415 — test-local

    ns = argparse.Namespace(
        only="Leaf Account or Parent Account",
        parallel=1,
        scenarios=None,
        dialects=None,
        targets=None,
        variants=None,
        fuzz_seeds=1,
        skip_cheap=False,
        keep_on_failure=False,
        trace_all=False,
        coverage=False,
    )
    opts = r._options_from_args(ns)
    assert opts.only == "(Leaf and Account) or (Parent and Account)"


def test_options_from_args_passes_through_none_only() -> None:
    """``--only`` omitted → ``RunOptions.only`` stays ``None``."""
    import argparse  # noqa: PLC0415 — test-local

    ns = argparse.Namespace(only=None)
    opts = r._options_from_args(ns)
    assert opts.only is None


# ---------------------------------------------------------------------------
# DL.3.4 — xdist exit=3 layer-skip tolerance when ``--only`` filters
# out the early layer.
# ---------------------------------------------------------------------------


def test_is_only_no_match_exit_rc_table() -> None:
    """``_is_only_no_match_exit`` classification table.

    The runner uses this to translate xdist's ``assert not crashitem``
    INTERNAL_ERROR (rc=3) AND pytest's NO_TESTS_COLLECTED (rc=5) into
    a clean layer-skip when the operator's ``-k`` expression doesn't
    match anything in that layer — same semantics either way:
    ``--only`` is targeted at a later layer, the early layer has
    nothing to do.
    """
    # rc=5 — pytest's "no tests collected".
    assert r._is_only_no_match_exit(5, "foo") is True
    # rc=3 — xdist's INTERNAL_ERROR (DL.3.4 path).
    assert r._is_only_no_match_exit(3, "foo") is True
    # rc=0 — passing tests; never skip.
    assert r._is_only_no_match_exit(0, "foo") is False
    # rc=1 — real test failures; never mask.
    assert r._is_only_no_match_exit(1, "foo") is False
    # rc=2 — KeyboardInterrupt; surface it.
    assert r._is_only_no_match_exit(2, "foo") is False
    # rc=4 — usage error; never mask.
    assert r._is_only_no_match_exit(4, "foo") is False
    # rc=5 without --only — surface as no-tests-found, don't auto-skip.
    assert r._is_only_no_match_exit(5, None) is False
    # rc=3 without --only — real INTERNAL_ERROR; never mask.
    assert r._is_only_no_match_exit(3, None) is False
