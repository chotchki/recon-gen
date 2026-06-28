"""AO.R.5 — App2 table column contract-consumption gate.

The smell this guards (operator-named 2026-05-21): a presentation field
declared once on the shared contract (``ColumnSpec.human_name`` header,
``currency`` measure format) was dropped by App2's ``shape_table``, which
emitted bare ``[{"name"}]`` so the d3 renderer fell back to the raw
snake_case SQL column name. AO.R.1 threads each per-column label + format
from the contract through the App2 fetcher → ``shape_table``.

This test pins that end-to-end for every Table visual in all four
bundled apps: it runs the real fetcher-side derivation (``_table_column_meta``)
through ``shape_table`` and asserts each emitted column carries

- ``label`` == the dataset contract's ``human_name`` (the SAME value the
  ``field_label`` helper resolves to) — pinned to the
  contract, not to the deriver, so a regression in either side fails here;
- ``format`` == ``"currency"`` for every column bound to a ``currency=True``
  measure/dim.

A contract field (header / currency) that App2 drops can no longer ship
silently.
"""

from __future__ import annotations

from typing import Any

import pytest

from recon_gen.apps.executives.app import build_executives_app
from recon_gen.apps.executives.datasets import (
    build_all_datasets as build_exec_datasets,
)
from recon_gen.apps.investigation.app import build_investigation_app
from recon_gen.apps.investigation.datasets import (
    build_all_datasets as build_inv_datasets,
)
from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app
from recon_gen.apps.l1_dashboard.datasets import build_all_l1_dashboard_datasets
from recon_gen.apps.l2_flow_tracing.app import build_l2_flow_tracing_app
from recon_gen.apps.l2_flow_tracing.datasets import (
    build_all_l2_flow_tracing_datasets,
)
from recon_gen.common.dataset_contract import DatasetContract, get_contract
from recon_gen.common.html._data_shape import shape_table
from recon_gen.common.html._tree_fetcher import (
    _find_visual_dataset_identifier,
    _leaf_column_name,
    _table_column_meta,
)
from recon_gen.common.l2 import default_l2_instance
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from recon_gen.common.tree.fields import Dim, Measure
from recon_gen.common.tree.structure import App
from tests._test_helpers import make_test_config


def _build_app(app_name: str) -> App:
    """Build a real app (+ register its datasets) for ``app_name``."""
    cfg = make_test_config(db_table_prefix=DEFAULT_PREFIX)
    inst = default_l2_instance()
    if app_name == "l1_dashboard":
        build_all_l1_dashboard_datasets(cfg, inst)
        app = build_l1_dashboard_app(cfg, l2_instance=inst)
    elif app_name == "executives":
        build_exec_datasets(cfg)
        app = build_executives_app(cfg, l2_instance=inst)
    elif app_name == "investigation":
        build_inv_datasets(cfg, inst)
        app = build_investigation_app(cfg, l2_instance=inst)
    elif app_name == "l2_flow_tracing":
        build_all_l2_flow_tracing_datasets(cfg, inst)
        app = build_l2_flow_tracing_app(cfg, l2_instance=inst)
    else:  # pragma: no cover - guarded by parametrize
        raise AssertionError(app_name)
    app.resolve_auto_ids()
    return app


def _table_leaves(visual: Any) -> list[tuple[str, Dim | Measure]]:  # typing-smell: ignore[explicit-any]: walks dynamic visual subtypes
    """``(column_name, leaf)`` for each Dim/Measure leaf of a Table's
    field wells (the columns the renderer paints headers for)."""
    out: list[tuple[str, Dim | Measure]] = []
    for field_name in ("columns", "group_by", "values"):
        fv: Any = getattr(visual, field_name, None)  # typing-smell: ignore[explicit-any]: dynamic field well off a visual subtype, narrowed by the isinstance walk below
        if fv is None:
            continue
        items = fv if isinstance(fv, list) else [fv]  # pyright: ignore[reportUnknownVariableType]: pyHanko reader iterator
        for item in items:  # pyright: ignore[reportUnknownVariableType]: pyHanko reader iterator
            if isinstance(item, (Dim, Measure)):
                name = _leaf_column_name(item)
                if name is not None:
                    out.append((name, item))
    return out


