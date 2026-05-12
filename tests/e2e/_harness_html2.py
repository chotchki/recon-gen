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

import logging
import os
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import uvicorn

from quicksight_gen.common.env_keys import EnvVarInvalid, QS_GEN_RUN_DIR
from quicksight_gen.common.html._tree_fetcher import OptionsFetcher
from quicksight_gen.common.html.render import FilterSpec
from quicksight_gen.common.html.server import (
    DataFetcher, ServedDashboard, make_app,
)
from quicksight_gen.common.tree.structure import App, Sheet


# Y.2.gate.c.11.app2-server-logs — loggers we route to the per-run
# log file. Only the two leaf uvicorn loggers, NOT root `uvicorn`:
# uvicorn's default LOGGING_CONFIG sets `uvicorn.error` to propagate
# (no propagate=False), so its messages bubble up to root `uvicorn`.
# Attaching our FileHandler to both would log every error twice.
# `uvicorn.access` has propagate=False so we attach to it directly.
# `quicksight_gen.app2.devlog` is the server's dev-log logger
# (`POST /log` handler in `common/html/server.py`); attaching here
# lands browser-forwarded events alongside uvicorn's access log
# when `dev_log=True` is enabled on `html2_server`.
_UVICORN_LOGGER_NAMES = (
    "uvicorn.error",
    "uvicorn.access",
    "quicksight_gen.app2.devlog",
)


def _attach_app2_log_handler() -> tuple[logging.FileHandler | None, Path | None]:
    """Y.2.gate.c.11.app2-server-logs — when ``QS_GEN_RUN_DIR`` is
    set, route uvicorn's three loggers through a shared
    ``$QS_GEN_RUN_DIR/app2/server.log`` file. Returns the handler
    + log path so the caller can detach + report on cleanup. No-op
    (returns ``(None, None)``) when the env var is unset (legacy
    direct ``pytest`` invocation; nowhere to put the file).

    Sidecar contract (matches c.10 / c.11 / c.12): any OSError on
    mkdir / open OR registry validator failure is swallowed —
    capture failure must not break the test session.
    """
    try:
        run_dir_path = QS_GEN_RUN_DIR.get_or_none()
    except EnvVarInvalid:
        return None, None
    if run_dir_path is None:
        return None, None
    run_dir = str(run_dir_path)
    try:
        log_dir = Path(run_dir) / "app2"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "server.log"
        handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s",
        ))
        for name in _UVICORN_LOGGER_NAMES:
            logger = logging.getLogger(name)
            logger.addHandler(handler)
            # Lift the level so request-line + traceback messages
            # actually emit through the handler. The default config
            # sets uvicorn loggers to INFO; uvicorn.access also at
            # INFO. Be defensive in case some other code lowered them.
            if logger.level == logging.NOTSET or logger.level > logging.INFO:
                logger.setLevel(logging.INFO)
        return handler, log_path
    except OSError:
        return None, None


def _detach_app2_log_handler(handler: logging.FileHandler | None) -> None:
    """Remove the file handler from the three uvicorn loggers and
    close the file. No-op when ``handler`` is None. Sidecar contract:
    swallow OSError on close (rare but possible if the FS yanked
    out from under us)."""
    if handler is None:
        return
    for name in _UVICORN_LOGGER_NAMES:
        try:
            logging.getLogger(name).removeHandler(handler)
        except Exception:  # noqa: BLE001
            pass
    try:
        handler.close()
    except OSError:
        pass


