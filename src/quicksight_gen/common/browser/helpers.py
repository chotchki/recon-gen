"""Helpers for driving QuickSight dashboards in a Playwright browser.

Used by both the e2e test suite (``tests/e2e/test_*.py``) and
production CLI code (the screenshot pipeline that renders handbook
images against a deployed dashboard). Promoted out of
``tests/e2e/`` in M.1.10 so production no longer has to import
from ``tests/``.

The QuickSight identity region (us-east-1) is where embed URL
generation and user operations live, even when the dashboard
itself is deployed in another region.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Generator, TypeVar

from quicksight_gen.common.env_keys import (
    EnvVarInvalid,
    QS_E2E_SCREENSHOT_DIR,
    QS_E2E_USER_ARN,
    QS_GEN_RUN_DIR,
    QS_GEN_TRACE_ALL,
)

if TYPE_CHECKING:
    # Playwright's sync API ships PEP 561 inline stubs — Page, Locator,
    # ElementHandle are all fully typed. Imported under TYPE_CHECKING
    # so loading this module doesn't require Playwright at runtime
    # (the helpers that touch it lazy-import inside the function body).
    from playwright.sync_api import Page

    # boto3-stubs[quicksight] provides this — typed client surface for
    # the QuickSight API. Used by ``generate_dashboard_embed_url`` to
    # avoid the partial-Unknown that bare ``boto3.client("quicksight")``
    # returns in pyright (the Literal-overload set is too large to resolve).
    from mypy_boto3_quicksight import QuickSightClient

T = TypeVar("T")


# Failure-screenshot output directory used by the e2e test suite's
# ``screenshot()`` helper. Resolved relative to the current working
# directory (pytest runs from repo root per pyproject.toml's
# ``testpaths = ["tests"]``); override via ``QS_E2E_SCREENSHOT_DIR``
# if you need a different sink. Production CLI screenshot capture
# uses an explicit ``output_dir`` arg to ``ScreenshotHarness`` and
# does NOT touch this constant.
SCREENSHOT_DIR = (
    QS_E2E_SCREENSHOT_DIR.get_or_none() or Path("tests/e2e/screenshots")
).resolve()


def get_user_arn() -> str:
    """Return the QuickSight user ARN to embed dashboards for.

    Reads ``QS_E2E_USER_ARN``. Raises ``RuntimeError`` when unset —
    the previous silent fallback to a hardcoded account-specific
    ARN string masked CI misconfiguration (Phase W's ``ci-bot`` user
    has a different ARN than the local-dev default; the fallback
    produced an embed URL the bot couldn't view) and burned a
    project AWS account ID into the source. Fail-loud is the
    contract.
    """
    # Use the registry's get_or_none() (NOT require()) so we keep the
    # historical RuntimeError contract — unit tests assert the exact
    # error type + message + runbook reference, and EnvVarRequired
    # would be a behavior change. The registry's IAM-ARN regex
    # validator still runs on the present value, surfacing
    # malformed-ARN bugs at this boundary instead of inside boto.
    arn = QS_E2E_USER_ARN.get_or_none()
    if not arn:
        raise RuntimeError(
            "QS_E2E_USER_ARN is not set. Embedding requires a "
            "QuickSight user ARN to sign the URL for. Export the "
            "ARN of the user whose session you want the embed to "
            "render under (locally: your default-namespace IAM "
            "user; CI: the ci-bot user). See "
            "`.github/E2E_SETUP.md` for the CI setup."
        )
    return arn


def generate_dashboard_embed_url(
    *,
    aws_account_id: str,
    aws_region: str,
    dashboard_id: str,  # typing-smell: ignore[bare-str-id]: QS API resource id, not the App2 routing slug DashboardId NewType
    user_arn: str | None = None,
    session_lifetime_minutes: int = 60,
) -> str:
    """Generate a pre-authenticated embed URL for a dashboard.

    Builds a boto3 QuickSight client in ``aws_region`` (the dashboard's
    region) and signs the URL with it. Embed URLs MUST be signed by a
    client whose region matches the dashboard's region — using the
    identity region (us-east-1) for a dashboard deployed elsewhere
    returns a URL QuickSight rejects with "We can't open that
    dashboard, another Quick account or it was deleted" — a confusing
    error that suggests permission/account/deletion when the actual
    cause is region mismatch. The M.4.1.i first AWS-side dry-run
    burned an hour on this when the harness called this helper with
    the identity-region client.

    Earlier the signature took a pre-built client which made it
    possible to pass the wrong region's client. This version requires
    callers to pass ``aws_region`` and constructs the client itself,
    making the bug class unrepresentable.

    All args keyword-only — protects against positional-arg drift if
    the parameter list ever changes again.
    """
    import boto3

    # boto3-stubs[quicksight] picks the right overload — the inferred
    # client type is QuickSightClient — but ``boto3.client`` itself is
    # an enormous Literal-overload set whose type pyright reports as
    # "partially unknown". The ignore is for THAT specific complaint;
    # the resolved RHS type is fully typed.
    qs: QuickSightClient = boto3.client(  # pyright: ignore[reportUnknownMemberType]: boto3-stubs huge overload union confuses pyright (X.2.o.5)
        "quicksight", region_name=aws_region,
    )
    resp = qs.generate_embed_url_for_registered_user(
        AwsAccountId=aws_account_id,
        SessionLifetimeInMinutes=session_lifetime_minutes,
        UserArn=user_arn or get_user_arn(),
        ExperienceConfiguration={
            "Dashboard": {"InitialDashboardId": dashboard_id},
        },
    )
    return resp["EmbedUrl"]


@contextmanager
def webkit_page(
    headless: bool = True, viewport: tuple[int, int] = (1600, 1000),
) -> Generator[Page, None, None]:
    """Yield a Playwright WebKit page; tears down browser on exit.

    On exception inside the ``with`` body, captures five diagnostics
    per failing test:

    - ``screenshot.png`` (or ``<test_id>.png`` in legacy mode) —
      full-page screenshot of the failure state
    - ``console.txt`` — every JS console message + uncaught
      ``pageerror`` accumulated since page creation (M.4.4.11 pattern,
      lifted from ``_harness_browser._attach_console_capture``)
    - ``qs_errors.txt`` — text content of any QS error overlays
      visible on the page (the "Failed to load visual" / SQL error
      tooltips that classic-QS shows for failed dataset queries)
    - ``network.txt`` — HTTP status + URL for every non-2xx
      response the page made. The X.1.b investigation revealed
      multiple ``404 Not Found`` responses paired with the
      ``Sample values not found`` JS errors — the URL pattern
      should disambiguate which QS-side resource is missing.
    - ``trace.zip`` (Y.2.gate.c.11) — Playwright trace bundle:
      full action timeline, DOM snapshots per action, screenshots,
      network, and console. Open with ``playwright show-trace
      trace.zip``.

    Output destination depends on ``QS_GEN_RUN_DIR``:

    - **Set** (running under the test layer chain runner):
      ``$QS_GEN_RUN_DIR/browser/<test_id>/{screenshot.png,console.txt,
      qs_errors.txt,network.txt,trace.zip}`` — per-test directory so
      artifacts cluster cleanly.
    - **Unset** (legacy ``./run_e2e.sh`` / direct ``pytest`` invocation):
      ``tests/e2e/screenshots/_failures/<test_id>.png`` etc., flat
      directory with per-file ``<test_id>_`` prefix to disambiguate.
      Trace.zip is NOT written in legacy mode (no run-dir to put it in).

    Trace capture policy:
    - On exception → trace always written (under the run-dir mode).
    - On clean exit → trace written iff ``QS_GEN_TRACE_ALL=1`` is set
      (operator opt-in for "I want the full trace even on green tests";
      flag plumbed by ``Y.2.gate.c.7``).

    All capture is best-effort — exceptions inside the dump path are
    swallowed so the original assertion bubbles up unchanged.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.webkit.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]},
        )
        # Y.2.gate.c.11 — start tracing immediately so the trace bundle
        # captures EVERYTHING the test does. We decide whether to save
        # vs. discard in the finally block based on outcome + env flag.
        # screenshots/snapshots/sources are the kitchen-sink set —
        # enables full timeline replay in `playwright show-trace`.
        try:
            context.tracing.start(
                screenshots=True, snapshots=True, sources=True,
            )
        except Exception:
            # Old Playwright versions or odd configs — keep going
            # without tracing; the other 4 diagnostics still fire.
            pass
        page = context.new_page()
        console_messages: list[str] = []
        network_responses: list[str] = []
        _attach_console_capture(page, console_messages)
        _attach_network_capture(page, network_responses)
        failed = False
        try:
            yield page
        except BaseException:
            failed = True
            _capture_failure_screenshot(page)
            _capture_failure_console(console_messages)
            _capture_failure_qs_errors(page)
            _capture_failure_network(network_responses)
            raise
        finally:
            _stop_and_maybe_save_trace(context, failed=failed)
            context.close()
            browser.close()


