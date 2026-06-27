"""Class-level test: no BarChart axis label renders as raw snake_case.

Pre-v8.5.5 a BarChart whose author didn't pass an explicit
``category_label`` / ``value_label`` / ``color_label`` override fell
back to the raw snake_case column name on the axis title
(``account_id``, ``signed_amount``). v8.5.5 wired the same
human_name pipeline Table column headers use: when an override is
absent, the axis label resolves to the first field-well leaf's
contract ``human_name`` (the ``display_name`` override or the
smart-titled column name).

This walker builds every shipped app, finds every tree-level
``BarChart`` node in any sheet, and for each populated axis leaf
resolves the label exactly as the renderers do:

- the visual's explicit ``category_label`` / ``value_label`` /
  ``color_label`` override when set, else
- the dataset contract's ``human_name`` for the leaf's column
  (``get_contract(ds_id).column(name).human_name`` — the same value
  QS stamps as the axis ``CustomLabel`` and App2 paints as the axis
  title), else
- the ``_field_label`` cascade for a calc-field / bare-string leaf
  (smart-title — always carries an uppercase, so it can't be snake).

It then asserts no resolved label survives in raw snake_case form. A
regression here means a new BarChart slipped through without an
override AND with a column whose ``display_name`` was left as a
snake_case string (don't do that — use the plain-English form).

DW.1 retired the two pure-QS-emit checks that previously lived here
(label-options structure presence + the ``ApplyTo`` axis binding) —
both asserted QS-API-only shapes with no tree/contract equivalent.
The no-snake_case intent is the part that survives onto the tree.
"""

from __future__ import annotations

import re
from typing import Iterator

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
from recon_gen.common.html._tree_fetcher import _leaf_column_name
from recon_gen.common.l2 import default_l2_instance
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from recon_gen.common.tree import BarChart
from recon_gen.common.tree.fields import Dim, Measure
from recon_gen.common.tree.structure import App
from recon_gen.common.tree.visuals import _field_label
from tests._test_helpers import make_test_config


# Same shape as ``test_table_column_headers.py``: all-lowercase with at
# least one underscore. The smart-title pass always produces at least
# one uppercase letter (the leading word), so any match is a regression.
_SNAKE_CASE_LABEL = re.compile(r"^[a-z]+(_[a-z0-9]+)+$")


_APPS = ["l1_dashboard", "executives", "investigation", "l2_flow_tracing"]


def _build_app(app_name: str) -> App:
    """Build a real app (+ register its datasets so contract lookups
    resolve) for ``app_name``. Mirrors ``test_html_table_parity``'s
    builder so the contract registry is populated the same way."""
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


def _contract_label(leaf: Dim | Measure) -> str:
    """The label a renderer paints for a leaf when its axis carries no
    explicit override: the contract ``human_name`` for the leaf's
    column (``get_contract(ds_id).column(name).human_name`` — the exact
    value QS stamps as the axis CustomLabel), falling back to the
    ``_field_label`` smart-title cascade for a calc-field / bare-string
    leaf that isn't a contract column."""
    name = _leaf_column_name(leaf)
    if name is not None:
        contract: DatasetContract | None
        try:
            contract = get_contract(leaf.dataset.identifier)
        except KeyError:
            contract = None
        if contract is not None and name in contract.column_names:
            return contract.column(name).human_name
    return _field_label(leaf)


def _bar_chart_axis_labels(
    app: App,
) -> Iterator[tuple[str, str, str, str]]:
    """Yield ``(sheet_name, visual_title, axis_name, resolved_label)``
    for every populated axis leaf of every tree-level ``BarChart`` in
    ``app``'s analysis. ``resolved_label`` is the axis override when set,
    else the leaf's contract ``human_name`` — exactly what renders."""
    assert app.analysis is not None, f"{app.name} has no analysis"
    for sheet in app.analysis.sheets:
        for v in sheet.visuals:
            if not isinstance(v, BarChart):
                continue
            for axis_name, override, leaves in (
                ("category", v.category_label, v.category),
                ("values", v.value_label, v.values),
                ("colors", v.color_label, v.colors),
            ):
                for leaf in leaves:
                    label = override if override is not None else _contract_label(leaf)
                    yield sheet.name, v.title, axis_name, label


@pytest.mark.parametrize("app_name", _APPS)
def test_no_bar_chart_axis_label_renders_as_snake_case(app_name: str) -> None:
    """Class regression: no axis label on any BarChart resolves to raw
    snake_case form (e.g. ``account_id``, ``signed_amount``).

    A failure here means either:
    1. A new BarChart was added with no axis-label override AND a leaf
       whose column carries no plain-English ``display_name``, OR
    2. A column was added to a contract with an explicit
       ``display_name`` set to a snake_case string (don't do that —
       use the plain-English form).
    """
    app = _build_app(app_name)
    bad: list[str] = []
    for sheet_name, visual_title, axis_name, label in _bar_chart_axis_labels(app):
        if _SNAKE_CASE_LABEL.match(label):
            bad.append(
                f"  sheet={sheet_name!r} visual={visual_title!r} "
                f"axis={axis_name} label={label!r}"
            )
    assert not bad, (
        f"App {app_name!r} has BarChart axis labels in raw snake_case "
        f"form. Either the column needs a ``display_name`` override on "
        f"its ``ColumnSpec``, or the BarChart needs an explicit "
        f"``category_label`` / ``value_label`` / ``color_label`` "
        f"override:\n" + "\n".join(bad)
    )


def test_bar_chart_axis_label_walk_is_not_vacuous() -> None:
    """Guard: the snake_case walk must actually examine BarChart axis
    labels. If every shipped app stops carrying BarChart visuals (or the
    field-well attribute names drift), the per-app test above would pass
    trivially — this pins that at least one real axis label was walked
    across the four apps.

    Scope note (honest about what's exercised): every shipped BarChart
    currently sets an explicit ``category_label`` / ``value_label`` /
    ``color_label`` override, so the override branch is what's checked
    here. The contract ``human_name`` fallback in
    ``_bar_chart_axis_labels`` is defensive for a future chart that omits
    an override — its resolver is covered separately by
    ``tests/unit/test_column_human_name.py``. So this guard pins that the
    override path is non-empty, NOT that the fallback was exercised."""
    total = sum(
        1
        for app_name in _APPS
        for _ in _bar_chart_axis_labels(_build_app(app_name))
    )
    assert total > 0, (
        "no BarChart axis labels were walked across any shipped app — "
        "the snake_case regression check is vacuous. Did the apps lose "
        "their BarChart visuals or did the field-well attribute names "
        "(category / values / colors) change?"
    )
