"""CS.11 — pin the Distinct Senders KPI's binding shape.

Investigation #241 documents an App2-side rendering bug: the "Distinct
Senders (Union)" KPI on the Recipient Fanout sheet renders as `None`
instead of a number. The audit comment guesses "column-name or
scalar-vs-row shape mismatch between the KPI's Measure field and the
SQL projection," but the actual root cause needs a live App2 +
seeded-DB reproduction to confirm (see CS.11.followup ticket).

This test pins the BINDING side of the contract at unit tier so a
future investigator has a known-good baseline:

1. The KPI's value is a single ``Measure`` of kind ``distinct_count``.
2. The Measure's column resolves to ``sender_account_id`` — a real
   column on ``RECIPIENT_FANOUT_CONTRACT``.
3. The matching "Qualifying Recipients" KPI uses the SAME
   distinct_count binding shape but a different column
   (``recipient_account_id``). If the rendering bug is binding-shape-
   specific, both KPIs would fail; the fact that only one fails in
   production rules out a generic distinct_count regression and
   points the investigation at column-name resolution or sender-side
   matview projection.

If a future fix lands, extend this test to assert the rendered
shape_kpi payload's `value` is a non-None integer.
"""

from __future__ import annotations

from typing import Any

from recon_gen.apps.investigation.app import build_investigation_app
from recon_gen.apps.investigation.datasets import (
    RECIPIENT_FANOUT_CONTRACT,
    build_all_datasets,
)
from recon_gen.common.l2 import default_l2_instance
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from recon_gen.common.tree.calc_fields import resolve_column
from recon_gen.common.tree.fields import Measure
from recon_gen.common.tree.structure import App
from recon_gen.common.tree.visuals import KPI
from tests._test_helpers import make_test_config


def _find_kpi_by_title(app: App, title: str) -> KPI | None:
    analysis = app.analysis
    assert analysis is not None, "app.analysis is None after build_investigation_app"
    for sheet in analysis.sheets:
        for visual in sheet.visuals:
            if isinstance(visual, KPI) and visual.title == title:
                return visual
    return None


def _solo_measure(kpi: KPI) -> Measure:
    assert len(kpi.values) == 1
    val: Any = kpi.values[0]  # noqa: ANN401 — KPI.values is a heterogeneous union (Measure | CalcField wrap)
    assert isinstance(val, Measure), (
        f"KPI {kpi.title!r} value is {type(val).__name__}, not Measure — "
        f"binding-shape change since the test was written."
    )
    return val


def test_distinct_senders_kpi_binds_to_sender_account_id() -> None:
    """The Distinct Senders (Union) KPI's first value measure is
    ``distinct_count(sender_account_id)``. Pins the binding shape so a
    future column rename / wrong-field regression surfaces at unit tier."""
    cfg = make_test_config(db_table_prefix=DEFAULT_PREFIX)
    inst = default_l2_instance()
    build_all_datasets(cfg, inst)
    app = build_investigation_app(cfg, l2_instance=inst)

    kpi = _find_kpi_by_title(app, "Distinct Senders (Union)")
    assert kpi is not None, (
        "Distinct Senders KPI vanished from Recipient Fanout sheet — "
        "either renamed (update the lookup) or deleted entirely."
    )
    measure = _solo_measure(kpi)
    assert measure.kind == "distinct_count", (
        f"Distinct Senders KPI should use distinct_count aggregation; "
        f"got kind={measure.kind!r}. A change here is operator-visible "
        f"(the KPI's headline number would shift meaning)."
    )
    column_name = resolve_column(measure.column)
    assert column_name == "sender_account_id", (
        f"Distinct Senders KPI must count distinct sender_account_id; "
        f"got column={column_name!r}. Counting a different "
        f"column would silently produce a wrong number."
    )


def test_recipients_with_n_kpi_binds_to_recipient_account_id() -> None:
    """The sibling "Recipients with N+ Senders" KPI uses the same
    distinct_count shape on a different column. If only Distinct Senders
    breaks at render time, the regression isn't binding-shape-specific —
    rules out a generic distinct_count bug."""
    cfg = make_test_config(db_table_prefix=DEFAULT_PREFIX)
    inst = default_l2_instance()
    build_all_datasets(cfg, inst)
    app = build_investigation_app(cfg, l2_instance=inst)

    kpi = _find_kpi_by_title(app, "Qualifying Recipients")
    assert kpi is not None
    measure = _solo_measure(kpi)
    assert measure.kind == "distinct_count"
    assert resolve_column(measure.column) == "recipient_account_id"


def test_both_kpi_columns_declared_in_contract() -> None:
    """Both KPI columns must be declared on RECIPIENT_FANOUT_CONTRACT
    — if either disappears, the qs_inner Oracle wrapper emits
    ORA-00904 (the CR.3-hotfix regression class) and BOTH renderers
    return broken output."""
    declared = {c.name for c in RECIPIENT_FANOUT_CONTRACT.columns}
    assert "sender_account_id" in declared, (
        "sender_account_id must be declared on RECIPIENT_FANOUT_CONTRACT "
        "for the Distinct Senders KPI binding to resolve."
    )
    assert "recipient_account_id" in declared, (
        "recipient_account_id must be declared on RECIPIENT_FANOUT_CONTRACT "
        "for the Recipients with N+ Senders KPI binding to resolve."
    )
