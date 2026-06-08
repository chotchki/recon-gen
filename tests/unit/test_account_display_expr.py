"""CQ.1 — Pin the NULL-safe ``account_display`` SQL expression + assert
no inline ``name || ' (' || id || ')'`` concats remain in src/.

Without the gate a future refactor could re-introduce the pre-CQ.1 shape
at any of the 7 picker / WHERE / SELECT sites surveyed in
``docs/audits/v13_6_1_picker.md`` and silently drop accounts whose
``account_name`` is NULL from every account picker on every renderer.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from recon_gen.common.sql import account_display_expr
from recon_gen.common.sql.display_labels import (
    account_display_expr as direct_import,
)


def test_helper_renders_coalesce_shape() -> None:
    """Bare columns → ``(COALESCE(name, id) || ' (' || id || ')')``."""
    assert (
        account_display_expr("account_name", "account_id")
        == "(COALESCE(account_name, account_id) || ' (' || account_id || ')')"
    )


def test_helper_passes_through_table_aliases() -> None:
    """Aliased columns survive verbatim — Daily Statement transactions
    site (``tx.account_name`` / ``tx.account_id``) keeps the alias on
    every reference."""
    assert (
        account_display_expr("tx.account_name", "tx.account_id")
        == "(COALESCE(tx.account_name, tx.account_id) || ' ('"
        " || tx.account_id || ')')"
    )


def test_helper_works_for_investigation_columns() -> None:
    """Source / target columns — Account Network + edges fetcher."""
    assert (
        account_display_expr("e.source_account_name", "e.source_account_id")
        == "(COALESCE(e.source_account_name, e.source_account_id) || ' ('"
        " || e.source_account_id || ')')"
    )


def test_helper_reexport_matches_direct_import() -> None:
    """``from recon_gen.common.sql import account_display_expr`` is the
    public surface; the leaf module re-export must be the same callable
    so monkeypatching the leaf in a test affects all callers."""
    assert account_display_expr is direct_import


_BANNED_PATTERN = re.compile(
    r"""
    [a-zA-Z_.]*account_name      # column ref (bare or aliased)
    \s*\|\|\s*'\s*\(\s*'         # || ' ('
    \s*\|\|\s*[a-zA-Z_.]*account_id
    \s*\|\|\s*'\s*\)\s*'
    """,
    re.VERBOSE,
)


def test_no_inline_account_display_concat_in_src() -> None:
    """CQ.1 anti-drift: no file in src/ may construct the
    ``account_name || ' (' || account_id || ')'`` concat inline.

    Every site must call ``account_display_expr(name_col, id_col)`` so
    the NULL-safe COALESCE wrapping is applied uniformly. Docstring
    examples that name the SHAPE (e.g.
    ``"(COALESCE(account_name, account_id) || ...)"``) are fine — the
    pattern only fires on the bare pre-CQ.1 shape.
    """
    src_root = Path(__file__).resolve().parents[2] / "src" / "recon_gen"
    offenders: list[str] = []
    for py_path in src_root.rglob("*.py"):
        text = py_path.read_text(encoding="utf-8")
        for match in _BANNED_PATTERN.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            offenders.append(f"{py_path}:{line_no}: {match.group(0)}")
    if offenders:
        joined = "\n  ".join(offenders)
        pytest.fail(
            "CQ.1 violation — inline account-display concat detected. "
            "Replace with `account_display_expr(name_col, id_col)` from "
            "`recon_gen.common.sql`. Offenders:\n  " + joined,
        )