_APPS = ["l1_dashboard", "executives", "investigation", "l2_flow_tracing"]


@pytest.mark.parametrize("app_name", _APPS)
def test_app2_table_columns_carry_contract_header_and_currency(app_name: str) -> None:
    app = _build_app(app_name)
    assert app.analysis is not None

    checked = 0
    for sheet in app.analysis.sheets:
        for visual in sheet.visuals:
            if type(visual).__name__ != "Table":
                continue
            ds_id = _find_visual_dataset_identifier(visual)
            labels, formats, _hidden, _decoration = _table_column_meta(visual, ds_id)
            contract: DatasetContract | None = None
            if ds_id is not None:
                try:
                    contract = get_contract(ds_id)
                except KeyError:
                    contract = None

            leaves = _table_leaves(visual)
            col_names = [name for name, _ in leaves]
            # Shape exactly as the fetcher does (end-to-end through the
            # wire dict the renderer reads).
            shaped = shape_table(
                rows=[], columns=col_names,
                column_labels=labels, column_formats=formats,
            )
            col_by_name = {c["name"]: c for c in shaped["columns"]}
            leaf_by_name = dict(leaves)

            for name in col_names:
                checked += 1
                col = col_by_name[name]
                leaf = leaf_by_name[name]
                # Header: App2 must carry SOME human label (never the bare
                # raw name only) ...
                assert "label" in col and col["label"], (
                    f"{app_name}: Table column {name!r} has no App2 header "
                    f"label — it would render as the raw SQL name."
                )
                # ... and when the column is on the contract, that label
                # must equal the contract's human_name — the single source
                # of truth for the column header.
                if contract is not None and name in contract.column_names:
                    assert col["label"] == contract.column(name).human_name, (
                        f"{app_name}: Table column {name!r} App2 label "
                        f"{col['label']!r} != contract human_name "
                        f"{contract.column(name).human_name!r} (contract header)."
                    )
                # Currency parity: a currency-flagged measure/dim formats
                # as currency on both renderers.
                if getattr(leaf, "currency", False):
                    assert col.get("format") == "currency", (
                        f"{app_name}: Table column {name!r} is a currency "
                        f"leaf but App2 format is {col.get('format')!r}."
                    )

    assert checked > 0, (
        f"{app_name}: no Table columns checked — the parity gate is "
        f"vacuous (did the app's Table visuals or field wells change?)."
    )


# ---------------------------------------------------------------------------
# Phase DA — decoration map parity (Drillable visual cue)
# ---------------------------------------------------------------------------


