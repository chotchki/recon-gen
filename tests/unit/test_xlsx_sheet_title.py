"""CR.1 — Excel sheet name silent-truncation guard.

``openpyxl`` silently accepts >31-char worksheet names + writes them
to the .xlsx; Excel enforces at file-open, so two visuals diverging
after char 31 silently collide. ``ws.title = visual_id[:31]`` (pre-CR.1)
let this through. ``sheet_title_from_visual_id`` closes the hole at
the export boundary — UUIDv5 visual_ids collapse to a stable 13-char
shortform; human-readable ids ≤31 pass through (forbidden chars
sanitized); human-readable ids >31 raise ``ValueError``.

This file pairs the helper's per-shape unit tests with a tree-walk
lint that asserts every emitted visual_id across L1 / L2FT /
Investigation / Executives satisfies the contract BEFORE deploy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from recon_gen.common.html.server import (
    XLSX_SHEET_NAME_MAX,
    sheet_title_from_visual_id,
)
from recon_gen.common.ids import VisualId
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from tests._test_helpers import make_test_config

if TYPE_CHECKING:
    from recon_gen.common.tree.structure import App

_TEST_CFG = make_test_config(db_table_prefix=DEFAULT_PREFIX)


# ---------------------------------------------------------------------------
# sheet_title_from_visual_id — per-shape behavior
# ---------------------------------------------------------------------------


def test_human_readable_below_limit_passes_through() -> None:
    assert sheet_title_from_visual_id(VisualId("l1-drift-kpi")) == "l1-drift-kpi"


def test_human_readable_at_limit_passes_through() -> None:
    """31-char ids (the wall) MUST pass through unchanged — there is
    one in apps/executives/app.py (``exec-account-bar-active-by-type``)
    sitting at exactly the limit. A regression on the boundary
    would silently break that visual's XLSX export."""
    title = "x" * XLSX_SHEET_NAME_MAX
    assert sheet_title_from_visual_id(VisualId(title)) == title


def test_human_readable_above_limit_raises() -> None:
    """Human-readable id exceeding the Excel cap must raise so the
    rename surfaces at request time (defense-in-depth — the unit
    lint below catches this pre-deploy)."""
    with pytest.raises(ValueError, match="exceeds Excel's 31-char"):
        sheet_title_from_visual_id(VisualId("x" * (XLSX_SHEET_NAME_MAX + 1)))


def test_uuid_visual_id_collapses_to_stable_shortform() -> None:
    """UUIDv5 auto-derived visual_ids (36 chars; emitted by
    ``common/tree/structure.py`` for visuals authored without an
    explicit ``visual_id=``) collapse to ``<first8>-<last4>``
    (13 chars). 48 bits of entropy = ~281T collision pairs."""
    uid = VisualId("40487b0e-5911-58a3-9e65-166f6c556711")
    result = sheet_title_from_visual_id(uid)
    assert result == "40487b0e-6711"
    assert len(result) == 13
    assert len(result) <= XLSX_SHEET_NAME_MAX


def test_uuid_collapse_is_deterministic() -> None:
    uid = VisualId("abcdef01-2345-6789-abcd-ef0123456789")
    assert sheet_title_from_visual_id(uid) == sheet_title_from_visual_id(uid)


def test_forbidden_excel_chars_sanitized() -> None:
    """Excel rejects ``: \\ / ? * [ ]`` in sheet names; defense-in-
    depth replace with ``_`` so a stray colon doesn't crash the
    file at openpyxl's save step. (Unit lint should still catch
    these at deploy time so a real visual never reaches this
    branch.)"""
    assert sheet_title_from_visual_id(VisualId("kpi:total")) == "kpi_total"
    assert sheet_title_from_visual_id(VisualId("rail[1]")) == "rail_1_"
    assert sheet_title_from_visual_id(VisualId("a/b\\c?d*e")) == "a_b_c_d_e"


# ---------------------------------------------------------------------------
# Tree-walk lint — every visual across L1 / L2FT / Investigation /
# Executives must satisfy the contract BEFORE the export endpoint
# runs. CR.1 fix at the boundary is defense-in-depth; this lint is
# the loud surface where a >31-char author mistake is supposed to
# fail.
# ---------------------------------------------------------------------------


def _walk_visual_ids() -> list[tuple[str, str]]:
    """Build every real app's tree + return ``[(app_label, visual_id)]``.

    Imports are lazy so a missing optional dependency (e.g. recon_gen
    can build with a partial install) surfaces as an import error here
    rather than at module load.
    """
    from recon_gen.apps.executives.app import build_executives_app
    from recon_gen.apps.executives.datasets import (
        build_all_datasets as build_exec_datasets,
    )
    from recon_gen.apps.investigation.app import build_investigation_app
    from recon_gen.apps.investigation.datasets import (
        build_all_datasets as build_inv_datasets,
    )
    from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app
    from recon_gen.apps.l1_dashboard.datasets import (
        build_all_l1_dashboard_datasets,
    )
    from recon_gen.apps.l2_flow_tracing.app import build_l2_flow_tracing_app
    from recon_gen.apps.l2_flow_tracing.datasets import (
        build_all_l2_flow_tracing_datasets,
    )
    from recon_gen.common.l2 import default_l2_instance

    l2 = default_l2_instance()
    apps: list[tuple[str, "App"]] = []

    build_exec_datasets(_TEST_CFG)
    apps.append(("executives", build_executives_app(_TEST_CFG)))

    build_inv_datasets(_TEST_CFG, l2)
    apps.append((
        "investigation",
        build_investigation_app(_TEST_CFG, l2_instance=l2),
    ))

    build_all_l2_flow_tracing_datasets(_TEST_CFG, l2)
    apps.append((
        "l2_flow_tracing",
        build_l2_flow_tracing_app(_TEST_CFG, l2_instance=l2),
    ))

    build_all_l1_dashboard_datasets(_TEST_CFG, l2)
    apps.append((
        "l1_dashboard",
        build_l1_dashboard_app(_TEST_CFG, l2_instance=l2),
    ))

    ids: list[tuple[str, str]] = []
    for label, tree_app in apps:
        tree_app.emit_analysis()
        analysis = tree_app.analysis
        if analysis is None:
            continue
        for sheet in analysis.sheets:
            for visual in sheet.visuals:
                vid_obj = getattr(visual, "visual_id", None)
                if isinstance(vid_obj, str):
                    ids.append((label, vid_obj))
    return ids


def test_every_app_visual_id_satisfies_xlsx_sheet_title_contract() -> None:
    """CR.1 — walk every emitted visual across all 4 apps + assert
    ``sheet_title_from_visual_id`` doesn't raise. A regression that
    adds a >31-char human-readable ``visual_id="..."`` fails here
    pre-deploy, not at the operator's first XLSX click."""
    failures: list[str] = []
    for app_label, vid in _walk_visual_ids():
        try:
            sheet_title_from_visual_id(VisualId(vid))
        except ValueError as exc:
            failures.append(f"{app_label}: {vid!r} — {exc}")
    assert not failures, (
        "These visual_ids would crash ?format=xlsx at request time:\n"
        + "\n".join(f"  - {row}" for row in failures)
    )
