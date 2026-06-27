"""Class-level test: no Table column header renders as raw snake_case.

Pre-v8.5.0 Table visuals rendered the raw snake_case column name as
the header (``account_id``, ``business_day_start``, etc.). v8.5.0 wires
``ColumnSpec.human_name`` through to the per-column header so every
header is title-cased by default (with smart initialism handling:
``id`` → ``ID``, ``eod`` → ``EOD``).

This walker builds every shipped app on the tree object graph, finds
every ``Table`` visual in any sheet, and for each field-well column
resolves the dataset contract's ``human_name`` (the SAME value both
renderers stamp as the column header) and asserts it does NOT match
the raw snake_case form (all-lowercase, at least one underscore).

The residual value this guards — NOT covered elsewhere:
- ``test_html_table_parity`` pins the App2 label == ``human_name``, but
  doesn't reject a ``human_name`` that's itself snake_case.
- ``test_column_human_name`` exercises the ``_smart_title`` resolver
  (the no-``display_name`` path), which can never emit snake_case.
The ONLY way ``human_name`` matches the snake_case regex is an explicit
``ColumnSpec.display_name`` override left in snake_case form — that's
the regression this catches. Don't do that: use the plain-English form.
"""

from __future__ import annotations

import re
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
from recon_gen.common.dataset_contract import get_contract
from recon_gen.common.html._tree_fetcher import (
    _find_visual_dataset_identifier,
    _leaf_column_name,
)
from recon_gen.common.l2 import default_l2_instance
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from recon_gen.common.tree.fields import Dim, Measure
from recon_gen.common.tree.structure import App
from tests._test_helpers import make_test_config


# A label that looks like a raw snake_case column name: all lowercase,
# at least one underscore. ``_smart_title`` always emits at least one
# uppercase letter (the leading word) and replaces every underscore
# with a space, so any match is an explicit snake_case ``display_name``
# override slipping through — a regression.
_SNAKE_CASE_LABEL = re.compile(r"^[a-z]+(_[a-z0-9]+)+$")


def _build_app(app_name: str) -> App:
    """Build a real app (+ register its datasets) for ``app_name``.

    Datasets must be built first so ``get_contract`` can resolve each
    Table column's contract from the process-global registry."""
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
        items = fv if isinstance(fv, list) else [fv]  # pyright: ignore[reportUnknownVariableType]: dynamic field-well list off a visual subtype
        for item in items:  # pyright: ignore[reportUnknownVariableType]: dynamic field-well element off a visual subtype
            if isinstance(item, (Dim, Measure)):
                name = _leaf_column_name(item)
                if name is not None:
                    out.append((name, item))
    return out


def _visual_title(visual: Any) -> str:  # typing-smell: ignore[explicit-any]: every concrete Table subtype carries title: str, read dynamically
    """Read ``title`` off a tree-level visual for the failure message."""
    return str(getattr(visual, "title", "<untitled>"))


_APPS = ["l1_dashboard", "executives", "investigation", "l2_flow_tracing"]


@pytest.mark.parametrize("app_name", _APPS)
def test_no_table_column_header_renders_as_snake_case(app_name: str) -> None:
    """Class regression: no Table column header resolves to a raw
    snake_case string (e.g. ``account_id``, ``business_day_start``).

    A failure here means a column was added to a contract with an
    explicit ``display_name`` set to a snake_case string — use the
    plain-English form instead, or drop the override and let
    ``_smart_title`` derive the header.
    """
    app = _build_app(app_name)
    assert app.analysis is not None

    checked = 0
    bad: list[str] = []
    for sheet in app.analysis.sheets:
        for visual in sheet.visuals:
            if type(visual).__name__ != "Table":
                continue
            ds_id = _find_visual_dataset_identifier(visual)
            if ds_id is None:
                continue
            try:
                contract = get_contract(ds_id)
            except KeyError:
                continue
            for name, _leaf in _table_leaves(visual):
                if name not in contract.column_names:
                    continue
                checked += 1
                header = contract.column(name).human_name
                if _SNAKE_CASE_LABEL.match(header):
                    bad.append(
                        f"  sheet={sheet.name!r} "
                        f"visual={_visual_title(visual)!r} "
                        f"column={name!r} header={header!r}"
                    )

    assert not bad, (
        f"App {app_name!r} has Table column headers in raw snake_case "
        f"form. Either the column's ``display_name`` override should be "
        f"plain-English, or its name shouldn't have been left as "
        f"snake_case in the first place:\n" + "\n".join(bad)
    )
    assert checked > 0, (
        f"{app_name}: no Table columns checked — the header gate is "
        f"vacuous (did the app's Table visuals or field wells change?)."
    )
