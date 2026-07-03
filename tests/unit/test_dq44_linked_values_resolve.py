"""DQ.4.4 (reframed) — every dropdown's ``options_column`` resolves to a
column its ``options_dataset`` actually produces.

The bulk of the "route ColumnSpec literals through DbObject" scope the
phase originally imagined is now covered by the DQ.5 lineage lint (contract
== SQL projection, every SQL ref resolves) + DQ.4.2b (``contract_from``).
The one surface those DON'T reach is the picker option source: a
``LinkedValues`` dropdown carries a bare ``column_name`` string naming a
column in its source ``Dataset``, consumed straight into the server's
``SELECT DISTINCT <col> FROM (<dataset sql>)`` fetch. ``LinkedValues.from_column``
validates it at construction (``Dataset.__getitem__`` against the registered
contract), but the ``from_string`` escape hatch and the direct
``options_column=`` spec paths don't — a rename of the referenced column
breaks the picker silently at query time.

This walks every built app's ``LinkedValues`` and resolves its column
through the same ``Dataset.__getitem__`` contract check, so a drifted
picker column fails at the unit tier. (Datasets with no registered contract
resolve unchecked — the irreducible escape hatch; they're counted so the
covered surface can't silently shrink to zero.)
"""
from __future__ import annotations

from recon_gen.apps.executives.app import build_executives_app
from recon_gen.apps.investigation.app import build_investigation_app
from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app
from recon_gen.apps.l2_flow_tracing.app import build_l2_flow_tracing_app
from recon_gen.common.l2 import default_l2_instance
from recon_gen.common.tree import App
from recon_gen.common.tree.controls import LinkedValues
from tests._test_helpers import make_test_config


def _all_apps() -> list[tuple[str, App]]:
    cfg = make_test_config()
    l2 = default_l2_instance()
    return [
        ("l1_dashboard", build_l1_dashboard_app(cfg, l2_instance=l2)),
        ("l2_flow_tracing", build_l2_flow_tracing_app(cfg, l2_instance=l2)),
        ("investigation", build_investigation_app(cfg, l2_instance=l2)),
        ("executives", build_executives_app(cfg, l2_instance=l2)),
    ]


def _linked_values(app: App) -> list[LinkedValues]:
    out: list[LinkedValues] = []
    assert app.analysis is not None, f"{app.name} has no analysis to walk"
    for sheet in app.analysis.sheets:
        for ctrl in getattr(sheet, "parameter_controls", ()):
            sv = getattr(ctrl, "selectable_values", None)
            if isinstance(sv, LinkedValues):
                out.append(sv)
    return out


def test_dq44_linked_values_columns_resolve_to_their_dataset_contract() -> None:
    """Every ``LinkedValues.column_name`` is a real column of its source
    dataset's contract — the ``options_column`` rename-gate (DQ.4.4)."""
    problems: list[str] = []
    resolved = 0
    for app_name, app in _all_apps():
        for sv in _linked_values(app):
            try:
                sv.dataset[sv.column_name]  # Dataset.__getitem__ contract check
                resolved += 1
            except KeyError as exc:
                # "no contract" is the irreducible escape hatch; a genuine
                # "not in dataset ... contract" is the drift we're catching.
                if "not in dataset" in str(exc).lower():
                    problems.append(
                        f"{app_name}: {sv.dataset.identifier}.{sv.column_name} "
                        f"— {exc}"
                    )
    assert not problems, (
        "picker options_column columns that don't exist in their source "
        "dataset's contract (DQ.4.4):\n" + "\n".join(problems)
    )
    # Floor guard — the covered surface can't silently drop to zero (e.g. a
    # refactor that stops registering picker contracts). 32 at time of writing.
    assert resolved >= 25, (
        f"only {resolved} LinkedValues resolved through a contract "
        f"(expected ~32) — picker contract registration may have regressed"
    )
