"""Phase DB.2 — App2 parity gate construction-time tests.

The gate at ``App.resolve_auto_ids()`` walks every Visual on the
analysis and asserts each dataclass field has a parity disposition
entry in ``APP2_ATTRIBUTE_REGISTRY``. Catches the DA-shape gap class
(tree adds a field, emit() lands it in QS JSON, App2 silently drops
it) at the wiring site instead of months later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import pytest

from recon_gen.common.ids import ParameterName, SheetId, VisualId
from recon_gen.common.tree import AUTO, KPI, Analysis, App, Sheet
from recon_gen.common.tree.app2_parity_registry import (
    APP2_ATTRIBUTE_REGISTRY,
    HARDCODED_EMIT_INVENTORY,
    App2Consumed,
    App2ParityGap,
    ByDesign,
    TreeOnly,
    check_app2_parity,
)
from recon_gen.common.tree.fields import Measure
from tests._test_helpers import make_test_config


def _minimal_app_with_visual(visual: object) -> App:
    """Build a one-sheet App that hosts ``visual`` so we can drive
    ``check_app2_parity(app)`` without exercising the dataset / drill
    / param validators that come BEFORE the gate in ``emit_analysis``.
    Calls the gate directly to keep failure isolation tight."""
    app = App(name="da-gate-test", cfg=make_test_config())
    analysis = app.set_analysis(
        Analysis(analysis_id_suffix="gate-test", name="Gate"),
    )
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("gate-sheet"),
        name="g", title="G", description="t",
    ))
    sheet.visuals.append(visual)  # pyright: ignore[reportArgumentType]: Visual union narrowing not load-bearing for this test
    return app


# ---------------------------------------------------------------------------
# Happy-path: known Visual kinds with only registered fields pass.
# ---------------------------------------------------------------------------


def test_kpi_with_only_registered_fields_passes_gate() -> None:
    """A vanilla KPI from the tree's built-in dataclass should pass
    the gate cleanly — every dataclass field is in the registry."""
    cfg = make_test_config()
    # Build a Measure that the KPI can hold without exercising dataset
    # resolution (we never call emit_analysis here — direct gate call).
    from recon_gen.common.tree.structure import Dataset
    ds = Dataset(identifier="gate-ds", arn="arn:aws:quicksight:::dataset/gate-ds")
    measure = Measure(dataset=ds, column="n", kind="sum")
    kpi = KPI(
        title="T", subtitle="s",
        values=[measure],
        visual_id=VisualId("v-tbl"),
    )
    del cfg
    app = _minimal_app_with_visual(kpi)
    check_app2_parity(app)  # passes


# ---------------------------------------------------------------------------
# Gate fires when a Visual carries an unregistered field.
# ---------------------------------------------------------------------------


def test_gate_fires_on_unregistered_dataclass_field() -> None:
    """A Visual class with a dataclass field that's NOT in
    APP2_ATTRIBUTE_REGISTRY raises ``App2ParityGap`` at construction.
    Simulates the DA-shape bug: an author adds ``new_emit_field`` to
    Table's dataclass, emit() picks it up, but the registry isn't
    updated — the gate raises the moment the App is wired.

    Uses a fresh fake Visual class with kind 'KPI' to land in the
    registry walk; injecting a real new field on Table would require
    monkey-patching the framework which makes the test brittle."""

    @dataclass
    class _GappyKPI:
        """A KPI-shaped Visual with one extra dataclass field that has
        no registry entry. The gate looks up entries by ``type(v).__name__``
        so we name the class to land in the KPI bucket — the registry
        has entries for KPI's standard fields but not for this synthetic
        ``unregistered_extra_field``."""
        title: str
        subtitle: str
        unregistered_extra_field: str = ""
        _AUTO_KIND: ClassVar[str] = "kpi"

    # Rename so the gate's `type(visual).__name__` lookup lands in
    # APP2_ATTRIBUTE_REGISTRY["KPI"] (which lacks the new field).
    _GappyKPI.__name__ = "KPI"
    gappy = _GappyKPI(title="T", subtitle="s")
    app = _minimal_app_with_visual(gappy)
    with pytest.raises(App2ParityGap, match=r"unregistered_extra_field"):
        check_app2_parity(app)


# ---------------------------------------------------------------------------
# Registry covers every settable dataclass field on every Visual kind.
# ---------------------------------------------------------------------------


def test_registry_covers_every_field_of_every_known_visual_kind() -> None:
    """Anti-regression: walks the real Visual dataclasses from
    ``common/tree/visuals.py`` and asserts every field landed in the
    registry. Forces an author who adds a Visual field to add the
    registry entry in the same commit (otherwise: this test fails)."""
    from dataclasses import fields as dc_fields
    from recon_gen.common.tree import visuals as v
    visual_classes = {
        "KPI": v.KPI,
        "Table": v.Table,
        "BarChart": v.BarChart,
        "LineChart": v.LineChart,
        "Sankey": v.Sankey,
        "ForceGraph": v.ForceGraph,
    }
    missing: list[tuple[str, str]] = []
    for kind, cls in visual_classes.items():
        if kind not in APP2_ATTRIBUTE_REGISTRY:
            missing.append((kind, "<entire kind missing from registry>"))
            continue
        entries = APP2_ATTRIBUTE_REGISTRY[kind]
        for f in dc_fields(cls):
            if f.name.startswith("_"):
                continue
            if f.name not in entries:
                missing.append((kind, f.name))
    assert not missing, (
        f"APP2_ATTRIBUTE_REGISTRY is missing entries for: {missing}. "
        f"Add them to src/recon_gen/common/tree/app2_parity_registry.py "
        f"with the right disposition (App2Consumed / TreeOnly / ByDesign)."
    )


# ---------------------------------------------------------------------------
# Registry shape sanity.
# ---------------------------------------------------------------------------


def test_registry_entries_use_only_typed_dispositions() -> None:
    """Each registry entry is one of the three typed dispositions.
    Catches accidental strings / dicts that would slip the type
    discipline."""
    valid = (App2Consumed, TreeOnly, ByDesign)
    for kind, entries in APP2_ATTRIBUTE_REGISTRY.items():
        for field_name, entry in entries.items():
            assert isinstance(entry, valid), (
                f"APP2_ATTRIBUTE_REGISTRY[{kind!r}][{field_name!r}] is "
                f"{type(entry).__name__}; expected App2Consumed / "
                f"TreeOnly / ByDesign."
            )


def test_hardcoded_emit_inventory_is_non_empty_and_well_formed() -> None:
    """The hardcoded-emit inventory captures emit() literal hardcodes
    that don't trace to a dataclass field. Operator-locked at DB.0 —
    one-time enumeration, doesn't grow per-Visual."""
    assert HARDCODED_EMIT_INVENTORY, "hardcoded-emit inventory unexpectedly empty"
    valid = (App2Consumed, TreeOnly, ByDesign)
    for hc in HARDCODED_EMIT_INVENTORY:
        assert hc.visual in APP2_ATTRIBUTE_REGISTRY, (
            f"hardcoded emit on unknown Visual kind {hc.visual!r}"
        )
        assert hc.emit_path, "emit_path empty"
        assert isinstance(hc.disposition, valid), (
            f"hardcoded {hc.visual}.{hc.emit_path} disposition is "
            f"{type(hc.disposition).__name__}; expected typed."
        )


# ---------------------------------------------------------------------------
# Gate is idempotent (re-runs are no-ops once registry is satisfied).
# ---------------------------------------------------------------------------


def test_gate_is_idempotent_on_clean_app() -> None:
    from recon_gen.common.tree.structure import Dataset
    ds = Dataset(identifier="idem-ds", arn="arn:aws:quicksight:::dataset/idem-ds")
    measure = Measure(dataset=ds, column="n", kind="sum")
    kpi = KPI(
        title="T", subtitle="s",
        values=[measure],
        visual_id=VisualId("v-tbl"),
    )
    app = _minimal_app_with_visual(kpi)
    check_app2_parity(app)
    check_app2_parity(app)  # re-run is also fine


# AUTO + ParameterName imports survive the test module's pyright sweep
# even when the symbols are unused in this file — keep the import set
# stable across the conftest's collection-time pyright pass.
del AUTO, ParameterName