def _stop_and_maybe_save_trace(context: object, *, failed: bool) -> None:
    """Y.2.gate.c.11 — finalize the Playwright trace.

    Saves + unpacks to ``$QS_GEN_RUN_DIR/browser/<test_id>/`` when:
    - ``failed`` is True (always capture failure traces), OR
    - ``QS_GEN_TRACE_ALL=1`` (operator opt-in for full traces on green).

    Otherwise discards the trace (call ``stop()`` with no path).
    No-op when ``QS_GEN_RUN_DIR`` isn't set — there's nowhere to put
    the trace file in legacy mode.

    Two outputs land per saved trace:
    - ``trace.zip`` — original Playwright bundle, openable with
      ``playwright show-trace trace.zip`` (full UI replay).
    - ``trace/`` (extracted) — sibling directory with the unpacked
      contents: ``trace.network`` / ``trace.trace`` / ``trace.stacks``
      (text) plus ``resources/`` (snapshot images, sources). Makes the
      contents directly ``grep``/``ls``-able without spinning up the
      trace viewer — operator-friendly for "what did this test
      actually do" inspection.

    All errors swallowed (sidecar contract; matches c.2 / c.10 / c.12).
    """
    import zipfile

    # Sidecar contract — swallow registry validator failures (e.g.
    # QS_GEN_RUN_DIR pointing at a non-dir) the same way the surrounding
    # try/except swallows OSError. A misconfigured env var must not
    # fail the wrapped browser test.
    try:
        run_dir_path = QS_GEN_RUN_DIR.get_or_none()
    except EnvVarInvalid:
        run_dir_path = None
    run_dir = str(run_dir_path) if run_dir_path is not None else None
    trace_all = bool(QS_GEN_TRACE_ALL.get_or_none())
    should_save = bool(run_dir) and (failed or trace_all)
    try:
        if should_save:
            trace_dir = (
                Path(run_dir) / "browser" / _test_id_from_pytest_env()  # type: ignore[arg-type]: run_dir narrowed truthy by the bool() above
            )
            trace_dir.mkdir(parents=True, exist_ok=True)
            zip_path = trace_dir / "trace.zip"
            context.tracing.stop(path=str(zip_path))  # type: ignore[attr-defined]: Playwright duck-typed tracing API
            try:
                # Extract for grepability — sibling "trace/" dir.
                # ZIP slip not a concern: Playwright generates the
                # archive itself, not user-supplied content.
                extract_dir = trace_dir / "trace"
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(extract_dir)
            except Exception:
                pass
        else:
            context.tracing.stop()  # type: ignore[attr-defined]: Playwright duck-typed tracing API
    except Exception:
        pass


def _capture_dir_for(test_id: str) -> Path:
    """Y.2.gate.c.11 — pick where per-failure dumps land.

    Returns ``$QS_GEN_RUN_DIR/browser/<test_id>/`` when the runner
    env is set, else the legacy ``<SCREENSHOT_DIR>/_failures/`` flat
    dir.
    """
    # Soft-fall through on bad value (matches the sidecar pattern in
    # ``_finalize_browser_capture``).
    try:
        run_dir = QS_GEN_RUN_DIR.get_or_none()
    except EnvVarInvalid:
        run_dir = None
    if run_dir is not None:
        return run_dir / "browser" / test_id
    return SCREENSHOT_DIR / "_failures"


def _capture_path(filename_short: str, test_id: str) -> Path:
    """Y.2.gate.c.11 — resolve the per-test path for one capture file.

    In run-dir mode: ``<run-dir>/browser/<test_id>/<filename_short>``
    (clean per-test directory; ``screenshot.png``, ``console.txt``).

    In legacy mode: ``<SCREENSHOT_DIR>/_failures/<test_id>_<filename_short>``
    (flat directory with per-test prefix). Backwards-compat with the
    pre-c.11 file naming so existing CI artifact-upload steps still
    pick the same files. Special-case: the screenshot in legacy mode
    is just ``<test_id>.png`` (no underscore prefix), matching the
    M.4.4.11-era convention.
    """
    # Soft-presence check (matches sidecar pattern).
    try:
        run_dir_present = QS_GEN_RUN_DIR.get_or_none() is not None
    except EnvVarInvalid:
        run_dir_present = False
    if run_dir_present:
        return _capture_dir_for(test_id) / filename_short
    legacy_dir = _capture_dir_for(test_id)
    if filename_short == "screenshot.png":
        return legacy_dir / f"{test_id}.png"
    return legacy_dir / f"{test_id}_{filename_short}"


def _attach_console_capture(page: Page, sink: list[str]) -> None:
    """Register ``page.on("console")`` + ``page.on("pageerror")`` so
    every JS console message + uncaught error during the page
    lifecycle accumulates into ``sink``. M.4.4.11 pattern; previously
    only wired in the harness, now lifted into ``webkit_page`` so
    every browser test gets it for free.

    Format mirrors what a human sees in the browser devtools:
    ``[<type>] <text>`` for console events, ``[pageerror] <text>``
    for uncaught exceptions. Each handler is wrapped in a broad
    ``except`` because a misbehaving listener that raises would
    otherwise abort the page lifecycle.
    """
    def _on_console(msg: object) -> None:
        try:
            msg_type = getattr(msg, "type", "log")
            text = getattr(msg, "text", "")
            sink.append(f"[{msg_type}] {text}")
        except Exception:
            pass

    def _on_pageerror(exc: object) -> None:
        try:
            sink.append(f"[pageerror] {exc}")
        except Exception:
            pass

    page.on("console", _on_console)
    page.on("pageerror", _on_pageerror)


def _attach_network_capture(page: Page, sink: list[str]) -> None:
    """Register ``page.on("response")`` so every non-2xx HTTP
    response during the page lifecycle accumulates into ``sink``.
    Format: ``<status> <method> <url>`` per response.

    Filters to non-2xx because QS dashboards make hundreds of
    requests; capturing only the failures keeps the dump readable.
    Listener is wrapped in broad ``except`` so a misbehaving handler
    can't abort the page lifecycle.
    """
    def _on_response(response: object) -> None:
        try:
            status = getattr(response, "status", 0)
            if 200 <= status < 300:
                return
            request = getattr(response, "request", None)
            method = getattr(request, "method", "?") if request else "?"
            url = getattr(response, "url", "")
            sink.append(f"{status} {method} {url}")
        except Exception:
            pass

    page.on("response", _on_response)


def _test_id_from_pytest_env(raw: str | None = None) -> str:
    """Derive a filename-safe test ID from ``PYTEST_CURRENT_TEST``.

    pytest sets the env var to a string like
    ``"tests/e2e/test_foo.py::test_bar (call)"`` (or with a class
    segment + parametrization brackets). Strip the trailing
    ``(setup|call|teardown)`` phase suffix and convert ``/`` + ``::``
    to underscores so the result is a valid filename. ``"unknown"``
    when the env var is unset — covers running outside pytest or
    after pytest cleared the var on test exit.
    """
    if raw is None:
        raw = os.environ.get("PYTEST_CURRENT_TEST", "")
    if not raw:
        return "unknown"
    return (
        raw.split(" (")[0]
        .replace("/", "_")
        .replace("::", "__")
        .replace(".py", "")
    )


