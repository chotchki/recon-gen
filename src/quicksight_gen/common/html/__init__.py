"""X.2 — HTML renderer for the dashboard tree.

spike.1 deliverable: ``emit_html(app, sheet)`` projects a tree
``Sheet`` to a complete HTML page (title, description, one
``<section>`` per visual carrying title + subtitle + a swap target).

spike.2 layers the interactive surface:

- HTMX + d3 + d3-sankey from CDN, plus a bootstrap script that
  hydrates ``data-visual-kind`` fragments after every swap.
- Page-level date-range form whose changes fire ``hx-post`` to
  ``/visual/<visual_id>/data`` for each visual on the sheet.
- ``emit_visual_data_fragment(visual_id, data)`` produces the swap
  fragment the Starlette server returns from the data endpoint.
- ``server.make_app(...)`` wires Starlette routes around a
  pluggable data fetcher (mock or real DB).

Per the X.2 design constraint: rendering functions take tree nodes
as parameters — never load from disk inside. This preserves the
X.4 stateful editor future where the tree lives in a per-session
in-memory object.

spike.2 does NOT include: authentication (X.2.phase.2), embedding,
caching, or generic chart-kind dispatch beyond ``Sankey`` (one
``case`` arm in the bootstrap; new kinds add one each).
"""

from quicksight_gen.common.html.render import (
    emit_html,
    emit_visual_data_fragment,
)

__all__ = ["emit_html", "emit_visual_data_fragment"]
