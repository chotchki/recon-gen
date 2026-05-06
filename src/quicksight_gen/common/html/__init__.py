"""App2 — HTML renderer for the dashboard tree.

``emit_html(app, sheet, *, dashboard_id)`` projects a tree
``Sheet`` to a complete HTML page (title, description, one
``<section>`` per visual carrying title + subtitle + a swap target).

Interactive surface:

- HTMX + d3 + d3-sankey from CDN, plus a bootstrap script that
  hydrates ``data-visual-kind`` fragments after every swap.
- All-GET REST data path (X.2.b): the date-range form's Refresh
  button + the in-chart click handlers both ``hx-get`` against
  ``/dashboards/{dashboard_id}/sheets/{sheet_id}/visuals/{visual_id}/data``
  with filter values in the query string. URL == cache key ==
  bookmark.
- ``emit_visual_data_fragment(visual_id, data)`` produces the swap
  fragment the Starlette server returns from the data endpoint.
- ``server.make_app(...)`` wires Starlette routes around a
  pluggable data fetcher (mock or real DB).

Per the X.2 design constraint: rendering functions take tree nodes
as parameters — never load from disk inside. This preserves the
X.4 stateful editor future where the tree lives in a per-session
in-memory object.

Out of scope here: authentication (deferred to backlog),
embedding, server-side caching (Cache-Control headers in X.2.b.4
push caching to edge / browser).
"""

from quicksight_gen.common.html.render import (
    emit_html,
    emit_visual_data_fragment,
)

__all__ = ["emit_html", "emit_visual_data_fragment"]