def _capture_failure_screenshot(page: Page) -> None:
    """Best-effort failure screenshot. Writes to
    ``<capture_dir>/screenshot.png`` (or legacy ``<test_id>.png``).
    All errors swallowed — a screenshot-capture exception must never
    mask the original test failure (closed page, missing env var,
    full disk, etc.).
    """
    try:
        test_id = _test_id_from_pytest_env()
        path = _capture_path("screenshot.png", test_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        pass


def _capture_failure_console(messages: list[str]) -> None:
    """Dump accumulated JS console + pageerror messages to
    ``<capture_dir>/console.txt`` (or legacy ``<test_id>_console.txt``).
    Empty file when nothing was logged (so the artifact bundle
    reliably contains the file and the absence of content is itself
    a signal).
    """
    try:
        test_id = _test_id_from_pytest_env()
        path = _capture_path("console.txt", test_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(messages), encoding="utf-8")
    except Exception:
        pass


def _capture_failure_network(responses: list[str]) -> None:
    """Dump the captured non-2xx HTTP responses to
    ``<capture_dir>/network.txt`` (or legacy ``<test_id>_network.txt``).
    Empty file when every request succeeded (so the artifact bundle
    reliably contains the file and the absence of content is itself
    a signal).
    """
    try:
        test_id = _test_id_from_pytest_env()
        path = _capture_path("network.txt", test_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(responses), encoding="utf-8")
    except Exception:
        pass


def _capture_failure_qs_errors(page: Page) -> None:
    """Dump the text of any QuickSight error overlays visible on the
    page to ``<capture_dir>/qs_errors.txt`` (or legacy
    ``<test_id>_qs_errors.txt``). Targets the well-known QS error
    markers — the "Failed to load visual" tooltip, the visual-error
    icon's accessible label, and error banners. Empty file when
    nothing matched.
    """
    try:
        test_id = _test_id_from_pytest_env()
        path = _capture_path("qs_errors.txt", test_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # JS-side scan: collect text from any DOM nodes whose
        # automation-id, role, or class hint at an error / failure
        # surface. Broad on purpose — the cost of an extra string is
        # nothing; missing the SQL-error tooltip during diagnosis
        # forces another CI cycle.
        errors = page.evaluate(
            """() => {
                const out = [];
                const selectors = [
                    '[data-automation-id*="error"]',
                    '[data-automation-id*="Error"]',
                    '[data-automation-id*="failure"]',
                    '[data-automation-id*="visual_unavailable"]',
                    '[role="alert"]',
                    '[class*="error-message"]',
                    '[class*="ErrorMessage"]',
                    '[class*="visualError"]',
                ];
                for (const sel of selectors) {
                    document.querySelectorAll(sel).forEach(el => {
                        const text = (el.innerText || el.textContent || '').trim();
                        if (text) out.push(`[${sel}] ${text}`);
                    });
                }
                return out;
            }"""
        )
        path.write_text("\n".join(errors or []), encoding="utf-8")
    except Exception:
        pass


def wait_for_dashboard_loaded(page: Page, timeout_ms: int) -> None:
    """Wait for the QuickSight dashboard chrome (sheet tabs) to appear.

    Polls for ``[role="tab"]`` directly. Don't wait for ``networkidle``
    first — QS holds open WebSocket / long-polling connections that
    keep the network busy indefinitely, so networkidle never fires
    and a 120s timeout burns waiting for an event that won't happen.
    The sheet-tab strip attaching to the DOM is the authoritative
    "chrome is up" signal; in practice it appears within ~1s of the
    embed URL load completing.
    """
    page.wait_for_selector('[role="tab"]', timeout=timeout_ms, state="attached")


def wait_for_visuals_rendered(page: Page, timeout_ms: int, min_visuals: int = 1) -> None:
    """Wait for visual containers to finish their loading state.

    QuickSight visuals show a loading skeleton while data fetches. We
    poll for the absence of skeleton/loading classes within visual cells.
    """
    page.wait_for_function(
        f"""() => {{
            const cells = document.querySelectorAll('[data-automation-id*="visual"], [class*="visual-container"]');
            if (cells.length < {min_visuals}) return false;
            // No element should still be in a loading state
            const loading = document.querySelectorAll('[class*="loading"], [class*="Loading"], [aria-busy="true"]');
            return loading.length === 0;
        }}""",
        timeout=timeout_ms,
    )


def get_sheet_tab_names(page: Page) -> list[str]:
    """Return the visible sheet tab labels in order."""
    tabs = page.query_selector_all('[role="tab"]')
    return [t.inner_text().strip() for t in tabs if t.inner_text().strip()]


VISUAL_SELECTOR = '[data-automation-id="analysis_visual"]'


def click_sheet_tab(page: Page, name: str, timeout_ms: int) -> None:
    """Activate a sheet tab by its visible name and wait for the switch.

    QuickSight tears down the prior sheet's visuals on switch. We
    snapshot the current visual titles before the click, then wait
    for them to be replaced — otherwise a wait that just checks
    "≥ N visuals present" can be satisfied by the prior sheet.
    """
    # No-op if we're already on the target sheet
    selected_el = page.query_selector('[data-automation-id="selectedTab_sheet_name"]')
    if selected_el and selected_el.inner_text().strip() == name:
        return
    prior_titles = sorted(set(get_visual_titles(page)))
    tab = page.locator('[role="tab"]', has_text=name).first
    tab.click(timeout=timeout_ms)
    # 1. Selected-tab name indicator updates to the target sheet
    page.wait_for_function(
        f"""() => {{
            const el = document.querySelector('[data-automation-id="selectedTab_sheet_name"]');
            return el && el.innerText.trim() === {name!r};
        }}""",
        timeout=timeout_ms,
    )
    # 2. The prior sheet's visual titles are no longer in the DOM
    if prior_titles:
        page.wait_for_function(
            f"""() => {{
                const prior = new Set({prior_titles!r});
                const labels = document.querySelectorAll('[data-automation-id="analysis_visual_title_label"]');
                for (const l of labels) {{
                    if (prior.has(l.innerText.trim())) return false;
                }}
                return true;
            }}""",
            timeout=timeout_ms,
        )


def selected_sheet_name(page: Page) -> str:
    """Return the label of the currently active sheet tab, or empty string."""
    el = page.query_selector('[data-automation-id="selectedTab_sheet_name"]')
    return el.inner_text().strip() if el else ""


def wait_for_sheet_tab(page: Page, name: str, timeout_ms: int) -> None:
    """Block until the active sheet tab's label equals ``name``.

    Used after a drill-down click to confirm navigation landed on the
    expected sheet. For deliberate tab switches use ``click_sheet_tab``
    which also waits for prior-sheet visuals to tear down.
    """
    page.wait_for_function(
        f"""() => {{
            const el = document.querySelector('[data-automation-id="selectedTab_sheet_name"]');
            return el && el.innerText.trim() === {name!r};
        }}""",
        timeout=timeout_ms,
    )


def wait_for_table_cells_present(page: Page, timeout_ms: int) -> None:
    """Wait until at least one table cell (row 0, col 0) renders on the
    active sheet. Useful after tab switches before asserting on row content.
    """
    page.wait_for_selector(
        '[data-automation-id^="sn-table-cell-0-0"]',
        timeout=timeout_ms,
        state="attached",
    )


def first_table_cell_text(page: Page, row: int, col: int) -> str:
    """Return the text of cell ``(row, col)`` in the first detail table on
    the active sheet. Targets the global ``sn-table-cell-{row}-{col}``
    automation id — use ``click_first_row_of_visual`` when multiple tables
    are on the same sheet.
    """
    cell = page.query_selector(f'[data-automation-id="sn-table-cell-{row}-{col}"]')
    assert cell is not None, f"No cell at row={row} col={col}"
    return cell.inner_text().strip()


def click_first_row_of_visual(
    page: Page, visual_title: str, timeout_ms: int,
) -> None:
    """Click the first data cell (row 0, col 0) of the named visual.

    Tags the cell with a unique ``data-e2e-target`` attribute first so the
    click selector is unambiguous even when multiple tables share the same
    global ``sn-table-cell-0-0``. Clears the marker after so subsequent
    calls don't pick up a stale target.
    """
    scroll_visual_into_view(page, visual_title, timeout_ms)
    ok = page.evaluate(
        """(title) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (!t || t.innerText.trim() !== title) continue;
                const cell = v.querySelector('[data-automation-id="sn-table-cell-0-0"]');
                if (cell) {
                    cell.setAttribute('data-e2e-target', '1');
                    return true;
                }
            }
            return false;
        }""",
        visual_title,
    )
    assert ok, f"Could not find first cell of visual {visual_title!r}"
    page.click('[data-e2e-target="1"]', timeout=timeout_ms)
    page.evaluate(
        """() => document.querySelectorAll('[data-e2e-target]').forEach(
            e => e.removeAttribute('data-e2e-target')
        )"""
    )


def right_click_first_row_of_visual(
    page: Page, visual_title: str, timeout_ms: int,
) -> None:
    """Right-click the first data cell of the named visual.

    Mirror of ``click_first_row_of_visual`` but dispatches a contextmenu
    event so QuickSight opens the visual's DATA_POINT_MENU drill list.
    Tags the cell with ``data-e2e-target`` first so the click target is
    unambiguous when multiple tables share the same global cell selectors.
    """
    scroll_visual_into_view(page, visual_title, timeout_ms)
    ok = page.evaluate(
        """(title) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (!t || t.innerText.trim() !== title) continue;
                const cell = v.querySelector('[data-automation-id="sn-table-cell-0-0"]');
                if (cell) {
                    cell.setAttribute('data-e2e-target', '1');
                    return true;
                }
            }
            return false;
        }""",
        visual_title,
    )
    assert ok, f"Could not find first cell of visual {visual_title!r}"
    page.locator('[data-e2e-target="1"]').first.click(
        button="right", timeout=timeout_ms,
    )
    # Confirm the contextmenu actually popped (vs. a fixed sleep). Two
    # wins: returns the moment the menu mounts (no fixed 800ms), and
    # fails *here* with "waiting for [role=menu]" if no DATA_POINT_MENU
    # drill is wired on this visual — instead of the caller's
    # ``click_context_menu_item`` timing out 30s later on the absent
    # menu *item*.
    page.wait_for_selector('[role="menu"]', timeout=timeout_ms, state="visible")
    page.evaluate(
        """() => document.querySelectorAll('[data-e2e-target]').forEach(
            e => e.removeAttribute('data-e2e-target')
        )"""
    )


def click_context_menu_item(page: Page, item_text: str, timeout_ms: int) -> None:
    """Click an entry in QuickSight's data-point context menu by visible text.

    QS's right-click menu mounts as a portal with each entry as a
    ``[role="menuitem"]``. The drill action's `Name` parameter from the
    Python builder appears verbatim as the menu item's text.
    """
    page.wait_for_selector(
        '[role="menu"] [role="menuitem"]',
        timeout=timeout_ms,
        state="visible",
    )
    page.locator(
        '[role="menu"] [role="menuitem"]', has_text=item_text,
    ).first.click(timeout=timeout_ms)


def sheet_control_titles(page: Page) -> list[str]:
    """Return the visible titles of filter controls on the active sheet."""
    els = page.query_selector_all('[data-automation-id="sheet_control_name"]')
    return [e.inner_text().strip() for e in els if e.inner_text().strip()]


def wait_for_sheet_controls_present(page: Page, timeout_ms: int) -> None:
    """Wait until at least one filter control is attached on the active sheet."""
    page.wait_for_selector(
        '[data-automation-id="sheet_control_name"]',
        timeout=timeout_ms,
        state="attached",
    )


def _retry_on_playwright_timeout(
    call: Callable[[], T], *, timeout_ms: int,
) -> T:
    """Run ``call()``; if Playwright's wait timed out, retry once with the
    same budget. Aurora Serverless v2 cold-start can stall the first SELECT
    for ~30s — the conftest warm-up fixture covers session start, but ad-hoc
    reruns and idle-between-sheets gaps can still hit a cold cluster. One
    retry survives that window without papering over genuine render bugs
    (which fail twice).
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        return call()
    except PlaywrightTimeoutError:
        return call()


def wait_for_visual_titles_present(
    page: Page, expected_titles: list[str], timeout_ms: int,
) -> None:
    """Block until every title in ``expected_titles`` is rendered as an
    ``analysis_visual_title_label``. Visual containers attach before their
    title labels hydrate, so a simple container count isn't enough when the
    test asserts on specific titles.
    """
    titles_list = sorted(set(expected_titles))
    script = f"""() => {{
        const want = new Set({titles_list!r});
        const have = new Set(
            Array.from(document.querySelectorAll(
                '[data-automation-id="analysis_visual_title_label"]'
            )).map(el => el.innerText.trim()).filter(Boolean)
        );
        for (const t of want) {{ if (!have.has(t)) return false; }}
        return true;
    }}"""
    _retry_on_playwright_timeout(
        lambda: page.wait_for_function(script, timeout=timeout_ms),
        timeout_ms=timeout_ms,
    )


def wait_for_visuals_present(page: Page, min_count: int, timeout_ms: int) -> int:
    """Wait until at least `min_count` visual containers are rendered.

    Returns the actual count observed.
    """
    script = f"""() => document.querySelectorAll('{VISUAL_SELECTOR}').length >= {min_count}"""
    _retry_on_playwright_timeout(
        lambda: page.wait_for_function(script, timeout=timeout_ms),
        timeout_ms=timeout_ms,
    )
    return len(page.query_selector_all(VISUAL_SELECTOR))


def get_visual_titles(page: Page) -> list[str]:
    """Return the title text of every visual currently on the page."""
    titles = page.query_selector_all('[data-automation-id="analysis_visual_title_label"]')
    return [t.inner_text().strip() for t in titles if t.inner_text().strip()]


def scroll_visual_into_view(
    page: Page, visual_title: str, timeout_ms: int, *, wait_for_cells: bool = True,
) -> None:
    """Scroll the visual with the given title to the viewport center.

    QuickSight virtualizes below-the-fold visuals — table cells are absent
    from the DOM until the visual is on screen. Browser tests that click
    into such a table must call this first, or the click-target selector
    will return nothing.

    Pass ``wait_for_cells=False`` for chart visuals (bar / pie / line),
    which don't render ``sn-table-cell-*`` markers and would otherwise
    time out.
    """
    page.evaluate(
        """(title) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (t && t.innerText.trim() === title) {
                    v.scrollIntoView({block: 'center'});
                    return;
                }
            }
        }""",
        visual_title,
    )
    if not wait_for_cells:
        page.wait_for_timeout(800)
        return
    page.wait_for_function(
        """(title) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (!t || t.innerText.trim() !== title) continue;
                return v.querySelector('[data-automation-id="sn-table-cell-0-0"]') !== null;
            }
            return false;
        }""",
        arg=visual_title,
        timeout=timeout_ms,
    )


def count_table_rows(page: Page, visual_title: str) -> int:
    """Count distinct table rows in the visual whose title matches.

    Returns -1 if no visual with that title is on the page. Returns 0 if
    the visual is present but empty. Caller is responsible for ensuring
    the visual is hydrated (use ``scroll_visual_into_view`` for
    below-the-fold tables).
    """
    return page.evaluate(
        """(title) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (!t || t.innerText.trim() !== title) continue;
                const rows = new Set();
                v.querySelectorAll('[data-automation-id^="sn-table-cell-"]').forEach(c => {
                    const m = c.getAttribute('data-automation-id').match(/sn-table-cell-(\\d+)-/);
                    if (m) rows.add(m[1]);
                });
                return rows.size;
            }
            return -1;
        }""",
        visual_title,
    )


def expand_all_tables_on_sheet(page: Page, *, timeout_ms: int = 10_000) -> int:
    """Bump every paged table visual on the active sheet to page-size 10000.

    X.1.c — QS tables virtualize at ~10 DOM rows. Assertions that read
    ``inner_text()`` of a table to check whether a specific row is
    present (e.g. the harness ``assert_l1_plants_visible`` walking the
    sheet text for a planted account_id) silently miss any row outside
    the rendered window. This is deterministic — not a flake — and
    surfaces only when the seed is dense enough to push the target row
    below the first ~10. (Spec_example passes; sasquatch_pr fails on
    the same assertion code, same dashboard, because it has more
    transactions hence more breach rows hence more table rows.)

    This helper finds every visual on the active sheet that exposes
    QS's ``simplePagedDisplayNav_dropdown_pageSize`` control, focuses
    it, and bumps the page size to 10000 so all rows mount in DOM.
    Visuals without pagination (KPIs, charts, line/bar) are silently
    skipped — the helper detects pagination by attempting the
    ``wait_for_selector`` lookup with a short per-visual timeout.

    Returns the number of table visuals that were successfully
    expanded. Caller can use the return for sanity checks but the
    helper is best-effort by design — a sheet with zero paged tables
    returns 0 and is not an error.

    Cost: ~1.5–3s per table visual (focus dance + dropdown click +
    settle). Acceptable for assertion paths that need correctness over
    speed; not appropriate for inner loops where ``count_table_rows``
    suffices.
    """
    expanded = 0
    visuals = page.query_selector_all('[data-automation-id="analysis_visual"]')
    for visual in visuals:
        title_el = visual.query_selector(
            '[data-automation-id="analysis_visual_title_label"]'
        )
        if title_el is None:
            continue
        title = title_el.inner_text().strip()
        if not title:
            continue
        # Focus the visual to surface the page-size control. Use a JS
        # click on the title so we dodge Playwright's actionability
        # checks (which race against QS's re-render churn).
        clicked = page.evaluate(
            """(t) => {
                const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
                for (const v of visuals) {
                    const lbl = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                    if (lbl && lbl.innerText.trim() === t) {
                        lbl.click();
                        return true;
                    }
                }
                return false;
            }""",
            title,
        )
        if not clicked:
            continue
        # Wait for the page-size dropdown to mount after the focus-click
        # (instead of a fixed 800ms + a 1.5s wait_for_selector). The
        # 2.5s budget = the deleted 800ms head start + the original
        # 1.5s wait; ``wait_for_selector`` returns the moment it
        # appears, so this is strictly faster-or-equal.
        try:
            page.wait_for_selector(
                '[data-automation-id="simplePagedDisplayNav_dropdown_pageSize"]',
                timeout=2500, state="visible",
            )
        except Exception:
            # No pagination — KPI / chart / line / bar / sankey. Skip.
            continue
        try:
            page.locator(
                '[data-automation-id="simplePagedDisplayNav_dropdown_pageSize"]'
            ).first.click()
            page.wait_for_selector(
                '[data-automation-id="simplePagedDisplayNav_menuItem_pageSize_10000"]',
                timeout=timeout_ms, state="visible",
            )
            page.locator(
                '[data-automation-id="simplePagedDisplayNav_menuItem_pageSize_10000"]'
            ).first.click()
            page.wait_for_timeout(500)
            expanded += 1
        except Exception:
            # Pagination existed but the bump failed — leave the table
            # at its existing page size and let the caller's assertion
            # decide if the partial-DOM read is enough.
            continue
    return expanded


def count_table_total_rows(page: Page, visual_title: str, timeout_ms: int) -> int:
    """Return the full (post-filter) row count of a QS table visual.

    QS tables virtualize — ``count_table_rows`` only sees the ~10 rows
    currently mounted in the DOM. For filter-narrowing assertions where
    both pre and post totals exceed the viewport, DOM counts stay flat
    and the assertion silently passes. This helper:

    1. Focuses the visual (click title) to reveal ``simplePagedDisplayNav_*``.
    2. Sets page size to 10000 so all rows fit on one page.
    3. Scrolls the inner ``.grid-container`` to the bottom, tracking the
       highest ``sn-table-cell-N-*`` index seen.

    Use this helper when the table's row count may exceed ~10 and you need
    a precise total. Prefer ``count_table_rows`` when you already know the
    table fits in the viewport — it's much faster.

    Raises a timeout if the pagination controls never appear (i.e. the
    visual isn't actually a paged table).
    """
    # Scroll the visual into view. Use scroll_visual_into_view when the
    # table has data (cells present); otherwise fall back to a plain
    # element.scrollIntoView, which still positions QS's inner scroll
    # container correctly even when cells haven't mounted.
    try:
        scroll_visual_into_view(page, visual_title, timeout_ms=5000)
    except Exception:
        page.evaluate(
            """(title) => {
                const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
                for (const v of visuals) {
                    const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                    if (t && t.innerText.trim() === title) {
                        v.scrollIntoView({block: 'center'});
                        return;
                    }
                }
            }""",
            visual_title,
        )
        page.wait_for_timeout(2000)
    # Focus the visual via a JS click dispatched directly on the title
    # element. Playwright's locator.click() runs actionability checks that
    # trigger auto-scroll within QS's re-rendering content and race against
    # "element detached" errors — a raw DOM click avoids all of that and
    # still reliably reveals simplePagedDisplayNav_*.
    clicked = page.evaluate(
        """(title) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (t && t.innerText.trim() === title) {
                    t.click();
                    return true;
                }
            }
            return false;
        }""",
        visual_title,
    )
    assert clicked, f"No visual with title {visual_title!r}"
    # Wait for the paging controls to mount after the focus-click
    # (instead of a fixed 1.5s + a 3s wait_for_selector). The 4.5s
    # budget = the deleted 1.5s head start + the original 3s wait;
    # ``wait_for_selector`` returns the moment it appears, so this is
    # strictly faster-or-equal. On repeat calls the controls often
    # don't re-mount (focus already consumed, or lost to a prior
    # filter interaction) — the ``except`` below catches that and
    # relies on the page size set by the first successful call
    # persisting through the session.
    try:
        page.wait_for_selector(
            '[data-automation-id="simplePagedDisplayNav_dropdown_pageSize"]',
            timeout=4500, state="visible",
        )
        page.locator(
            '[data-automation-id="simplePagedDisplayNav_dropdown_pageSize"]'
        ).first.click()
        page.wait_for_selector(
            '[data-automation-id="simplePagedDisplayNav_menuItem_pageSize_10000"]',
            timeout=timeout_ms, state="visible",
        )
        page.locator(
            '[data-automation-id="simplePagedDisplayNav_menuItem_pageSize_10000"]'
        ).first.click()
        page.wait_for_timeout(500)
    except Exception:
        pass

    return page.evaluate(
        """async (title) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            let target = null;
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (t && t.innerText.trim() === title) { target = v; break; }
            }
            if (!target) return -1;
            const container = target.querySelector('.grid-container');
            if (!container) return -2;
            const getMaxRow = () => {
                let max = -1;
                target.querySelectorAll('[data-automation-id^="sn-table-cell-"]').forEach(c => {
                    const m = c.getAttribute('data-automation-id').match(/sn-table-cell-(\\d+)-/);
                    if (m) {
                        const n = parseInt(m[1], 10);
                        if (n > max) max = n;
                    }
                });
                return max;
            };
            let max = getMaxRow();
            let stable = 0;
            for (let step = 0; step < 500; step++) {
                const prev = max;
                container.scrollTop = container.scrollTop + 400;
                await new Promise(r => setTimeout(r, 120));
                const now = getMaxRow();
                if (now > max) max = now;
                if (container.scrollTop + container.clientHeight >= container.scrollHeight - 1) {
                    await new Promise(r => setTimeout(r, 400));
                    max = Math.max(max, getMaxRow());
                    break;
                }
                if (now === prev) { stable++; if (stable > 3) break; }
                else { stable = 0; }
            }
            return max < 0 ? 0 : max + 1;
        }""",
        visual_title,
    )


def wait_for_table_total_rows_to_change(
    page: Page, visual_title: str, before: int, timeout_ms: int,
) -> int:
    """Poll a table's total row count (via ``count_table_total_rows``) until
    it differs from ``before``. Returns the new total.

    Unlike ``wait_for_table_rows_to_change``, this compares *post-filter*
    totals, not DOM-visible rows — use it when the table may exceed the
    virtualization window.
    """
    import time
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        current = count_table_total_rows(page, visual_title, timeout_ms=timeout_ms)
        if current != before:
            return current
        page.wait_for_timeout(500)
    raise TimeoutError(
        f"{visual_title!r} total row count never changed from {before} "
        f"within {timeout_ms}ms"
    )


def count_chart_categories(page: Page, visual_title: str) -> int:
    """Count distinct categorical entries (bars / slices) in a chart.

    QS renders charts to ``<canvas>``, so there are no DOM bars/slices to
    count directly. Two signals we can read:

    1. **Chart aria-label**: QS publishes a screen-reader description like
       "This is a chart with type Bar chart ... the data for X is Y, the
       data for Z is W, ...". Counting ``the data for`` occurrences yields
       the category count reliably (works for bar + line + pie).
    2. **Legend rows** (``data-automation-id="visual_legend_item_value"``):
       present on pie/donut charts and any chart with a legend.

    Returns the max of the two signals, or ``-1`` if the visual isn't found.
    Use to assert *change*, not exact value (chart may hide low-freq series).
    """
    return page.evaluate(
        """(title) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (!t || t.innerText.trim() !== title) continue;
                let aria = 0;
                for (const e of v.querySelectorAll('[aria-label]')) {
                    const lbl = e.getAttribute('aria-label') || '';
                    if (lbl.includes('the data for')) {
                        aria = Math.max(aria, (lbl.match(/the data for/g) || []).length);
                    }
                }
                const legend = v.querySelectorAll(
                    '[data-automation-id="visual_legend_item_value"]'
                ).length;
                return Math.max(aria, legend);
            }
            return -1;
        }""",
        visual_title,
    )


def wait_for_chart_categories_to_change(
    page: Page, visual_title: str, before: int, timeout_ms: int,
) -> int:
    """Poll ``count_chart_categories`` until the value differs from ``before``.
    Returns the new count. Mirrors ``wait_for_table_rows_to_change``.
    """
    import time
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        current = count_chart_categories(page, visual_title)
        if current != before and current >= 0:
            return current
        page.wait_for_timeout(250)
    raise TimeoutError(
        f"{visual_title!r} chart category count never changed from {before} "
        f"within {timeout_ms}ms"
    )


def read_chart_categories(page: Page, visual_title: str) -> list[str]:
    """Return the ordered category labels (bar names / slice names) of a
    chart visual, parsed from QS's screen-reader aria-label.

    QS aria-labels a chart container with "...the data for <CAT> is <N>,
    the data for <CAT> is <N>, ...". Parse that into an ordered list.
    Returns [] if the visual isn't found or has no aria description.
    """
    return page.evaluate(
        """(title) => {
            const visuals = document.querySelectorAll(
                '[data-automation-id="analysis_visual"]'
            );
            for (const v of visuals) {
                const t = v.querySelector(
                    '[data-automation-id="analysis_visual_title_label"]'
                );
                if (!t || t.innerText.trim() !== title) continue;
                let best = [];
                for (const e of v.querySelectorAll('[aria-label]')) {
                    const lbl = e.getAttribute('aria-label') || '';
                    if (!lbl.includes('the data for')) continue;
                    const matches = [
                        ...lbl.matchAll(/the data for ([^,]+?) is /g)
                    ].map(m => m[1].trim());
                    if (matches.length > best.length) best = matches;
                }
                return best;
            }
            return [];
        }""",
        visual_title,
    )


def click_chart_bar(
    page: Page, visual_title: str, index: int, timeout_ms: int,
) -> None:
    """Select the bar at ``index`` in a bar-chart visual via keyboard nav.

    QS renders charts to ``<canvas>``, so there's no DOM bar to click.
    The keyboard-accessible path (bar charts only — pie/donut don't
    expose it):

    1. Click the visual's container to give it focus.
    2. Tab 5 times to move focus into the inner bar group.
    3. Enter to highlight a bar.
    4. Right-arrow ``index`` times to cycle to the target bar.
    5. Enter to select (fires the same-sheet filter action).

    The visual must already be rendered and on-screen. Category order
    matches ``read_chart_categories``.
    """
    card = page.locator(
        f'[data-automation-id="analysis_visual"]:has('
        f'[data-automation-id="analysis_visual_title_label"]:text-is("{visual_title}"))'
    ).first
    card.wait_for(state="visible", timeout=timeout_ms)
    box = card.bounding_box()
    assert box, f"No bounding box for {visual_title!r}"
    # Click whitespace inside the card (just under the title) to focus
    # the visual without landing on the canvas / title / axis labels.
    page.mouse.click(
        box["x"] + box["width"] / 2,
        box["y"] + 30,
    )
    page.wait_for_timeout(300)
    for _ in range(5):
        page.keyboard.press("Tab")
        page.wait_for_timeout(100)
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    # Horizontal bar charts navigate with ArrowDown; try both to be
    # orientation-agnostic (extra presses on the wrong axis no-op).
    for _ in range(index):
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(120)
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(120)
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)


def read_visual_column_values(
    page: Page, visual_title: str, col_index: int,
) -> list[str]:
    """Return the text of every visible cell in column ``col_index`` within
    the table visual whose title matches ``visual_title``.

    Scoped to the specific visual (unlike the global ``sn-table-cell-{r}-{c}``
    lookup) so sibling tables can't contaminate the result. Caller is
    responsible for ensuring the visual is hydrated (use
    ``scroll_visual_into_view`` or ``count_table_total_rows`` first if the
    table is below-the-fold or paginated beyond the ~10-row viewport).
    """
    return page.evaluate(
        """({title, col}) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (!t || t.innerText.trim() !== title) continue;
                const out = [];
                v.querySelectorAll(
                    `[data-automation-id^="sn-table-cell-"]`
                ).forEach(c => {
                    const m = c.getAttribute('data-automation-id').match(
                        /sn-table-cell-(\\d+)-(\\d+)/
                    );
                    if (m && parseInt(m[2]) === col) {
                        out.push({row: parseInt(m[1]), text: c.innerText.trim()});
                    }
                });
                out.sort((a, b) => a.row - b.row);
                return out.map(o => o.text);
            }
            return null;
        }""",
        {"title": visual_title, "col": col_index},
    ) or []


def read_table_rows_dom(
    page: Page, visual_title: str,
) -> list[dict[str, str]]:
    """Read the DOM-visible rows of a QuickSight Table visual as a list
    of dicts keyed by column-header text, in display order.

    QS virtualizes — only ~10 rows are in the DOM at once — so this
    returns that window, not necessarily the whole table. Caller is
    responsible for getting the table on screen (use
    ``scroll_visual_into_view`` first; bump the page size if the full
    table is needed). Returns ``[]`` if the visual isn't found or has no
    body cells (empty table / still loading).

    Column headers come from the ``[data-automation-id="sn-table-column-N"]``
    divs (their ``.title`` span — the visible header text); body cells
    from ``sn-table-cell-{row}-{col}``. Headers and cells are zipped by
    *position* — the Nth header (left-to-right in the DOM) pairs with the
    Nth cell (smallest ``col`` first) in each row — so it's robust to QS's
    internal column-index numbering (the header's ``sn-table-column-N`` and
    the body's ``sn-table-cell-r-c`` use different ``N``/``c`` origins).
    """
    return page.evaluate(
        """(title) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (!t || t.innerText.trim() !== title) continue;
                // Column headers, left-to-right in DOM order.
                const headers = [];
                v.querySelectorAll('[data-automation-id^="sn-table-column-"]').forEach(c => {
                    if (!/sn-table-column-\\d+$/.test(c.getAttribute('data-automation-id'))) return;
                    const titleEl = c.querySelector('.table-title .title')
                        || c.querySelector('.title');
                    headers.push(titleEl ? titleEl.innerText.trim() : c.innerText.trim());
                });
                // Body cells -> { rowIdx: { colIdx: text } }
                const cellsByRow = {};
                v.querySelectorAll('[data-automation-id^="sn-table-cell-"]').forEach(c => {
                    const m = c.getAttribute('data-automation-id').match(/sn-table-cell-(\\d+)-(\\d+)/);
                    if (!m) return;
                    const r = parseInt(m[1], 10), col = parseInt(m[2], 10);
                    (cellsByRow[r] = cellsByRow[r] || {})[col] = c.innerText.trim();
                });
                const rows = [];
                Object.keys(cellsByRow).map(Number).sort((a, b) => a - b).forEach(r => {
                    const ordered = Object.keys(cellsByRow[r]).map(Number).sort((a, b) => a - b)
                        .map(col => cellsByRow[r][col]);
                    const row = {};
                    for (let i = 0; i < headers.length && i < ordered.length; i++) {
                        row[headers[i]] = ordered[i];
                    }
                    rows.push(row);
                });
                return rows;
            }
            return [];
        }""",
        visual_title,
    ) or []


def read_kpi_value(page: Page, visual_title: str) -> str:
    """Return the displayed big-number text of a KPI visual.

    QS renders the value inside ``.visual-x-center`` (the actual text node).
    The ``kpi-display-value`` automation-id wraps the container but its
    innerText sometimes includes the comparison label — prefer the center
    node and fall back to the automation-id if unavailable.

    Raises ``AssertionError`` if the visual isn't found or has no value.
    """
    value = page.evaluate(
        """(title) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (!t || t.innerText.trim() !== title) continue;
                const center = v.querySelector('.visual-x-center');
                if (center && center.innerText.trim()) return center.innerText.trim();
                const kpi = v.querySelector('[data-automation-id="kpi-display-value"]');
                if (kpi && kpi.innerText.trim()) return kpi.innerText.trim();
                return null;
            }
            return null;
        }""",
        visual_title,
    )
    assert value is not None, f"No KPI value found for {visual_title!r}"
    return value


def parse_kpi_number(text: str) -> float:
    """Strip ``$``, ``%``, ``,`` and ``K``/``M``/``B`` suffixes; return float.

    Handles QS's compact-number formatting: ``$1.2K`` -> 1200.0,
    ``45.3M`` -> 45_300_000.0. Unsuffixed strings parse straight.
    """
    s = text.strip().replace("$", "").replace(",", "").replace("%", "").strip()
    multiplier = 1.0
    if s and s[-1] in "KMB":
        multiplier = {"K": 1e3, "M": 1e6, "B": 1e9}[s[-1]]
        s = s[:-1]
    return float(s) * multiplier


def wait_for_kpi_text_nonempty(
    page: Page, visual_title: str, timeout_ms: int,
) -> str:
    """Poll ``read_kpi_value`` until the KPI is readable, returning its
    text. Useful pre-filter when the KPI hydrates after the visual
    mounts but before the test wants to baseline its value.
    """
    import time
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            value = read_kpi_value(page, visual_title)
            if value:
                return value
        except AssertionError:
            pass
        page.wait_for_timeout(250)
    raise TimeoutError(
        f"{visual_title!r} KPI never became readable within {timeout_ms}ms"
    )


def wait_for_kpi_value_to_change(
    page: Page, visual_title: str, before: str, timeout_ms: int,
) -> str:
    """Poll ``read_kpi_value`` until the displayed text differs from ``before``.
    Returns the new value. Raw string comparison — caller parses if needed.
    """
    import time
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        current = read_kpi_value(page, visual_title)
        if current != before:
            return current
        page.wait_for_timeout(250)
    raise TimeoutError(
        f"{visual_title!r} KPI value never changed from {before!r} "
        f"within {timeout_ms}ms"
    )


def wait_for_table_nonzero(
    page: Page, visual_title: str, timeout_ms: int,
) -> int:
    """Poll a table visual's row count until it's > 0 (or timeout).

    Returns the new row count. Use this after triggering a filter /
    dropdown pick when the assertion is "table not empty" rather than
    "count changed" — the change-based wait would false-timeout when
    the picked value happens to leave the count unchanged (e.g., an
    L2FT cascade pick where the value covers every row in the
    window). The "must remain non-empty" semantic is the actual
    regression guard for those tests.
    """
    page.wait_for_function(
        """({title}) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (!t || t.innerText.trim() !== title) continue;
                const rows = new Set();
                v.querySelectorAll('[data-automation-id^="sn-table-cell-"]').forEach(c => {
                    const m = c.getAttribute('data-automation-id').match(/sn-table-cell-(\\d+)-/);
                    if (m) rows.add(m[1]);
                });
                return rows.size > 0;
            }
            return false;
        }""",
        arg={"title": visual_title},
        timeout=timeout_ms,
    )
    return count_table_rows(page, visual_title)


def wait_for_dropdown_options_present(
    page: Page, dropdown_title: str, timeout_ms: int,
) -> list[str]:
    """Poll ``read_dropdown_options`` until it returns at least one
    option (or timeout).

    Replaces fixed-sleep waits in cascade-dropdown tests where one
    parameter pick (e.g., a Metadata Key) repopulates a downstream
    dropdown's options (e.g., Metadata Value). The dropdown is closed
    between reads, so this is a Python-side retry loop rather than a
    ``page.wait_for_function`` JS poll. Fail-fasts on a real "options
    never populate" regression; tight on the happy path.
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    last: list[str] = []
    while time.monotonic() < deadline:
        last = read_dropdown_options(page, dropdown_title, timeout_ms=2_000)
        if last:
            return last
        time.sleep(0.25)  # typing-smell: ignore[no-sleep]: 250ms inter-poll backoff inside a bounded retry loop with overall timeout
    return last


