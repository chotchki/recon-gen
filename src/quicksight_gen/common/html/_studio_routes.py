"""Studio route builder (X.4.a.4 placeholder; expands across X.4.c-g).

``make_studio_routes(cache)`` returns the ``Route``/``Mount`` list that
``cli.studio`` splices into ``make_app(... studio_routes=...)``. Each
returned route closes over the supplied ``L2InstanceCache`` so Studio's
read/write paths share one in-memory instance per server.

X.4.a.4 ships ONLY the landing placeholder (``GET /``) — the unified
diagram (X.4.c), the editor routes (``/l2_shape/...`` X.4.e), the
data-shaping routes (``/data/...`` X.4.h), and the orchestration endpoint
(``POST /deploy`` X.4.g) all hang off this same builder later.

Severability: this module is Studio-only. ``cli.dashboards`` calls
``make_app`` with ``studio_routes=None`` and never imports this file.
"""

from __future__ import annotations

from html import escape

from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Mount, Route

from quicksight_gen.common.l2.cache import L2InstanceCache


def _render_landing_placeholder(cache: L2InstanceCache) -> str:
    """Minimal landing page proving the mount + cache wiring resolve.

    Replaced by the real Studio landing once X.4.c (unified diagram)
    + X.4.e (editor list) land. Carries the L2 instance prefix so a
    deploy mistake (wrong YAML wired) is visible in the page body.
    """
    instance = cache.get()
    prefix = escape(str(instance.instance))
    accounts_n = len(instance.accounts)
    rails_n = len(instance.rails)
    chains_n = len(instance.chains)
    templates_n = len(instance.transfer_templates)
    return (
        "<!doctype html>\n"
        "<html lang=\"en\"><head>\n"
        f"<title>Studio — {prefix}</title>\n"
        "<meta charset=\"utf-8\">\n"
        "</head><body>\n"
        f"<h1>Studio</h1>\n"
        f"<p>L2 instance: <code>{prefix}</code></p>\n"
        "<ul>\n"
        f"<li>Accounts: {accounts_n}</li>\n"
        f"<li>Rails: {rails_n}</li>\n"
        f"<li>Chains: {chains_n}</li>\n"
        f"<li>Templates: {templates_n}</li>\n"
        "</ul>\n"
        "<p><a href=\"/dashboards\">→ Dashboards</a></p>\n"
        "<p><em>Studio is a placeholder; the unified diagram + editor "
        "land in X.4.c through X.4.g.</em></p>\n"
        "</body></html>\n"
    )


def make_studio_routes(cache: L2InstanceCache) -> list[Route | Mount]:
    """Build the Studio route list bound to ``cache``.

    Spliced into ``make_app(..., studio_routes=...)`` BEFORE the
    Dashboards routes so Studio's ``GET /`` overrides the
    ``GET / → /dashboards`` redirect that ``make_app`` installs in
    Dashboards-only mode.
    """
    async def landing(_request: Request) -> HTMLResponse:
        return HTMLResponse(_render_landing_placeholder(cache))

    return [Route("/", landing, methods=["GET"])]
