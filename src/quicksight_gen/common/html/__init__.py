"""X.2 — HTML renderer for the dashboard tree.

Spike.1 deliverable: takes a tree's ``Sheet`` object and produces an
HTML fragment that mirrors the sheet's structure (title, description,
one ``<section>`` per visual carrying its title + subtitle + a
data placeholder).

Per the X.2 spike.1 design constraint: rendering functions take
``tree: Sheet`` (or other tree node) as a parameter — never load
from disk inside. This preserves the X.4 stateful editor future
where the tree lives in a per-session in-memory object without
coupling the spike to it.

Spike.1 does NOT include: data fetching, charting (d3 hydration is
spike.2), filters (HTMX swap is spike.2), authentication (X.2.phase.2),
or embedding.
"""

from quicksight_gen.common.html.render import emit_html

__all__ = ["emit_html"]