def wait_for_table_rows_to_change(
    page: Page, visual_title: str, before: int, timeout_ms: int,
) -> int:
    """Poll a table visual's row count until it differs from ``before``.

    Returns the new row count. Raises a Playwright timeout if the count
    never changes. Use this after triggering a filter / drill action so
    the test doesn't sleep blindly.
    """
    page.wait_for_function(
        """({title, before}) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (!t || t.innerText.trim() !== title) continue;
                const rows = new Set();
                v.querySelectorAll('[data-automation-id^="sn-table-cell-"]').forEach(c => {
                    const m = c.getAttribute('data-automation-id').match(/sn-table-cell-(\\d+)-/);
                    if (m) rows.add(m[1]);
                });
                return rows.size !== before;
            }
            return false;
        }""",
        arg={"title": visual_title, "before": before},
        timeout=timeout_ms,
    )
    return count_table_rows(page, visual_title)


def set_date_range(
    page: Page, start: str, end: str, timeout_ms: int,
    picker_indices: tuple[int, int] = (0, 1),
) -> None:
    """Fill the two date-range pickers and commit each with Enter.

    ``start`` / ``end`` use QuickSight's accepted text format (``YYYY/MM/DD``).
    ``picker_indices`` defaults to (0, 1) — the first date-range control on
    the active sheet. Override when a sheet has multiple ranges.

    For parameter-driven date pickers (``ParameterDateTimePicker``,
    not ``FilterDateTimePicker.type=DATE_RANGE``), use
    :func:`set_parameter_datetime_value` instead — those render as
    separate sheet controls each with their own scoped DOM, not a
    shared 0-and-1-indexed range widget.
    """
    for picker_index, value in zip(picker_indices, (start, end)):
        selector = f'[data-automation-id="date_picker_{picker_index}"]'
        page.wait_for_selector(selector, timeout=timeout_ms, state="visible")
        page.fill(selector, value)
        page.press(selector, "Enter")


