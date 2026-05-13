"""X.2.p — App 2's third-party browser libs are vendored, not CDN-loaded.

Two guards:

1. **SHA256-lock** — every dep in
   ``common/html/assets/vendor/vendor.lock`` has a committed file at its
   ``dest`` whose SHA256 matches the lock. A stale or hand-edited
   vendored file fails loudly; re-vendor with ``python
   scripts/vendor_js_deps.py --update`` when a version bump is
   intentional. (Same model as ``tests/data/_locked_seeds/`` +
   ``test_locked_seeds.py``.)
2. **No external runtime asset** — the page shell loads every
   ``<script>`` / ``<link>`` from a local ``/static/...`` path; nothing
   is fetched from a CDN at runtime. This *is* the offline guarantee
   for ``pip install quicksight-gen[serve] && quicksight-gen serve app2
   apply`` — as a fast unit test (the CI ``docs-portable-install`` job
   adds the "the vendored files actually land in the installed wheel"
   half).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from quicksight_gen.common.html import render
from quicksight_gen.common.html.render import emit_dashboards_list

_VENDOR_DIR = Path(render.__file__).resolve().parent / "assets" / "vendor"
_LOCK = json.loads((_VENDOR_DIR / "vendor.lock").read_text(encoding="utf-8"))
_DEPS: list[dict[str, str]] = _LOCK["deps"]


@pytest.mark.parametrize("dep", _DEPS, ids=[d["dest"] for d in _DEPS])
def test_vendored_file_matches_lock(dep: dict[str, str]) -> None:
    """The committed file at ``dest`` exists and its SHA256 matches the lock."""
    path = _VENDOR_DIR / dep["dest"]
    assert path.is_file(), (
        f"vendored file {dep['dest']!r} is missing — run "
        f"`python scripts/vendor_js_deps.py --update`"
    )
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    assert actual == dep["sha256"], (
        f"{dep['dest']!r}: SHA256 {actual} != locked {dep['sha256']} — if "
        f"the version bump is intentional, re-run `--update` and commit; "
        f"otherwise the file is corrupt."
    )


def test_render_module_vendor_constants_are_local() -> None:
    """``render.py``'s asset-URL constants point at ``/static/vendor/...``,
    and the referenced file is one of the vendored ones."""
    consts = {
        "_HTMX_SRC": render._HTMX_SRC,
        "_D3_SRC": render._D3_SRC,
        "_D3_SANKEY_SRC": render._D3_SANKEY_SRC,
        "_TOM_SELECT_CSS": render._TOM_SELECT_CSS,
        "_TOM_SELECT_JS": render._TOM_SELECT_JS,
        "_FLATPICKR_CSS": render._FLATPICKR_CSS,
        "_FLATPICKR_JS": render._FLATPICKR_JS,
        "_NOUISLIDER_CSS": render._NOUISLIDER_CSS,
        "_NOUISLIDER_JS": render._NOUISLIDER_JS,
        "_CTXMENU_JS": render._CTXMENU_JS,
    }
    vendored_dests = {d["dest"] for d in _DEPS}
    for name, url in consts.items():
        assert url.startswith("/static/vendor/"), f"{name} = {url!r} is not /static/vendor/..."
        rel = url[len("/static/vendor/"):]
        assert rel in vendored_dests, f"{name} = {url!r} → {rel!r} not in vendor.lock"
        assert (_VENDOR_DIR / rel).is_file(), f"{name} → {rel!r} not committed"


_EXTERNAL_ASSET_RE = re.compile(
    r'(?:src|href)\s*=\s*["\'](?:https?:)?//', re.IGNORECASE,
)


def test_page_shell_has_no_external_script_or_link() -> None:
    """The rendered page shell pulls zero JS/CSS from a remote origin —
    every ``<script src>`` / ``<link href>`` is a local ``/static/...``
    path. This is the App 2 offline contract (X.2.p)."""
    html = emit_dashboards_list([("d1", "Dashboard One"), ("d2", "Dashboard Two")])
    leaks = _EXTERNAL_ASSET_RE.findall(html)
    assert not leaks, (
        f"page shell references {len(leaks)} external asset URL(s) — App 2 "
        f"must serve all JS/CSS from /static/ so a `pip install` works "
        f"offline (X.2.p). Offenders: {leaks}"
    )
    # Belt-and-braces: the vendor blocks themselves carry only /static/.
    assert "http" not in render._VENDOR_JS, render._VENDOR_JS
    assert "http" not in render._VENDOR_CSS, render._VENDOR_CSS