def test_table_column_meta_returns_decoration_map_for_drillable() -> None:
    """Phase DA — `_table_column_meta` returns a per-column decoration
    map keyed by `Drillable.on.column`. The visual kind ("accent" vs
    "accent-menu") is resolved by the `Drillable.visual_kind` code path
    the App2 decoration map reads: a column with any DATA_POINT_MENU
    drill writing from it resolves to "accent-menu"; a column with only
    DATA_POINT_CLICK drill(s) resolves to "accent".

    Standalone Table fixture (not an app build) so the test names the
    expected decoration for each shape rather than discovering it from
    apps that may evolve.
    """
    from recon_gen.common.drill import ColumnShape
    from recon_gen.common.dataset_contract import (
        ColumnSpec,
        DatasetContract,
        isolated_dataset_registries,
        register_contract,
    )
    from recon_gen.common.tree.structure import Dataset
    from recon_gen.common.tree import (
        AUTO,
        Drill,
        Drillable,
        DrillParam,
    )
    from recon_gen.common.tree.visuals import Table
    from recon_gen.common.ids import ParameterName, VisualId

    with isolated_dataset_registries():
        ds = Dataset(
            identifier="da-deco-ds",
            arn="arn:aws:quicksight:::dataset/da-deco-ds",
        )
        register_contract(
            ds.identifier,
            DatasetContract(columns=[
                ColumnSpec("account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
                ColumnSpec("transfer_id", "STRING", shape=ColumnShape.TRANSFER_ID),
            ]),
        )
        col_account = Dim(dataset=ds, field_id="f-acct", column="account_id")
        col_transfer = Dim(dataset=ds, field_id="f-tx", column="transfer_id")
        param_account = DrillParam(
            name=ParameterName("pAcct"), shape=ColumnShape.ACCOUNT_ID,
        )
        param_transfer = DrillParam(
            name=ParameterName("pTx"), shape=ColumnShape.TRANSFER_ID,
        )
        # account_id carries a CLICK drill -> "accent".
        # transfer_id carries a MENU drill  -> "accent-menu".
        table = Table(
            visual_id=VisualId("v-tbl"),
            title="Decoration mix",
            subtitle="t",
            columns=[col_account, col_transfer],
            actions=[
                Drill(
                    writes=[(param_account, col_account)],
                    name="Walk to account",
                    trigger="DATA_POINT_CLICK",
                    action_id="act-1",
                    target_sheet=AUTO,
                ),
                Drill(
                    writes=[(param_transfer, col_transfer)],
                    name="View transfer downstream",
                    trigger="DATA_POINT_MENU",
                    action_id="act-2",
                    target_sheet=AUTO,
                ),
            ],
            conditional_formatting=[
                Drillable(on=col_account, color="#000000"),
                Drillable(on=col_transfer, color="#000000"),
            ],
        )
        _labels, _formats, _hidden, decoration = _table_column_meta(
            table, ds.identifier,
        )
        assert decoration == {
            "account_id": "accent",
            "transfer_id": "accent-menu",
        }


def test_shape_table_forwards_decoration_to_column_payload() -> None:
    """Phase DA — `shape_table` emits the per-column `"decoration"` key
    when `column_decoration` is supplied. The renderer reads this from
    `col.decoration` and maps "accent" / "accent-menu" to CSS classes.
    Columns without a decoration entry omit the key entirely so the
    JSON stays tight."""
    shaped = shape_table(
        rows=[],
        columns=["account_id", "transfer_id", "amount"],
        column_decoration={
            "account_id": "accent",
            "transfer_id": "accent-menu",
        },
    )
    cols = {c["name"]: c for c in shaped["columns"]}
    assert cols["account_id"]["decoration"] == "accent"
    assert cols["transfer_id"]["decoration"] == "accent-menu"
    # `amount` has no decoration -> key absent (NOT None / empty).
    assert "decoration" not in cols["amount"]


def test_l1_overdraft_account_resolves_to_accent_menu() -> None:
    """Anti-regression for the operator-flagged Overdraft bug class:
    the drill-source column on the Overdraft sheet's Violations table
    carries a Drillable + a DATA_POINT_MENU drill writing from it, so
    ``_table_column_meta`` must resolve it to "accent-menu" (tint
    background — the cue that subsumes plain accent text).

    DL.3 moved the drill source from raw ``account_id`` (which Daily
    Statement's WHERE clause couldn't match against the display-format
    binding) to ``account_display`` (the composite ``"<name> (<id>)"``
    shape Daily Statement's MappedDataSetParameters expects). The
    accent-menu decoration follows the drill source, so this assertion
    now checks ``account_display``."""
    app = _build_app("l1_dashboard")
    assert app.analysis is not None

    found = False
    for sheet in app.analysis.sheets:
        for visual in sheet.visuals:
            if type(visual).__name__ != "Table":
                continue
            title = getattr(visual, "title", "")
            if title != "Overdraft Violations":
                continue
            ds_id = _find_visual_dataset_identifier(visual)
            _l, _f, _h, decoration = _table_column_meta(visual, ds_id)
            assert decoration.get("account_display") == "accent-menu", (
                f"Overdraft Violations: account_display decoration was "
                f"{decoration.get('account_display')!r}; expected "
                f"'accent-menu' since the menu drill writes from it. "
                f"Full decoration map: {decoration}"
            )
            found = True
    assert found, "Overdraft Violations Table not found on L1 app"