def set_parameter_datetime_value(
    page: Page, control_title: str, value: str, timeout_ms: int,
) -> None:
    """Fill a single ``ParameterDateTimePicker`` control by its title.

    Each ParameterDateTimePicker on a sheet renders as its own
    ``sheet_control`` card scoped by ``data-automation-context`` to
    the control's title. The date input lives at
    ``data-automation-id="date_picker_0"`` *within* that card. Targeting
    by title avoids the cross-control collision (each card has its own
    locally-indexed picker).

    ``value`` uses QuickSight's accepted text format (``YYYY/MM/DD``).
    """
    card_selector = (
        f'[data-automation-id="sheet_control"]'
        f'[data-automation-context="{control_title}"]'
    )
    picker_selector = (
        f'{card_selector} [data-automation-id="date_picker_0"]'
    )
    page.wait_for_selector(picker_selector, timeout=timeout_ms, state="visible")
    page.fill(picker_selector, value)
    page.press(picker_selector, "Enter")


def set_slider_range(
    page: Page, control_title: str, low: int | None, high: int | None,
    timeout_ms: int,
) -> None:
    """Set a RANGE FilterSliderControl's min/max via its backing text inputs.

    QS renders each range slider with two MUI text inputs
    (``sheet_control_range_slider_min`` / ``_max``) wired to React state.
    Dragging the thumbs is fragile in Playwright, but filling the inputs
    and blurring them commits the value reliably. Pass ``None`` to leave
    a bound untouched.
    """
    card_selector = (
        f'[data-automation-id="sheet_control"]'
        f'[data-automation-context="{control_title}"]'
    )
    page.wait_for_selector(card_selector, timeout=timeout_ms, state="visible")
    for bound, value in (("min", low), ("max", high)):
        if value is None:
            continue
        selector = (
            f'{card_selector} '
            f'[data-automation-id="sheet_control_range_slider_{bound}"]'
        )
        loc = page.locator(selector).first
        loc.click(timeout=timeout_ms)
        loc.fill(str(value), timeout=timeout_ms)
        loc.press("Enter", timeout=timeout_ms)