@contextmanager
def html2_server(
    *,
    tree_app: App,
    sheet: Sheet,
    data_fetcher: DataFetcher,
    dashboard_id: str = "harness",
    dashboard_title: str = "Harness",
    filter_specs: Sequence[FilterSpec] = (),
    options_fetcher: OptionsFetcher | None = None,
    dev_log: bool = False,
    startup_timeout_s: float = 5.0,
) -> Iterator[str]:
    """Run an App2 Starlette server on an ephemeral port.

    Yields the bound URL. Tears down on context exit.

    Args:
        tree_app: Tree ``App`` owning the Sheet.
        sheet: Sheet to serve at ``/dashboards/{dashboard_id}``.
        data_fetcher: Layer-1 source-of-truth callable invoked on
            every GET to ``/dashboards/{d}/sheets/{s}/visuals/{v}/data``.
        dashboard_id: URL slug embedded in every visual's data
            URL. Defaults to ``"harness"`` so tests don't need to
            care unless they're asserting on the path.
        dashboard_title: human-readable name on the
            ``/dashboards`` listing page.
        filter_specs: explicit filter-form controls. When empty
            (the default) the server auto-derives them from the
            tree's parameter-control nodes via
            ``make_filter_specs_for_sheet`` — pass an explicit list
            for trees that have no controls but want the controls
            rendered anyway (e.g. the smoke app's ``SMOKE_FILTER_SPECS``).
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
        dashboards={
            dashboard_id: ServedDashboard(
                tree_app=tree_app, sheet=sheet,
                title=dashboard_title, data_fetcher=data_fetcher,
                filter_specs=tuple(filter_specs),
                options_fetcher=options_fetcher,
            ),
        },
        dev_log=dev_log,
    )
    # Y.2.gate.c.11.app2-server-logs — log_level lifts to "info" iff
    # capturing (otherwise stays "error" — keeps stderr quiet during
    # direct pytest invocations that don't set QS_GEN_RUN_DIR). The
    # handler attaches AFTER server.started: uvicorn.Server.run()
    # calls logging.config.dictConfig at startup which wipes any
    # handlers added beforehand. Post-start the dictConfig has run
    # and our addHandler sticks. Trade-off: the uvicorn startup
    # banner doesn't reach the file (only stderr); every per-request
    # access log + traceback after that DOES land.
    # Soft presence-check (sidecar pattern — bad env value should
    # degrade to "not capturing" rather than fail the server boot).
    try:
        capture_enabled = QS_GEN_RUN_DIR.get_or_none() is not None
    except EnvVarInvalid:
        capture_enabled = False
    log_level = "info" if capture_enabled else "error"
    config = uvicorn.Config(
        asgi, host="127.0.0.1", port=0, log_level=log_level,
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
        time.sleep(0.05)  # typing-smell: ignore[no-sleep]: 50ms uvicorn-startup poll; server.started has no awaitable
    log_handler, log_path = _attach_app2_log_handler()
    sock = server.servers[0].sockets[0]
    port = sock.getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        _detach_app2_log_handler(log_handler)
        if log_path is not None:
            # Soft signal to the operator that the file is there;
            # only printed when capturing actually happened.
            print(f"app2-harness: server log -> {log_path}")


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


def wait_for_kpi_value(page: Any, timeout_ms: int = 10000) -> str:
    """Wait for at least one ``.kpi-value`` to appear in the DOM and
    return its inner text. Used after page load / refresh to confirm
    the auto-load swap (X.2.g.1.a) fired and the KPI renderer
    hydrated."""
    page.wait_for_function(
        "() => document.querySelector('.kpi-value') !== null",
        timeout=timeout_ms,
    )
    return str(page.locator(".kpi-value").first.inner_text())


def wait_for_table_rows(
    page: Any, min_rows: int = 1, timeout_ms: int = 10000,
) -> int:
    """Wait for ``min_rows`` data rows in any rendered Table visual.
    Returns the actual row count seen.

    The d3 Table renderer paints ``<tr>`` per row inside a
    ``.table-data`` table. This wait fires after the HTMX swap +
    bootstrap.js hydration."""
    page.wait_for_function(
        f"() => document.querySelectorAll('.table-data tbody tr').length >= {min_rows}",
        timeout=timeout_ms,
    )
    return int(page.evaluate(
        "() => document.querySelectorAll('.table-data tbody tr').length",
    ))


def make_live_db_fetchers_for_app(
    *,
    tree_app: App,
    cfg: Any,
) -> tuple[DataFetcher, OptionsFetcher]:
    """Construct the live-DB ``(visual_fetcher, options_fetcher)`` pair
    from a built tree, sharing one lazily-created connection pool.

    The visual fetcher resolves any visual to its dataset SQL → executes
    → shapes (``make_tree_db_fetcher``); the options fetcher resolves a
    dataset-sourced dropdown's option universe (``make_options_fetcher``,
    X.2.u.4.b). Both go through ``execute_visual_sql_async`` so dialect /
    placeholder handling is identical.

    Pool lifecycle: an ``AsyncConnectionPool`` (psycopg / aiosqlite) is
    bound to the asyncio event loop it was opened in. The harness spins
    uvicorn in a thread with its own loop, so a pool created outside it
    hangs forever on acquire. The pool is lazy-created on the first
    fetcher call (which runs inside uvicorn's loop) and shared by both;
    leaked at process exit (fine for a test process about to die).
    """
    from quicksight_gen.common.db import (  # noqa: PLC0415
        make_connection_pool,
    )
    from quicksight_gen.common.html._tree_fetcher import (  # noqa: PLC0415
        make_options_fetcher,
        make_tree_db_fetcher,
    )

    cached: dict[str, Any] = {}

    async def _pool() -> Any:
        pool = cached.get("pool")
        if pool is None:
            pool = await make_connection_pool(
                cfg, max_size=cfg.app2_db_pool_size,
            )
            cached["pool"] = pool
        return pool

    async def visual_fetcher(visual_id: str, params: Any) -> Any:
        fn = cached.get("vf")
        if fn is None:
            fn = make_tree_db_fetcher(tree_app, cfg, pool=await _pool())
            cached["vf"] = fn
        return await fn(visual_id, params)

    async def options_fetcher(dataset_id: str, column: str) -> tuple[str, ...]:
        fn = cached.get("of")
        if fn is None:
            fn = make_options_fetcher(cfg, pool=await _pool())
            cached["of"] = fn
        return await fn(dataset_id, column)

    return visual_fetcher, options_fetcher


def make_live_db_fetcher_for_app(
    *,
    tree_app: App,
    cfg: Any,
) -> DataFetcher:
    """The visual-only half of ``make_live_db_fetchers_for_app`` — kept
    for callers that don't carry dataset-sourced dropdowns (so don't
    need the options fetcher)."""
    return make_live_db_fetchers_for_app(tree_app=tree_app, cfg=cfg)[0]


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
