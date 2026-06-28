"""CY.7 — anti-drift gate for the metadata column.

Per PLAN.md operator lock 8: "if metadata column gets renamed or its
IS-JSON constraint changes, the popup-fetcher should fail loud not
silent."

Two-layer guard. If either layer breaks, the build trips at the buggy
line — no silent-empty-render fallback for the metadata popup.

**Layer A — DDL contract** (parametrized over every dialect):
The L1 ``<prefix>_transactions`` DDL emitted by
``common/l2/schema.py::emit_schema`` MUST contain:

1. A ``metadata`` column declaration in the dialect-appropriate string
   type (``VARCHAR(4000)`` on PG / DuckDB; ``VARCHAR2(4000)`` on Oracle —
   from ``common/sql/dialect.py::json_text_type``);
2. The ``json_check("metadata", dialect)`` constraint helper output —
   ``CHECK (metadata IS NULL OR metadata IS JSON)`` on PG / Oracle,
   ``CHECK (metadata IS NULL OR json_valid(metadata))`` on DuckDB.

Rename / drop / weaken the constraint and the assertion fires.

**Layer B — Construction-time gate** (CY.4 ``Table.__post_init__``):
Build a synthetic Dataset whose registered contract has NO ``metadata``
column. Try to construct a ``Table`` with ``metadata_popup=True`` bound
to that dataset. ``__post_init__`` MUST raise — the wiring fails at the
buggy line, not at fetch time.

The CY.4 agent went with a plain ``ValueError`` (no custom
``MetadataPopupColumnMissing`` class), so we pin on the exception type +
the operator-locked message phrase. If a future cell upgrades the check
to a typed exception, swap ``ValueError`` for the new class here.

**Layer C — Route-level gate** (CY.5 metadata route):
A malformed metadata URL param MUST yield 500 + the locked phrase
``'metadata JSON parse failed'`` (the phrase ``_side_panel.py`` actually
emits — defense-in-depth behind the DB IS-JSON constraint), and MUST
NOT silently render the empty-metadata fragment. The empty-metadata
branch still renders ``'No metadata for this row.'`` — that's the
legitimate empty case, not the drift case.

Pins the contract that future schema changes can't silently break the
popup.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.dataset_contract import (
    ColumnSpec,
    DatasetContract,
    isolated_dataset_registries,
    register_contract,
)
from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    stub_money_trail_fetcher,
)
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.ids import SheetId, VisualId
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.sql.dialect import Dialect, json_check, json_text_type
from recon_gen.common.tree.datasets import Dataset
from recon_gen.common.tree.structure import Analysis, App, Sheet
from recon_gen.common.tree.visuals import Dim, Table
from tests._test_helpers import make_test_config


# ---------------------------------------------------------------------------
# Layer A — DDL contract: every dialect's schema emits the metadata column
# AND the IS-JSON / json_valid CHECK constraint.
# ---------------------------------------------------------------------------


_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"


@pytest.mark.parametrize(
    "dialect",
    [Dialect.POSTGRES, Dialect.ORACLE, Dialect.DUCKDB],
    ids=["postgres", "oracle", "duckdb"],
)
def test_emit_schema_declares_metadata_column_with_json_check(
    dialect: Dialect,
) -> None:
    """``<prefix>_transactions`` DDL carries a ``metadata`` column of
    the dialect's bounded string type AND the dialect-appropriate
    ``IS JSON`` / ``json_valid`` check constraint.

    Anti-drift: rename ``metadata`` to anything else (or drop the check)
    and this assertion fires before the popup-fetcher hits prod.
    """
    instance = load_instance(_SPEC_EXAMPLE)
    sql = emit_schema(instance, prefix=_PREFIX, dialect=dialect)

    # The column declaration is rendered as ``metadata <type>`` inside
    # the CREATE TABLE block. Both base tables (transactions +
    # daily_balances) share the column name; we just need the substring
    # to fire so a rename to ``meta_json`` breaks loud.
    expected_type = json_text_type(dialect)
    assert f"metadata             {expected_type}" in sql or (
        f"metadata               {expected_type}" in sql
    ), (
        f"emit_schema({dialect.value}) is missing the `metadata "
        f"{expected_type}` column declaration. If you renamed the column "
        f"or changed its type, update CY.4 + CY.5 (popup-fetcher) at "
        f"the same time — operator lock 8."
    )

    # The IS-JSON / json_valid check constraint MUST land in the DDL
    # verbatim from ``json_check`` — drop or weaken it and the popup
    # loses its production guarantee that the column always parses.
    expected_check = json_check("metadata", dialect)
    assert expected_check in sql, (
        f"emit_schema({dialect.value}) is missing the metadata "
        f"IS-JSON constraint {expected_check!r}. Dropping or weakening "
        f"this constraint silently lets non-JSON values reach the popup "
        f"fetcher — operator lock 8: fail loud, not silent."
    )


# ---------------------------------------------------------------------------
# Layer B — Construction-time gate: Table(metadata_popup=True) raises at the
# wiring site if the bound dataset's contract has no ``metadata`` column.
# ---------------------------------------------------------------------------


def test_table_metadata_popup_true_raises_when_contract_lacks_metadata() -> (
    None
):
    """CY.4's ``Table.__post_init__`` MUST raise when a Table is wired
    ``metadata_popup=True`` against a dataset whose registered contract
    omits the ``metadata`` column. The mistake fails at the buggy line —
    not at fetch time, not as an empty popup, not silently.

    The CY.4 agent went with ``ValueError`` (no custom typed exception
    class). If a future cell upgrades to a typed
    ``MetadataPopupColumnMissing`` class, swap the exception type here.
    """
    cfg = make_test_config()
    with isolated_dataset_registries():
        ds_no_meta = Dataset(
            identifier="cy7-drift-no-meta",
        )
        # Contract carries an ``id`` column only — explicitly NO
        # ``metadata`` (simulates the rename / drop case).
        register_contract(
            ds_no_meta.identifier,
            DatasetContract(columns=[ColumnSpec("id", "STRING")]),
        )

        with pytest.raises(ValueError) as excinfo:
            Table(
                visual_id=VisualId("drift-tbl"),
                title="Drift Detection Table",
                subtitle=(
                    "CY.7 anti-drift fixture — should fail at construction "
                    "because the contract has no metadata column."
                ),
                columns=[
                    Dim(
                        dataset=ds_no_meta,
                        field_id="f-id",
                        column="id",
                    ),
                ],
                metadata_popup=True,
            )

        message = str(excinfo.value)
        # The exact phrasing comes from
        # ``common/tree/visuals.py::Table.__post_init__`` — keep both
        # substrings so a future polish on either side keeps the test
        # honest about WHY it tripped.
        assert "metadata_popup=True" in message, (
            f"Expected the raised message to name metadata_popup=True; "
            f"got {message!r}."
        )
        assert "'metadata' column" in message, (
            f"Expected the raised message to reference the missing "
            f"'metadata' column; got {message!r}."
        )
    # ``cfg`` is unused on this path but make_test_config() proves the
    # helper still imports — kept for symmetry with the route fixture
    # below which DOES need a config.
    del cfg


# ---------------------------------------------------------------------------
# Layer C — Route-level gate: malformed metadata URL param surfaces as
# 500 with the explicit ``metadata JSON parse failed`` phrase, NOT a
# silent empty-state fallback.
# ---------------------------------------------------------------------------


@pytest.fixture
def anti_drift_app() -> Iterator[tuple[Any, str, str]]:
    """Spin a Starlette ``make_app`` with one dashboard / one sheet
    whose Table is wired ``metadata_popup=True``. Yields
    ``(app, dash_id, sheet_id)``.

    Mirrors ``tests/unit/test_cy_metadata_side_panel.py`` so we exercise
    the same code path that surfaces in production.
    """
    cfg = make_test_config()
    with isolated_dataset_registries():
        ds_with_meta = Dataset(
            identifier="cy7-with-meta",
        )
        register_contract(
            ds_with_meta.identifier,
            DatasetContract(columns=[
                ColumnSpec("id", "STRING"),
                ColumnSpec("metadata", "STRING"),
            ]),
        )
        app_tree = App(name="cy7-anti-drift-app", cfg=cfg)
        analysis = app_tree.set_analysis(
            Analysis(
                analysis_id_suffix="cy7-anti-drift-analysis",
                name="CY7 Anti Drift",
            )
        )
        sheet = analysis.add_sheet(
            Sheet(
                sheet_id=SheetId("rows-sheet"),
                name="Rows",
                title="Rows",
                description="Sheet hosting the metadata_popup=True Table.",
            )
        )
        sheet.visuals.append(
            Table(
                visual_id=VisualId("rows-tbl"),
                title="Rows Detail",
                subtitle=(
                    "CY.7 anti-drift fixture — metadata_popup=True Table "
                    "for the route-level gate."
                ),
                columns=[
                    Dim(
                        dataset=ds_with_meta, field_id="f-id", column="id",
                    ),
                ],
                metadata_popup=True,
            )
        )
        served = ServedDashboard(
            tree_app=app_tree, sheet=sheet, title="anti-drift",
            data_fetcher=stub_money_trail_fetcher,
            filter_specs=SMOKE_FILTER_SPECS,
        )
        app = make_app(dashboards={"anti-drift": served})
        yield app, "anti-drift", "rows-sheet"


def test_route_malformed_metadata_fails_loud_not_silent(
    anti_drift_app: tuple[Any, str, str],
) -> None:
    """Malformed metadata URL param → 500 + the locked
    ``'metadata JSON parse failed'`` phrase. Explicitly NOT the
    empty-state fragment — that would be the silent fallback operator
    lock 8 prohibits.
    """
    app, dash_id, sheet_id = anti_drift_app
    with TestClient(app) as c:  # type: ignore[arg-type]: Starlette TestClient signature accepts ASGI3Application but pyright's stub expects narrower type
        resp = c.get(
            f"/dashboards/{dash_id}/sheets/{sheet_id}/rows/metadata",
            params={
                "metadata": "{not valid json",
                "transaction_id": "drift-txn",
            },
        )
    assert resp.status_code == 500, (
        f"Expected 500 for malformed metadata; got {resp.status_code}. "
        f"Operator lock 8: drift must fail loud."
    )
    body = resp.text
    assert "metadata JSON parse failed" in body, (
        f"Expected the body to carry the locked 'metadata JSON parse "
        f"failed' phrase; got {body!r}."
    )
    # Explicit rejection of the silent-fallback path — the legitimate
    # empty-metadata branch DOES render this exact string; for the
    # drift case it MUST NOT.
    assert "No metadata for this row." not in body, (
        "Malformed metadata fell through to the empty-state fragment — "
        "that's the silent fallback operator lock 8 prohibits. The "
        "drift case must surface a 500 + explicit parse-failed message."
    )


def test_route_legitimate_empty_metadata_still_renders_empty_state(
    anti_drift_app: tuple[Any, str, str],
) -> None:
    """Sanity-pair test: the legitimate empty-metadata branch (``{}``)
    still renders the operator-locked ``'No metadata for this row.'``
    fragment. Confirms the drift gate above isn't a false-positive
    detector that fires on every empty payload.
    """
    app, dash_id, sheet_id = anti_drift_app
    with TestClient(app) as c:  # type: ignore[arg-type]: Starlette TestClient signature accepts ASGI3Application but pyright's stub expects narrower type
        resp = c.get(
            f"/dashboards/{dash_id}/sheets/{sheet_id}/rows/metadata",
            params={
                "metadata": "{}",
                "transaction_id": "empty-txn",
            },
        )
    assert resp.status_code == 200
    body = resp.text
    assert "No metadata for this row." in body, (
        f"Expected the legitimate empty-metadata branch to render the "
        f"locked empty-state fragment; got {body!r}."
    )