# MUI v4 renders some FilterControl options inside ``[role="listbox"]``
# (most sheet controls) and others directly in the value-menu popover
# (Show-Only-X single-selects on Settlements/Payments). Match both.
_OPTION_SELECTOR = (
    '[role="listbox"] [role="option"], '
    '[data-automation-id="sheet_control_value-menu"] [role="option"]'
)
_SELECTED_OPTION_SELECTOR = (
    '[role="listbox"] [role="option"][aria-selected="true"], '
    '[data-automation-id="sheet_control_value-menu"] [role="option"][aria-selected="true"]'
)


def _open_control_dropdown(page: Page, control_title: str, timeout_ms: int) -> None:
    """Open the FilterControl popover for the named sheet control.

    QuickSight renders each control as
    ``[data-automation-id="sheet_control"][data-automation-context="<title>"]``
    with the value picker at ``sheet_control_value`` (a Material-UI Select
    combobox). Opens the popover and waits for the listbox to be visible.
    """
    card_selector = (
        f'[data-automation-id="sheet_control"]'
        f'[data-automation-context="{control_title}"]'
    )
    page.wait_for_selector(card_selector, timeout=timeout_ms, state="visible")
    # MUI mounts the listbox in a portal; aria-haspopup="listbox" expands.
    # The first click sometimes no-ops if the sheet just mounted and the
    # combobox's onClick handler hasn't attached — retry until the listbox
    # appears or timeout.
    value_selector = (
        f'{card_selector} [data-automation-id="sheet_control_value"]'
    )
    page.locator(value_selector).first.click(timeout=timeout_ms)
    # MUI v4 sometimes renders options under role="listbox" inside the menu
    # popover, but some control instances skip the listbox role and put
    # options directly in the popover. Match either shape, but scope to
    # the just-opened control's popover so other (stale) popovers don't
    # pollute the option set.
    popover_selector = (
        f'[data-automation-id="sheet_control_value-menu"]'
        f'[data-automation-context="{control_title}"]'
    )
    page.wait_for_selector(
        f'{popover_selector} [role="option"], [role="listbox"] [role="option"]',
        timeout=timeout_ms, state="visible",
    )


