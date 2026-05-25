"""AI.2.d.2 — uvicorn-on-ephemeral-port harness for the studio editor.

Parallel to ``_harness_html2.html2_server``, but bound to the studio
editor app (``build_editor_app``) rather than App2's dashboards. Used
by ``test_studio_dogfood_browser.py`` to drive the real HTML form via
Playwright/WebKit — proves the operator-facing path (HTML render +
inline JS + browser form-submit encoding + 303 redirect) end-to-end,
which the ``TestClient``-based HTTP transport can't.

Why a separate harness: ``html2_server`` wires App2's dashboards
fetcher pipeline (data fetchers, options fetchers, FilterSpecs), all
irrelevant to the editor's create/edit/save flow. A minimal editor-
only harness keeps the AI.2.d.2 surface tight + avoids accidentally
exercising dashboards code paths the dogfood claim doesn't care about.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from collections.abc import Iterator

import uvicorn


@contextmanager
def studio_editor_server(
    asgi_app: object, startup_timeout_s: float = 5.0,
) -> Iterator[str]:
    """Run a Starlette / ASGI app on an ephemeral port. Yields the
    bound URL; tears down on context exit.

    ``asgi_app`` is whatever ``build_editor_app(cache)`` returns —
    the harness doesn't know about L2 or caches, just that it's an
    ASGI app to serve.

    Daemon thread + ``server.should_exit`` shutdown so a test
    exception always tears down at process exit even if the
    context manager's ``finally`` is bypassed.
    """
    config = uvicorn.Config(
        asgi_app,  # type: ignore[arg-type]: ASGI Protocol — Starlette satisfies it structurally
        host="127.0.0.1", port=0, log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + startup_timeout_s
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"studio-editor uvicorn failed to start within "
                f"{startup_timeout_s}s"
            )
        time.sleep(0.05)  # typing-smell: ignore[no-sleep]: 50ms uvicorn-startup poll; server.started has no awaitable
    sock = server.servers[0].sockets[0]
    port = sock.getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
