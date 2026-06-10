"""CY.4.1 — ``ColumnSpec.hidden`` flag suppresses the column on App2's
Table renderer.

Contract: a dataset that declares ``ColumnSpec("metadata", "STRING",
hidden=True)`` still projects the value through the SELECT (so the row
payload carries it, which is what the per-row metadata-popup wiring
will consume), but the App2 table renderer SKIPS emitting both the
``<th>`` header and the per-row ``<td>`` cell. The QS path is unaffected
since QS only declares fields explicitly placed on the visual's field
wells — a hidden contract column never reaches QS unless it was put on
the visual directly.

Three gates:

1. ``shape_table`` stamps ``hidden: True`` on the emitted column object
   when the ``column_hidden`` map says so. The row payload still
   carries the cell positionally (so the script tag's
   ``data-chart-data`` has the full row).
2. ``_table_column_meta`` derives the hidden map from the contract's
   ``ColumnSpec.hidden`` flag, and a column EXPLICITLY placed on a
   visual's field well overrides the contract default (author intent
   wins).
3. The L1 dashboard's two metadata columns
   (``TRANSACTIONS_CONTRACT`` + ``DAILY_STATEMENT_TRANSACTIONS_CONTRACT``)
   carry ``hidden=True``, completing the CY.4 → CY.4.1 chain so the
   metadata column ships through the SELECT but is invisible on the
   table. (Anti-drift: if CY.4 ever drops the metadata column, this
   gate catches the regression by failing first.)
"""

from __future__ import annotations

from recon_gen.apps.l1_dashboard.datasets import (
    DAILY_STATEMENT_TRANSACTIONS_CONTRACT,
    TRANSACTIONS_CONTRACT,
)
from recon_gen.common.dataset_contract import ColumnSpec
from recon_gen.common.html._data_shape import shape_table


class TestShapeTableHiddenFlag:
    def test_hidden_column_stamps_hidden_true(self) -> None:
        """``column_hidden`` map → emitted column carries ``hidden: True``."""
        out = shape_table(
            rows=[("tx1", 100, '{"foo": "bar"}')],
            columns=["transaction_id", "amount", "metadata"],
            column_hidden={"metadata": True},
        )
        cols_by_name = {c["name"]: c for c in out["columns"]}
        assert cols_by_name["metadata"].get("hidden") is True, (
            f"metadata column should carry hidden=True; got "
            f"{cols_by_name['metadata']!r}"
        )
        # Non-hidden columns get no `hidden` key (kept clean).
        assert "hidden" not in cols_by_name["transaction_id"]
        assert "hidden" not in cols_by_name["amount"]

    def test_row_payload_still_includes_hidden_cells(self) -> None:
        """CY.4.1 contract: hidden columns are dropped from rendered
        chrome but the row tuple is UNCHANGED — the popup wiring
        reads ``data-chart-data`` from the script tag (carrying the
        full payload) and indexes by column name."""
        rows = [("tx1", 100, '{"foo": "bar"}'), ("tx2", 200, '{"baz": "qux"}')]
        out = shape_table(
            rows=rows,
            columns=["transaction_id", "amount", "metadata"],
            column_hidden={"metadata": True},
        )
        # Row payload still has 3 cells per row (positional with the
        # full columns list); the d3 renderer filters at paint time.
        assert out["rows"] == [list(r) for r in rows]
        assert all(len(r) == 3 for r in out["rows"]), (
            f"hidden columns must stay in the row payload positionally "
            f"so popup wiring can read them; got {out['rows']!r}"
        )

    def test_empty_column_hidden_omits_key(self) -> None:
        """Default path (no ``column_hidden`` supplied) keeps the
        emitted columns clean — no spurious ``hidden: False``."""
        out = shape_table(
            rows=[("a", 1)],
            columns=["letter", "n"],
        )
        for col in out["columns"]:
            assert "hidden" not in col, (
                f"`hidden` should be omitted (not stamped False) when "
                f"the column isn't hidden; got {col!r}"
            )

    def test_columns_list_keeps_hidden_entry_for_positional_alignment(
        self,
    ) -> None:
        """Hidden columns stay in the ``columns`` array — the renderer
        filters at paint, not at the shape layer. This is what lets
        the per-row ``columns[ci]`` lookup in bootstrap.js's td-mapping
        match cell index to col index even when some cols are hidden."""
        out = shape_table(
            rows=[("a", 1, "meta")],
            columns=["letter", "n", "metadata"],
            column_hidden={"metadata": True},
        )
        names = [c["name"] for c in out["columns"]]
        assert names == ["letter", "n", "metadata"], (
            f"hidden columns must stay in the columns array for "
            f"positional cell→col lookup; got {names!r}"
        )


class TestColumnSpecHiddenDefault:
    def test_default_is_false(self) -> None:
        """Backward-compatible: ``hidden`` defaults to False so every
        existing contract column-spec keeps rendering normally."""
        spec = ColumnSpec("foo", "STRING")
        assert spec.hidden is False

    def test_explicit_true_persists(self) -> None:
        spec = ColumnSpec("metadata", "STRING", hidden=True)
        assert spec.hidden is True


class TestL1MetadataColumnsAreHidden:
    """Anti-drift: the two L1 dashboard contracts CY.4 added metadata to
    must keep that column hidden=True so the cold-read doesn't show raw
    ``{}`` cells. If the metadata column is removed entirely, the
    upstream CY.4 contract gate
    (``tests/unit/test_l1_metadata_popup_contracts.py``) catches it
    first; this gate covers the "column kept, hidden flipped off"
    regression shape."""

    def test_transactions_metadata_is_hidden(self) -> None:
        col = next(
            c for c in TRANSACTIONS_CONTRACT.columns if c.name == "metadata"
        )
        assert col.hidden is True, (
            "TRANSACTIONS_CONTRACT.metadata must carry hidden=True so "
            "App2's table renderer skips the raw '{}' column."
        )

    def test_daily_statement_metadata_is_hidden(self) -> None:
        col = next(
            c
            for c in DAILY_STATEMENT_TRANSACTIONS_CONTRACT.columns
            if c.name == "metadata"
        )
        assert col.hidden is True, (
            "DAILY_STATEMENT_TRANSACTIONS_CONTRACT.metadata must carry "
            "hidden=True so App2's table renderer skips the raw '{}' "
            "column."
        )