def set_dropdown_value(
    page: Page, control_title: str, value: str, timeout_ms: int,
) -> None:
    """Pick a single value from a SINGLE_SELECT FilterControl by title.

    Opens the dropdown for ``control_title`` and clicks the option whose
    text equals ``value``. Use ``clear_dropdown`` to reset to "All".
    Dismisses the popover with Escape so subsequent visual interactions
    aren't blocked by the listbox overlay.
    """
    _open_control_dropdown(page, control_title, timeout_ms)
    page.locator(
        _OPTION_SELECTOR, has_text=value,
    ).first.click(timeout=timeout_ms)
    page.keyboard.press("Escape")


def set_multi_select_values(
    page: Page, control_title: str, values: list[str], timeout_ms: int,
) -> None:
    """Pick one or more values from a MULTI_SELECT FilterControl by title.

    Deselects any currently-checked options first (via the option's
    aria-selected state), then ticks only the requested values. Commits
    by pressing Escape to dismiss the popover.
    """
    _open_control_dropdown(page, control_title, timeout_ms)
    # MULTI_SELECT controls always render in ``[role="listbox"]``;
    # restrict to that path to avoid duplicate matches from the broader
    # popover selector used for SINGLE_SELECT Show-Only-X controls.
    mselect = '[role="listbox"] [role="option"]'
    selected_labels = page.evaluate(
        """() => Array.from(
            document.querySelectorAll(
                '[role="listbox"] [role="option"][aria-selected="true"]'
            )
        ).map(o => o.innerText.trim())"""
    )
    targets = set(values)
    for label in selected_labels:
        if label in targets:
            targets.discard(label)
            continue
        page.locator(mselect, has_text=label).first.click(timeout=timeout_ms)
    for value in targets:
        page.locator(mselect, has_text=value).first.click(timeout=timeout_ms)
    page.keyboard.press("Escape")


