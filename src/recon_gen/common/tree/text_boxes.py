"""Tree-side wrapper for ``models.SheetTextBox`` — the rich-text box
nodes used on landing-page sheets (Getting Started).

QuickSight splits a sheet's contents into ``Visuals`` and ``TextBoxes``
in the underlying model, but at the layout level both occupy
``GridLayoutElement`` slots — only the ``ElementType`` differs
(``"VISUAL"`` vs ``"TEXT_BOX"``). The ``LayoutNode`` Protocol in
``structure.py`` abstracts over that split so callers see a single
``Sheet.place(node, ...)`` API regardless of which underlying field
the node will eventually emit into.
"""

from __future__ import annotations

from dataclasses import dataclass

from recon_gen.common.tree._helpers import GridLayoutElementType


@dataclass(eq=False)
class TextBox:
    """Tree-side rich-text box.

    Mirrors the ``KPI`` / ``Table`` / ``BarChart`` / ``Sankey`` typed
    wrapper pattern: callers construct typed nodes; the tree owns
    layout / id resolution / emission. Compose ``content`` via the
    ``common.rich_text`` helpers (``rt.text_box(...)``) and pass the
    string in.

    ``text_box_id`` is required (no auto-ID for text boxes — they
    don't carry a ``_AUTO_KIND``). The ID surfaces in the layout's
    ``ElementId`` and the sheet's ``TextBoxes`` list.
    """
    text_box_id: str
    content: str

    @property
    def element_id(self) -> str:
        return self.text_box_id

    @property
    def element_type(self) -> GridLayoutElementType:
        return "TEXT_BOX"

