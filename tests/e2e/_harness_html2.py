"""X.2.a.5 — HTML2 (HTMX dialect) e2e harness.

Mirrors the QS harness shape (``_harness_browser`` + ``_harness_*``)
against the self-hosted Starlette + d3 dashboard server instead of
QuickSight. Same Layer-1 + Layer-2 split:

- **Layer 1 (renderer-agnostic).** A pluggable ``DataFetcher``
  callable returns the visual's data for a given (visual_id,
  params) tuple. Tests assert against the fetcher's output as the
  ground truth — what the rendered DOM must reflect.

- **Layer 2 (HTMX dialect).** A Starlette server runs against an
  ephemeral port; Playwright (WebKit, headless — same browser as
  the QS harness) drives it. Tests assert SVG carries the
  structure Layer 1 promised: rect-per-node, path-per-link, etc.

Different selectors from the QS harness — ``data-visual-kind`` +
d3-rendered SVG instead of QS's ``data-automation-id`` — same
gate shape. Lets the same tree ``Sheet`` + visual primitives get
verified against both dialects when the X.2.a.5 cross-dialect
parity tests land.

No AWS, no embed URL, no QuickSight identity region. Just uvicorn
+ Playwright. Fast enough that gating behind ``QS_GEN_E2E=1`` is
the only e2e-suite concession — local devs setting that env var
get the dialect parity tests in the same run as the QS tests.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import uvicorn

from quicksight_gen.common.html.server import DataFetcher, make_app
from quicksight_gen.common.tree.structure import App, Sheet


@contextmanager
def html2_server(
    *,
    tree_app: App,
    sheet: Sheet,
    data_fetcher: DataFetcher,
    dev_log: bool = False,
    startup_timeout_s: float = 5.0,
) -> Iterator[str]:
    """Run an App2 Starlette server on an ephemeral port.

    Yields the bound URL. Tears down on context exit.

    Args:
        tree_app: Tree ``App`` owning the Sheet.
        sheet: Sheet to serve at ``/``.
        data_fetcher: Layer-1 source-of-truth callable invoked on
            every POST to ``/visual/{id}/data``.
        dev_log: when True, the server prints HTMX + d3 click
            events forwarded from the browser. Off by default;
            harness debug runs flip it on.
        startup_timeout_s: how long to wait for uvicorn to bind.
            Beyond this, raise — it usually means a port-conflict
            or a cfg / sheet wiring error caught at app build time.

    The server runs in a daemon thread so a test exception always
    tears it down at process exit, even if the context manager's
    ``finally`` is bypassed (e.g., by ``os._exit`` in a debugger).
    """
    asgi = make_app(
        tree_app=tree_app, sheet=sheet,
        data_fetcher=data_fetcher, dev_log=dev_log,
    )
    config = uvicorn.Config(
        asgi, host="127.0.0.1", port=0, log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + startup_timeout_s
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"App2 uvicorn failed to start within {startup_timeout_s}s"
            )
        time.sleep(0.05)
    sock = server.servers[0].sockets[0]
    port = sock.getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def trigger_initial_swap(page: Any) -> None:
    """Click the Refresh button to fire the first HTMX swap.

    The page shell renders an empty placeholder div for each
    visual; the chart-data script only arrives via the swap. This
    helper mirrors the QS harness's "wait for visual to render"
    pattern but for the HTMX dialect — without this click, no
    SVG ever appears.
    """
    page.click("button[hx-post]")


def visual_section(page: Any, kind: str) -> Any:
    """Return the Playwright locator for a visual section by kind.

    ``kind`` matches the visual class name (``Sankey`` / ``ForceGraph`` /
    ``KPI`` / ``Table`` / ``BarChart`` / ``LineChart``) — the same
    string that ends up on ``data-visual-kind`` after render.py
    projects the tree.
    """
    return page.locator(f'section[data-visual-kind="{kind}"]')


def visual_svg(page: Any, kind: str) -> Any:
    """SVG locator inside a visual section. Convenience for
    ``visual_section(...).locator("svg")``."""
    return visual_section(page, kind).locator("svg")


def assert_layer2_sankey_shape(
    sankey_svg: Any, *, expected_nodes: int, expected_links: int,
    diag: str = "",
) -> None:
    """Layer 2 assertion: SVG carries N rects + M paths.

    The d3-sankey hydrator renders one ``<rect>`` per node + one
    ``<path>`` per link. Counts match the Layer 1 fetcher's
    ``len(nodes)`` / ``len(links)`` for the same params.

    ``diag`` is appended to the assertion message — useful when
    a parametrized test wants to identify which scenario failed.
    """
    actual_rects = sankey_svg.locator("rect").count()
    actual_paths = sankey_svg.locator("path").count()
    assert actual_rects == expected_nodes, (
        f"Layer 2 (HTMX): expected {expected_nodes} rects "
        f"(one per node from Layer 1), got {actual_rects}. {diag}"
    )
    assert actual_paths == expected_links, (
        f"Layer 2 (HTMX): expected {expected_links} paths "
        f"(one per link from Layer 1), got {actual_paths}. {diag}"
    )


def make_recording_fetcher(
    response: dict[str, Any],
) -> tuple[DataFetcher, list[tuple[str, dict[str, str]]]]:
    """Build a fetcher that returns ``response`` and records calls.

    Returns ``(fetcher, calls)``. Tests assert on ``calls`` to verify
    the swap pipeline forwarded the expected (visual_id, params)
    tuple. Useful for date-filter / anchor tests where the test
    cares more about the *what was requested* contract than the
    response shape (the response is a known fixture).
    """
    calls: list[tuple[str, dict[str, str]]] = []

    def fetcher(visual_id: str, params: dict[str, str]) -> dict[str, Any]:
        calls.append((visual_id, dict(params)))
        return response

    return fetcher, calls