def read_dropdown_options(
    page: Page, control_title: str, timeout_ms: int,
) -> list[str]:
    """Return the data-value option labels in a FilterControl dropdown.

    Opens the dropdown for ``control_title``, reads every
    ``[role="option"]`` label, dismisses the popover, and returns the
    list with sentinel entries filtered out (``"Select all"``, ``"All"``,
    blanks). Used by data-agnostic e2e tests that need to pick a
    valid value from the dropdown without hardcoding what the values
    are — e.g., "pick the first selectable value to narrow the table."
    """
    _open_control_dropdown(page, control_title, timeout_ms)
    labels = page.evaluate(
        """() => Array.from(
            document.querySelectorAll('[role="listbox"] [role="option"]')
        ).map(o => o.innerText.trim())"""
    )
    page.keyboard.press("Escape")
    return [
        label for label in labels
        if label and label not in ("Select all", "All")
    ]


def clear_dropdown(page: Page, control_title: str, timeout_ms: int) -> None:
    """Reset a FilterControl to its "all values" default.

    Opens the dropdown and clicks the "Select all" / "All" entry. Works
    for both SINGLE_SELECT and MULTI_SELECT controls — QuickSight uses
    the same listbox markup for both.
    """
    _open_control_dropdown(page, control_title, timeout_ms)
    options = page.locator(_OPTION_SELECTOR)
    for label in ("Select all", "All"):
        match = options.filter(has_text=label).first
        if match.count() > 0:
            match.click(timeout=timeout_ms)
            page.keyboard.press("Escape")
            return
    # SINGLE_SELECT: no listbox clear-all entry. Close popover and open
    # the control's options menu (``⋯``), then click its "Clear" item.
    page.keyboard.press("Escape")
    card_selector = (
        f'[data-automation-id="sheet_control"]'
        f'[data-automation-context="{control_title}"]'
    )
    page.locator(
        f'{card_selector} [data-automation-id="sheet_control_menu_button"]'
    ).first.click(timeout=timeout_ms)
    page.wait_for_selector(
        '[role="menu"] [role="menuitem"]', timeout=timeout_ms, state="visible",
    )
    items = page.locator('[role="menu"] [role="menuitem"]')
    for label in ("Clear selection", "Clear", "Reset"):
        match = items.filter(has_text=label).first
        if match.count() > 0:
            match.click(timeout=timeout_ms)
            return
    raise AssertionError(
        f"No Clear/Reset item in options menu for {control_title!r}"
    )


def screenshot(page: Page, name: str, subdir: str | None = None) -> Path:
    """Save a screenshot under tests/e2e/screenshots/[subdir/].

    Pass ``subdir`` to namespace outputs per-app (e.g. ``"payment_recon"`` or
    ``"account_recon"``) so the two apps' screenshots don't overwrite each
    other when they happen to share a test name.
    """
    target_dir = SCREENSHOT_DIR / subdir if subdir else SCREENSHOT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    return path
