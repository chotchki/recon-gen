"""X.2.spike.2 — manual smoke runner for the HTML dashboard server.

Builds a minimal Money-Trail-shaped tree ``App`` + ``Sheet`` with one
``Sankey`` visual, wires a stub data fetcher that returns
deterministic d3-sankey-shaped data (responsive to date params), and
runs uvicorn on http://127.0.0.1:8765.

    .venv/bin/python -m quicksight_gen.common.html

The smoke runner intentionally does NOT touch a database — spike.2
validates the swap-on-mutation pattern + d3 hydration, not the
DB-to-d3 pipeline. Phase.1 swaps the stub for a real Money Trail
query against ``<prefix>_inv_money_trail_edges``.

Browser checklist (per CLAUDE.md "use the feature in a browser"):

1. Open http://127.0.0.1:8765/.
2. The Sankey renders on initial page load.
3. Type any date in either input — after 200ms debounce the
   "Refresh" button's HTMX trigger fires, server returns a
   different fragment, d3 re-renders the Sankey.
4. Confirm the swap is fast (<100ms server-side; total <300ms
   including d3 redraw).
5. Confirm the JSON in the swap fragment differs based on the
   date inputs (proves the fetcher's params plumbing).
"""

from __future__ import annotations

import sys
from typing import Any

import uvicorn

from quicksight_gen.common.html.server import make_app
from quicksight_gen.common.ids import SheetId, VisualId
from quicksight_gen.common.tree.structure import Analysis, App, Sheet
from quicksight_gen.common.tree.visuals import Sankey
from tests._test_helpers import make_test_config


def _build_smoke_app() -> tuple[App, Sheet]:
    cfg = make_test_config()
    app = App(name="x2-spike2-smoke", cfg=cfg)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="smoke-analysis",
        name="Spike 2 Smoke",
    ))
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("money-trail"),
        name="MoneyTrail",
        title="Money Trail",
        description=(
            "Spike 2: pick a date range and watch the Sankey re-hydrate "
            "via HTMX swap + d3 from the swapped fragment."
        ),
    ))
    sheet.visuals.append(Sankey(
        title="Money Trail — Chain Sankey",
        subtitle=(
            "Stub data; phase.1 wires this to the real "
            "<prefix>_inv_money_trail_edges matview."
        ),
        visual_id=VisualId("smoke-sankey"),
    ))
    return app, sheet


def _stub_money_trail_fetcher(
    visual_id: str, params: dict[str, str],
) -> dict[str, Any]:
    """Deterministic stub responsive to date params.

    Without real data the swap looks the same every time, which
    masks "is the swap actually firing?" bugs. Tying the link
    weights to a hash of the date inputs makes each form change
    visibly redraw the Sankey, proving the round-trip works
    end-to-end.
    """
    seed = sum(ord(c) for c in (params.get("date_from", "") + params.get("date_to", "")))
    base = max(10, seed % 50)
    # L1-layer: keep persona-blind. Real Money Trail Sankey labels
    # come from <prefix>_inv_money_trail_edges (source/target_display)
    # which the L2 instance's persona block populates; the spike just
    # proves the swap pattern, not the labels.
    return {
        "nodes": [
            {"name": "External Acquirer"},
            {"name": "Customer DDA"},
            {"name": "GL Control"},
            {"name": "Concentration"},
            {"name": "Funds Pool"},
        ],
        "links": [
            {"source": 0, "target": 1, "value": base * 100},
            {"source": 1, "target": 2, "value": base * 80},
            {"source": 2, "target": 3, "value": base * 60},
            {"source": 3, "target": 4, "value": base * 40},
        ],
    }


def main() -> int:
    tree_app, sheet = _build_smoke_app()
    asgi_app = make_app(
        tree_app=tree_app,
        sheet=sheet,
        data_fetcher=_stub_money_trail_fetcher,
    )
    print("Spike 2 smoke server: http://127.0.0.1:8765/")
    uvicorn.run(asgi_app, host="127.0.0.1", port=8765, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
