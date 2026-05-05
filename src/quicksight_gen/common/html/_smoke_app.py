"""X.2.a — smoke App2 builder + stub Money-Trail fetcher.

Lifted out of ``__main__.py`` so both the direct ``python -m`` smoke
runner and the ``quicksight-gen serve app2 apply`` CLI can share one
implementation. X.2.a.4 will introduce a real config + L2 driven
builder; until then the stub ships the same Money-Trail-shaped tree
both entry points used.

The fetcher stays a deterministic stub responsive to ``date_from``,
``date_to`` and ``anchor`` params — proves the swap pipeline without
a database. X.2.a.4 swaps the stub for a real ``DataFetcher``
factory keyed off the L2 instance + dialect.
"""

from __future__ import annotations

from typing import Any

from quicksight_gen.common.config import Config
from quicksight_gen.common.ids import SheetId, VisualId
from quicksight_gen.common.tree.structure import Analysis, App, Sheet
from quicksight_gen.common.tree.visuals import ForceGraph, Sankey


def build_smoke_app(cfg: Config) -> tuple[App, Sheet]:
    """Build a one-sheet App2 with a Money Trail Sankey + Force topology.

    Stays L1-pure: persona-blind labels, no DB lookups. The cfg is
    threaded through so the App carries the same per-instance prefix
    the QuickSight builders use — once X.2.a.4 plugs in a real
    fetcher, the same config picks the dialect / connection.
    """
    app = App(name="x2-app2-smoke", cfg=cfg)
    analysis = app.set_analysis(
        Analysis(
            analysis_id_suffix="smoke-analysis",
            name="App2 Smoke",
        )
    )
    sheet = analysis.add_sheet(
        Sheet(
            sheet_id=SheetId("money-trail"),
            name="MoneyTrail",
            title="Money Trail",
            description=(
                "Pick a date range and watch the Sankey re-hydrate via "
                "HTMX swap + d3 from the swapped fragment."
            ),
        )
    )
    sheet.visuals.append(
        Sankey(
            title="Money Trail — Chain Sankey",
            subtitle=(
                "Stub data; X.2.a.4 wires this to the real "
                "<prefix>_inv_money_trail_edges matview."
            ),
            visual_id=VisualId("smoke-sankey"),
        )
    )
    sheet.visuals.append(
        ForceGraph(
            title="Rails & Accounts — Force Layout",
            subtitle=(
                "X.4 capability test: d3-force renders an account "
                "topology like the existing graphviz pipeline does for "
                "docs. Click a node to anchor; drag-to-position is a "
                "follow-on."
            ),
            visual_id=VisualId("smoke-force"),
        )
    )
    return app, sheet


def _stub_rails_accounts() -> dict[str, Any]:
    """Stub topology shaped like ``common/l2/topology.py`` projects.

    Accounts as nodes (typed by ``account_type``), rails as undirected
    edges between them. Persona-blind labels — the X.2.a.4 real
    fetcher pulls names from the L2 instance's persona block.
    """
    return {
        "nodes": [
            {"id": "ext_acquirer",      "label": "External Acquirer",      "group": "external_counter"},
            {"id": "customer_dda_a",    "label": "Customer DDA A",         "group": "dda"},
            {"id": "customer_dda_b",    "label": "Customer DDA B",         "group": "dda"},
            {"id": "merchant_dda",      "label": "Merchant DDA",           "group": "merchant_dda"},
            {"id": "gl_control",        "label": "GL Control",             "group": "gl_control"},
            {"id": "concentration",     "label": "Concentration Master",   "group": "concentration_master"},
            {"id": "funds_pool",        "label": "Funds Pool",             "group": "funds_pool"},
        ],
        "links": [
            {"source": "ext_acquirer",   "target": "customer_dda_a"},
            {"source": "ext_acquirer",   "target": "customer_dda_b"},
            {"source": "customer_dda_a", "target": "merchant_dda"},
            {"source": "customer_dda_b", "target": "merchant_dda"},
            {"source": "merchant_dda",   "target": "gl_control"},
            {"source": "gl_control",     "target": "concentration"},
            {"source": "concentration",  "target": "funds_pool"},
            {"source": "customer_dda_a", "target": "gl_control"},
        ],
    }


def stub_money_trail_fetcher(
    visual_id: str, params: dict[str, str],
) -> dict[str, Any]:
    """Deterministic stub responsive to date + anchor params.

    Two interaction surfaces feed the stub:

    - **date_from / date_to** (form filter) — seed the link
      multipliers (primes 7/11/13/17) so date changes visibly
      shift the ratios.
    - **anchor** (clicked node name) — applies a per-link factor
      keyed off the anchor's character sum. Clicking a different
      node pivots the Sankey ratios in a fresh direction so the
      click-to-trace experiment is visible.

    Both seed and anchor are echoed into the first node label so a
    glance at any swap confirms the round-trip ran with the
    expected params (decouples "did the swap fire?" from "did the
    Sankey shape change?").
    """
    if visual_id == "smoke-force":
        return _stub_rails_accounts()
    seed = sum(
        ord(c)
        for c in (params.get("date_from", "") + params.get("date_to", ""))
    )
    anchor = params.get("anchor", "")
    anchor_factor = (sum(ord(c) for c in anchor) % 5 + 1) if anchor else 1
    label = f"seed={seed}, anchor={anchor or 'none'}"
    return {
        "nodes": [
            {"name": f"External Acquirer ({label})"},
            {"name": "Customer DDA"},
            {"name": "GL Control"},
            {"name": "Concentration"},
            {"name": "Funds Pool"},
        ],
        "links": [
            {"source": 0, "target": 1,
             "value": max(10, (seed * 7 * anchor_factor) % 100 + 10)},
            {"source": 1, "target": 2,
             "value": max(10, (seed * 11 * anchor_factor) % 100 + 10)},
            {"source": 2, "target": 3,
             "value": max(10, (seed * 13 * anchor_factor) % 100 + 10)},
            {"source": 3, "target": 4,
             "value": max(10, (seed * 17 * anchor_factor) % 100 + 10)},
        ],
    }
