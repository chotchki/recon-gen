"""AO.R.5 — App2 table column parity gate.

The smell this guards (operator-named 2026-05-21): a presentation field
declared once on the shared contract (``ColumnSpec.human_name`` header,
``currency`` measure format) used to land in QuickSight *only* — App2's
``shape_table`` emitted bare ``[{"name"}]`` and the d3 renderer fell back
to the raw snake_case SQL column name. AO.R.1 threads the same per-column
label + format QS derives through the App2 fetcher → ``shape_table``.

This test pins that parity end-to-end for every Table visual in all four
bundled apps: it runs the real fetcher-side derivation (``_table_column_meta``)
through ``shape_table`` and asserts each emitted column carries

- ``label`` == the dataset contract's ``human_name`` (the SAME value QS's
  ``_field_label`` stamps as the column ``CustomLabel``) — pinned to the
  contract, not to the deriver, so a regression in either side fails here;
- ``format`` == ``"currency"`` for every column bound to a ``currency=True``
  measure/dim.

A QS-only contract field (header / currency) that App2 drops can no longer
ship silently.
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
from recon_gen.common.tree.fields import Dim, Measure
from recon_gen.common.tree.structure import App
from tests._test_helpers import make_test_config


def _build_app(app_name: str) -> App:
    """Build a real app (+ register its datasets) for ``app_name``."""
    cfg = make_test_config(db_table_prefix="spec_example")
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
            labels, formats = _table_column_meta(visual, ds_id)
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
                # must equal the contract's human_name — the exact value
                # QS stamps as the column CustomLabel.
                if contract is not None and name in contract.column_names:
                    assert col["label"] == contract.column(name).human_name, (
                        f"{app_name}: Table column {name!r} App2 label "
                        f"{col['label']!r} != contract human_name "
                        f"{contract.column(name).human_name!r} (QS header)."
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
