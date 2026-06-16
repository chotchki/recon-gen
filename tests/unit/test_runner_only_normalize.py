"""Unit tests for the runner's ``--only`` plumbing (DL.3.3).

DL.3.3: ``_normalize_only_expr`` rewrites visible-name substrings
carrying spaces (``"Leaf Account or Parent Account"``) into a
pytest-tokenizer-safe form (``"(Leaf and Account) or (Parent and
Account)"``) so the operator can paste-copy visual / parametrize-id
strings into ``--only`` without hand-translating them to underscore
identifiers.
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
        allow_dirty_deploy=False,
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
