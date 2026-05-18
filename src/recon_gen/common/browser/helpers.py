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
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Generator, TypeVar

from recon_gen.common.env_keys import (
    EnvVarInvalid,
    RECON_E2E_SCREENSHOT_DIR,
    RECON_E2E_USER_ARN,
    RECON_GEN_RUN_DIR,
    RECON_GEN_TRACE_ALL,
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
# ``testpaths = ["tests"]``); override via ``RECON_E2E_SCREENSHOT_DIR``
# if you need a different sink. Production CLI screenshot capture
# uses an explicit ``output_dir`` arg to ``ScreenshotHarness`` and
# does NOT touch this constant.
SCREENSHOT_DIR = (
    RECON_E2E_SCREENSHOT_DIR.get_or_none() or Path("tests/e2e/screenshots")
).resolve()


def get_user_arn() -> str:
    """Return the QuickSight user ARN to embed dashboards for.

    Reads ``RECON_E2E_USER_ARN``. Raises ``RuntimeError`` when unset —
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
    arn = RECON_E2E_USER_ARN.get_or_none()
    if not arn:
        raise RuntimeError(
            "RECON_E2E_USER_ARN is not set. Embedding requires a "
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

    On exception inside the ``with`` body, captures six diagnostics
    per failing test:

    - ``screenshot.png`` (or ``<test_id>.png`` in legacy mode) —
      full-page screenshot of the failure state
    - ``dom.html`` — serialized DOM of the top-level frame at failure
      moment (``page.content()``). Pairs with the screenshot: the
      pixels show what's visually there, the DOM shows what the
      test's selectors were actually looking at. Critical for
      "click target not found" / "control didn't mount" failures.
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
    - ``ws_frames.txt`` (AA.A.qs-triage.1) — QS-only: every text
      WebSocket frame the page sent (QS's data-layer protocol —
      ``START_VIS`` carries the actual parameter substitution QS
      made for the visual's CustomSql). Sink lives on
      ``QsEmbedDriver._ws_frames`` and is attached to the page as
      ``page._qs_gen_ws_frames_sink``; App2-only tests leave the
      sink empty and land an empty file (signal that the test
      didn't open a QS embed).
    - ``trace.zip`` (Y.2.gate.c.11) — Playwright trace bundle:
      full action timeline, DOM snapshots per action, screenshots,
      network, and console. Open with ``playwright show-trace
      trace.zip``. Plain-text artifacts (``dom.html``) cover the
      grep-able path; trace.zip is for full-UI replay.

    Output destination depends on ``RECON_GEN_RUN_DIR``:

    - **Set** (running under the test layer chain runner):
      ``$RECON_GEN_RUN_DIR/browser/<test_id>/{screenshot.png,dom.html,
      console.txt,qs_errors.txt,network.txt,ws_frames.txt,trace.zip}``
      — per-test directory so artifacts cluster cleanly.
    - **Unset** (legacy ``./run_e2e.sh`` / direct ``pytest`` invocation):
      ``tests/e2e/screenshots/_failures/<test_id>.png`` etc., flat
      directory with per-file ``<test_id>_`` prefix to disambiguate.
      Trace.zip is NOT written in legacy mode (no run-dir to put it in).

    The test_id is snapshotted at ``webkit_page`` entry (when pytest's
    ``PYTEST_CURRENT_TEST`` env var is reliably set inside the test body)
    rather than re-read inside the ``except`` handler — that handler
    can run during fixture teardown after pytest has cleared the var,
    which would silently demote captures to ``unknown/``.

    Trace capture policy:
    - On exception → trace always written (under the run-dir mode).
    - On clean exit → trace written iff ``RECON_GEN_TRACE_ALL=1`` is set
      (operator opt-in for "I want the full trace even on green tests";
      flag plumbed by ``Y.2.gate.c.7``).

    Capture is best-effort: each capture function catches its own
    exceptions and emits a ``[CAPTURE FAILURE] <artifact>: <type>: <msg>``
    line to stderr (loud-fail). The original assertion still bubbles
    up unmasked — but a regression in the capture path is visible in
    the layer's ``stderr.log`` instead of being invisible until the
    next forensic session.
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
        # Snapshot the test ID at entry — pytest sets PYTEST_CURRENT_TEST
        # for the duration of the test body, but it can be cleared by the
        # time fixture teardown runs the ``except`` handler. Resolving
        # the test_id once here pins each capture to the right
        # ``browser/<test_id>/`` dir regardless of when the exception
        # actually surfaces.
        test_id = _test_id_from_pytest_env()
        # Attach sinks to the page so ``trigger_failure_capture`` can read
        # them from outside the ``with`` block. This bridges the pytest
        # fixture-vs-direct-raise gap: pytest's yield-fixture semantics
        # don't re-throw test-body exceptions back into the fixture's
        # generator, so the ``except BaseException:`` below never fires
        # for a typical e2e test failure. The fixture's teardown (or
        # ``pytest_runtest_makereport`` hook) reaches in via these attrs
        # and calls ``trigger_failure_capture(page)`` directly.
        page._qs_gen_console_sink = console_messages  # type: ignore[attr-defined]: monkey-attach sink for trigger_failure_capture
        page._qs_gen_network_sink = network_responses  # type: ignore[attr-defined]: see _qs_gen_console_sink above
        page._qs_gen_test_id = test_id  # type: ignore[attr-defined]: see _qs_gen_console_sink above
        failed = False
        try:
            yield page
        except BaseException:
            # Direct-raise path (eg unit tests that `raise` inside the
            # ``with`` block — see ``tests/unit/test_browser_trace_smoke``).
            # Pytest e2e fixtures take the explicit-trigger path instead;
            # see ``trigger_failure_capture``.
            failed = True
            _capture_failure_screenshot(page, test_id)
            _capture_failure_dom(page, test_id)
            _capture_failure_console(console_messages, test_id)
            _capture_failure_qs_errors(page, test_id)
            _capture_failure_network(network_responses, test_id)
            # AA.A.qs-triage.1 — QS-side ws_frames sink attaches in
            # ``QsEmbedDriver.__init__``; App2-only tests never set it.
            # getattr-with-None lets the unset case land an empty file.
            _capture_failure_ws_frames(
                getattr(page, "_qs_gen_ws_frames_sink", None) or [],
                test_id,
            )
            raise
        finally:
            # Track "did the fixture caller already trigger capture
            # explicitly" so we don't double-emit trace.zip (and so the
            # trace-saving decision matches the actual outcome). The
            # explicit trigger sets ``page._qs_gen_capture_triggered``.
            triggered_externally = bool(
                getattr(page, "_qs_gen_capture_triggered", False),
            )
            # Trigger may have rewritten the test_id (so all 6 artifacts
            # cluster under one dir even when the trigger passes an
            # override). Read the latest value back from the page.
            final_test_id: str = getattr(page, "_qs_gen_test_id", None) or test_id
            _stop_and_maybe_save_trace(
                context,
                failed=failed or triggered_externally,
                test_id=final_test_id,
            )
            context.close()
            browser.close()


def trigger_failure_capture(
    page: Page, *, test_id: str | None = None, cfg: object | None = None,
) -> None:
    """Public-API capture trigger for the pytest-fixture path.

    Pytest's yield-fixture semantics don't re-throw test-body exceptions
    back into the fixture's generator — so ``webkit_page``'s
    ``except BaseException:`` handler never fires for a typical e2e
    test failure. The fixture's teardown (post-yield code, after
    consulting ``request.node.rep_call.failed`` via the standard
    ``pytest_runtest_makereport`` hook) calls this function instead to
    drop the same artifacts.

    Reads the sinks ``webkit_page`` attached to the page
    (``_qs_gen_console_sink``, ``_qs_gen_network_sink``,
    ``_qs_gen_test_id``). Falls back to ``_test_id_from_pytest_env()``
    when no test_id is passed and no page-attached default exists.

    ``cfg`` is the Config dataclass; when present, also dumps
    ``db_counts.txt`` (per-table row counts for every relation matching
    ``cfg.db_table_prefix_*``). Pass ``None`` to skip — the QS-side
    artifacts still land. Conftest's ``maybe_capture_on_failure``
    resolves cfg from the ``cfg`` fixture and forwards it.

    Sets ``page._qs_gen_capture_triggered = True`` so ``webkit_page``'s
    ``finally`` block knows to save the trace.zip (otherwise the trace
    would only land when the exception bubbled through the
    ``except``).

    Idempotent — calling twice with the same test_id just overwrites
    the artifacts (last call wins).
    """
    resolved_test_id: str
    if test_id is not None:
        resolved_test_id = test_id
    else:
        resolved_test_id = (
            getattr(page, "_qs_gen_test_id", None) or _test_id_from_pytest_env()
        )
    # Sinks are list[str] attached by ``webkit_page``; cast through the
    # ``getattr`` Any to satisfy strict pyright without an explicit Any.
    console_messages: list[str] = getattr(page, "_qs_gen_console_sink", None) or []
    network_responses: list[str] = getattr(page, "_qs_gen_network_sink", None) or []
    # Signal to webkit_page's finally block: trace.zip should land
    # alongside the other artifacts. We also overwrite the page-attached
    # test_id so the finally block's trace-save uses the same dir as the
    # 5 artifacts we just wrote (otherwise trace would orphan to the
    # webkit_page-entry test_id while screenshot/dom/etc go to the
    # override-passed test_id — a confusing split).
    page._qs_gen_capture_triggered = True  # type: ignore[attr-defined]: signal trace-save to webkit_page finally
    page._qs_gen_test_id = resolved_test_id  # type: ignore[attr-defined]: align trace dir with artifact dir
    _capture_failure_screenshot(page, resolved_test_id)
    _capture_failure_dom(page, resolved_test_id)
    _capture_failure_console(console_messages, resolved_test_id)
    _capture_failure_qs_errors(page, resolved_test_id)
    _capture_failure_network(network_responses, resolved_test_id)
    # AA.A.qs-triage.1 — QS-side ws_frames sink attaches in
    # ``QsEmbedDriver.__init__``; App2-only tests never set it.
    # getattr-with-None lets the unset case land an empty file.
    ws_frames: list[str] = getattr(page, "_qs_gen_ws_frames_sink", None) or []
    _capture_failure_ws_frames(ws_frames, resolved_test_id)
    if cfg is not None:
        _capture_failure_db_counts(cfg, resolved_test_id)


def _stop_and_maybe_save_trace(
    context: object, *, failed: bool, test_id: str,
) -> None:
    """Y.2.gate.c.11 — finalize the Playwright trace.

    Saves + unpacks to ``$RECON_GEN_RUN_DIR/browser/<test_id>/`` when:
    - ``failed`` is True (always capture failure traces), OR
    - ``RECON_GEN_TRACE_ALL=1`` (operator opt-in for full traces on green).

    Otherwise discards the trace (call ``stop()`` with no path).
    No-op when ``RECON_GEN_RUN_DIR`` isn't set — there's nowhere to put
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

    ``test_id`` is passed in (snapshotted by the caller at ``webkit_page``
    entry) rather than re-derived here — pytest may have cleared
    ``PYTEST_CURRENT_TEST`` by the time fixture teardown runs.

    Errors emit a loud-fail ``[CAPTURE FAILURE]`` line to stderr;
    they don't re-raise (sidecar contract).
    """
    import zipfile

    # Sidecar contract — swallow registry validator failures (e.g.
    # RECON_GEN_RUN_DIR pointing at a non-dir) the same way the surrounding
    # try/except swallows OSError. A misconfigured env var must not
    # fail the wrapped browser test.
    try:
        run_dir_path = RECON_GEN_RUN_DIR.get_or_none()
    except EnvVarInvalid:
        run_dir_path = None
    run_dir = str(run_dir_path) if run_dir_path is not None else None
    trace_all = bool(RECON_GEN_TRACE_ALL.get_or_none())
    should_save = bool(run_dir) and (failed or trace_all)
    try:
        if should_save:
            trace_dir = (
                Path(run_dir) / "browser" / test_id  # type: ignore[arg-type]: run_dir narrowed truthy by the bool() above
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
            except Exception as exc:
                _warn_capture_failure("trace/", exc)
        else:
            context.tracing.stop()  # type: ignore[attr-defined]: Playwright duck-typed tracing API
    except Exception as exc:
        _warn_capture_failure("trace.zip", exc)


def _capture_dir_for(test_id: str) -> Path:
    """Y.2.gate.c.11 — pick where per-failure dumps land.

    Returns ``$RECON_GEN_RUN_DIR/browser/<test_id>/`` when the runner
    env is set, else the legacy ``<SCREENSHOT_DIR>/_failures/`` flat
    dir.
    """
    # Soft-fall through on bad value (matches the sidecar pattern in
    # ``_finalize_browser_capture``).
    try:
        run_dir = RECON_GEN_RUN_DIR.get_or_none()
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
        run_dir_present = RECON_GEN_RUN_DIR.get_or_none() is not None
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
    response — plus every App2 ``/visuals/*/data`` response regardless
    of status — accumulates into ``sink``. Format:
    ``<status> <method> <url>`` per response.

    Non-2xx by default because QS dashboards make hundreds of requests
    and the surfaced failures (4xx / 5xx / network errors) are usually
    enough. ``/visuals/*/data`` is the App2 per-visual data endpoint —
    a 200 with empty rows looks identical to "no request fired" in the
    default-filtered log, so AA.B.5.followon's class of bug (pick
    fires URL but server returns 0 because the pick value never made
    it into the right request) goes invisible. Capturing every visual-
    data request keeps the per-pick request fan-out reconstructable
    from the artifact alone — no need to re-deploy with extra logging.
    Listener is wrapped in broad ``except`` so a misbehaving handler
    can't abort the page lifecycle.
    """
    def _on_response(response: object) -> None:
        try:
            status = getattr(response, "status", 0)
            request = getattr(response, "request", None)
            method = getattr(request, "method", "?") if request else "?"
            url = getattr(response, "url", "")
            is_visual_data = "/visuals/" in url and "/data" in url
            if 200 <= status < 300 and not is_visual_data:
                return
            sink.append(f"{status} {method} {url}")
        except Exception:
            pass

    page.on("response", _on_response)


# Filename-portable charset: ASCII alphanumerics + `_`, `-`, `[`, `]`, `.`.
# Brackets stay so parametrized IDs disambiguate (`[qs-Rail]` vs `[qs-Bundle]`);
# everything else (spaces, em-dashes, parens, colons, slashes, etc.) collapses
# to `_` so the resulting filename works on every filesystem the artifact
# bundle has to traverse (macOS APFS, ext4, NTFS, GHA artifact upload, zip).
_TEST_ID_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9_\-\[\].]+")


def _sanitize_test_id(raw: str) -> str:
    """Collapse runs of non-portable chars in a test ID to a single ``_``."""
    return _TEST_ID_SAFE_CHARS_RE.sub("_", raw)


def _test_id_from_pytest_env(raw: str | None = None) -> str:
    """Derive a filename-safe test ID from ``PYTEST_CURRENT_TEST``.

    pytest sets the env var to a string like
    ``"tests/e2e/test_foo.py::test_bar (call)"`` (or with a class
    segment + parametrization brackets). Strip the trailing
    ``(setup|call|teardown)`` phase suffix, convert ``/`` + ``::``
    to underscores, then sanitize remaining non-portable chars via
    ``_sanitize_test_id`` so the result works on every filesystem.
    ``"unknown"`` when the env var is unset — covers running outside
    pytest or after pytest cleared the var on test exit.
    """
    if raw is None:
        raw = os.environ.get("PYTEST_CURRENT_TEST", "")
    if not raw:
        return "unknown"
    after_basics = (
        raw.split(" (")[0]
        .replace("/", "_")
        .replace("::", "__")
        .replace(".py", "")
    )
    # Parametrized IDs from pytest carry spaces / em-dashes / parens inside
    # `[…]` (e.g. `[qs-Money Trail — Hop-by-Hop]`). The basic replace chain
    # above only strips the path separators; the inner-bracket content can
    # still be unfriendly to downstream consumers (GHA artifact zips, Windows,
    # shell-glob patterns). Sanitize here so the test_id is portable across
    # ALL the places the captured artifact has to land.
    return _sanitize_test_id(after_basics)


def _warn_capture_failure(artifact_name: str, exc: BaseException) -> None:
    """Loud-fail sidecar for capture functions.

    Capture must NEVER mask the original test failure (closed page,
    missing env var, full disk, OS quota, etc.) — historically each
    `_capture_failure_*` swallowed exceptions silently. That
    bit us when the dump path quietly stopped producing artifacts and
    the next failure had no diagnostics. New contract: still don't
    raise (test failure stays the surfaced one), but emit a `[CAPTURE
    FAILURE]` line to stderr so a future regression is visible in the
    test layer's `stderr.log` instead of being invisible until the
    next forensic session.
    """
    print(
        f"[CAPTURE FAILURE] {artifact_name}: {type(exc).__name__}: {exc}",
        file=sys.stderr,
    )


def _capture_failure_screenshot(page: Page, test_id: str) -> None:
    """Best-effort failure screenshot. Writes to
    ``<capture_dir>/screenshot.png`` (or legacy ``<test_id>.png``).
    Exceptions are caught + logged to stderr (loud-fail) so the
    original assertion still bubbles up unmasked, but capture
    regressions are visible in the layer's stderr.log.
    """
    try:
        path = _capture_path("screenshot.png", test_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
    except Exception as exc:
        _warn_capture_failure("screenshot.png", exc)


def _capture_failure_dom(page: Page, test_id: str) -> None:
    """Dump the live DOM to ``<capture_dir>/dom.html`` (or legacy
    ``<test_id>_dom.html``).

    Single most-useful diagnostic for "click target not found" /
    "control didn't mount" failures — the screenshot shows what's
    visually there, the DOM shows what the test's selectors were
    actually looking at. ``page.content()`` returns the serialized
    HTML of the top-level document; iframe contents aren't inlined
    (QS embeds the dashboard in a same-origin iframe — the iframe's
    DOM is captured in the trace.zip's snapshot stream, but for a
    grep-able plain-text artifact the top-level frame is the start
    of the trail).
    """
    try:
        path = _capture_path("dom.html", test_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page.content(), encoding="utf-8")
    except Exception as exc:
        _warn_capture_failure("dom.html", exc)


def _capture_failure_console(messages: list[str], test_id: str) -> None:
    """Dump accumulated JS console + pageerror messages to
    ``<capture_dir>/console.txt`` (or legacy ``<test_id>_console.txt``).
    Empty file when nothing was logged (so the artifact bundle
    reliably contains the file and the absence of content is itself
    a signal).
    """
    try:
        path = _capture_path("console.txt", test_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(messages), encoding="utf-8")
    except Exception as exc:
        _warn_capture_failure("console.txt", exc)


def _capture_failure_network(responses: list[str], test_id: str) -> None:
    """Dump the captured non-2xx HTTP responses to
    ``<capture_dir>/network.txt`` (or legacy ``<test_id>_network.txt``).
    Empty file when every request succeeded (so the artifact bundle
    reliably contains the file and the absence of content is itself
    a signal).
    """
    try:
        path = _capture_path("network.txt", test_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(responses), encoding="utf-8")
    except Exception as exc:
        _warn_capture_failure("network.txt", exc)


def _capture_failure_ws_frames(frames: list[str], test_id: str) -> None:
    """Dump the captured QuickSight WebSocket frames to
    ``<capture_dir>/ws_frames.txt`` (or legacy
    ``<test_id>_ws_frames.txt``). QS-only artifact: the sink is wired
    by ``QsEmbedDriver``; ``App2Driver`` doesn't use WebSockets and
    leaves the sink ``None``, so this file appears only for QS-driven
    tests (mirrors ``qs_errors.txt`` which is also QS-only).

    AA.A.qs-triage.1 — every QS data-layer round-trip rides one
    long-lived WebSocket; the frames are the ground truth for "what
    parameter value did QS actually substitute into the picker", which
    a DOM-only triage can only approximate. Without this artifact the
    only way to see the post-pick START_VIS payload was to redeploy
    the JS bootstrap with a ``console.debug`` tracer and re-run, which
    burns a CI cycle per question. With it, every failing QS e2e drops
    the full frame log alongside the screenshot and DOM dump.

    Empty file when nothing was captured (so the artifact bundle
    reliably contains the file and the absence of content is itself
    a signal — e.g. App2-only test that never opened a QS embed).
    """
    try:
        path = _capture_path("ws_frames.txt", test_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(frames), encoding="utf-8")
    except Exception as exc:
        _warn_capture_failure("ws_frames.txt", exc)


def _capture_failure_qs_errors(page: Page, test_id: str) -> None:
    """Dump the text of any QuickSight error overlays visible on the
    page to ``<capture_dir>/qs_errors.txt`` (or legacy
    ``<test_id>_qs_errors.txt``). Targets the well-known QS error
    markers — the "Failed to load visual" tooltip, the visual-error
    icon's accessible label, and error banners. Empty file when
    nothing matched.
    """
    try:
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
    except Exception as exc:
        _warn_capture_failure("qs_errors.txt", exc)


def _capture_failure_db_counts(
    cfg: object, test_id: str,  # typing-smell: ignore[explicit-any]: cfg typed as Config but importing it would force a cycle from helpers.py — soft-duck-type
) -> None:
    """Dump per-table row counts from the demo DB to
    ``<capture_dir>/db_counts.txt`` (or legacy
    ``<test_id>_db_counts.txt``).

    Answers the first question every "visual rendered blank" triage
    asks: "is the data even there?". A blank Sankey is two failure
    modes superimposed:

      - Backend OK, frontend broken: matview has N rows but the swap
        landed in the wrong container / the test selector mis-matched
        / HTMX stalled mid-flight.
      - Backend empty: matview has 0 rows because seed didn't fire,
        matview refresh skipped, or the parameter narrow excluded
        everything.

    Without this dump, triage means inspecting the DOM artifact + cross-
    referencing the API leg's pass/fail. With it, the first line of the
    `db_counts.txt` answers it before any DOM archaeology — same role
    the in-dashboard "Info" sheet plays for live runs (see
    ``common/sheets/app_info.py``).

    Format: ``<table_name>: <count>`` per line, sorted by name. Tables
    enumerated by querying the dialect's catalog for objects (tables +
    views + matviews) starting with ``<cfg.db_table_prefix>_``. Empty
    file is a signal — either prefix is wrong or schema was never
    applied (the very thing this is meant to surface).

    Sidecar contract: swallow ALL exceptions to a stderr warning. A
    failed DB roundtrip during capture must not mask the original test
    failure.
    """
    try:
        from recon_gen.common.db import connect_demo_db
        from recon_gen.common.sql.dialect import Dialect

        path = _capture_path("db_counts.txt", test_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        prefix = str(getattr(cfg, "db_table_prefix", "") or "")
        dialect = getattr(cfg, "dialect", None)
        if not prefix or dialect is None:
            path.write_text(
                f"# capture skipped: cfg missing db_table_prefix or dialect\n"
                f"# prefix={prefix!r} dialect={dialect!r}\n",
                encoding="utf-8",
            )
            return

        conn = connect_demo_db(cfg)  # type: ignore[arg-type]: cfg duck-typed to Config (see header note)
        try:
            # sqlite3.Cursor doesn't implement the context-manager
            # protocol (unlike psycopg + oracledb), so use try/finally
            # for portable resource handling.
            cur = conn.cursor()
            try:
                # Dialect-aware enumeration of every relation (table /
                # view / matview) whose name starts with the prefix.
                # Each name then gets a `SELECT COUNT(*)` — bounded by
                # the number of prefixed objects (matview list is
                # ~30 per L2 instance — under 1s end-to-end on Aurora).
                if dialect is Dialect.POSTGRES:
                    cur.execute(
                        "SELECT relname FROM pg_class "
                        "WHERE relkind IN ('r', 'm', 'v') AND relname LIKE %s "
                        "ORDER BY relname",
                        (f"{prefix}_%",),
                    )
                    names = [row[0] for row in cur.fetchall()]
                elif dialect is Dialect.ORACLE:
                    # Oracle uppercases identifiers; prefix is case-insensitive
                    # in our cfg so query both layers.
                    cur.execute(
                        "SELECT object_name FROM user_objects "
                        "WHERE object_type IN ('TABLE', 'VIEW', "
                        "'MATERIALIZED VIEW') "
                        "AND UPPER(object_name) LIKE UPPER(:1) "
                        "ORDER BY object_name",
                        (f"{prefix}_%",),
                    )
                    names = [row[0] for row in cur.fetchall()]
                elif dialect is Dialect.SQLITE:
                    cur.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type IN ('table', 'view') AND name LIKE ? "
                        "ORDER BY name",
                        (f"{prefix}_%",),
                    )
                    names = [row[0] for row in cur.fetchall()]
                else:
                    path.write_text(
                        f"# capture skipped: unsupported dialect {dialect!r}\n",
                        encoding="utf-8",
                    )
                    return

                lines: list[str] = []
                for name in names:
                    try:
                        # Identifier is from a catalog query against our
                        # own prefix — safe to interpolate. No bind path
                        # because table names can't be parameterized.
                        cur.execute(f"SELECT COUNT(*) FROM {name}")
                        row = cur.fetchone()
                        count = row[0] if row else "?"
                        lines.append(f"{name}: {count}")
                    except Exception as exc:
                        lines.append(f"{name}: ERROR {type(exc).__name__}: {exc}")
                path.write_text("\n".join(lines), encoding="utf-8")
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as exc:
        _warn_capture_failure("db_counts.txt", exc)


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


def visual_is_empty(page: Page, visual_title: str) -> bool:
    """Cheap DOM probe — does this visual show QS's empty-state overlay?

    QS mounts a ``[data-automation-id="visual-overlay-title"]`` element
    with ``data-automation-context="No data"`` inside any visual whose
    backing dataset returned zero rows (table / Sankey / chart / KPI).
    The overlay mounts at the same time the visual's frame renders —
    typically within a few hundred ms of the sheet load — so a positive
    return from this probe is high-signal: the visual is mounted AND
    confirmed empty, no row-fetch race to wait out.

    Use this BEFORE ``scroll_visual_into_view`` to short-circuit empty
    cases. ``scroll_visual_into_view``'s ``wait_for_cells=True`` mode
    waits for ``sn-table-cell-0-0`` to appear, which empty tables never
    mount — without this probe the helper times out at ``timeout_ms``
    on every empty visual. Returns False if the visual isn't found
    (let downstream selectors raise the "no visual" error with their
    own context).
    """
    return page.evaluate(
        """(title) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (!t || t.innerText.trim() !== title) continue;
                return v.querySelector(
                    '[data-automation-id="visual-overlay-title"]'
                    + '[data-automation-context="No data"]'
                ) !== null;
            }
            return false;
        }""",
        visual_title,
    )


def visual_error_text(page: Page, visual_title: str) -> str | None:
    """AA.A.8 — cheap DOM probe — does this visual show a QS error overlay?

    QuickSight surfaces per-visual rendering failures via an error
    overlay scoped inside the ``[data-automation-id="analysis_visual"]``
    container — e.g. the "your tabular report contains duplicate
    columns. To proceed, remove all duplicates." message that surfaces
    on Pending Aging's Stuck Pending Detail today (the AA.A.8.bug
    case). Returns the overlay's text content (concatenated across
    matched nodes) when an error is present; ``None`` when the visual
    is mounted without errors OR when the visual isn't found.

    Use this in ``DashboardDriver.wait_loaded`` to HARD-FAIL on error
    overlays — pre-AA.A.8 a broken visual would silently time out the
    cell/empty-overlay predicate and the caller had to wait the full
    visual timeout before getting a generic "didn't render" failure
    (with no error text). After AA.A.8: the operator gets the actual
    QS error string in the test failure message, fast.

    Selector union mirrors ``_capture_failure_qs_errors`` (the post-
    failure forensics path) but scopes to the named visual container
    — that's the meaningful axis for "did THIS visual fail to render".
    """
    raw = page.evaluate(
        """(title) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (!t || t.innerText.trim() !== title) continue;
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
                const fragments = [];
                for (const sel of selectors) {
                    v.querySelectorAll(sel).forEach(el => {
                        const txt = (el.innerText || el.textContent || '').trim();
                        if (txt) fragments.push(txt);
                    });
                }
                return fragments.length ? fragments.join(' | ') : null;
            }
            return null;
        }""",
        visual_title,
    )
    return raw if isinstance(raw, str) and raw else None


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
    # Settle on EITHER a populated visual (sn-table-cell-0-0 mounted) OR
    # the QS empty-state overlay ([data-automation-id="visual-overlay-title"]
    # with data-automation-context="No data") — both are positive
    # signals that the visual finished resolving its data state.
    # Pre-AA.H.11.followon: only waited for cells, so empty tables timed
    # out at `timeout_ms` even though QS had rendered the empty overlay
    # within ~200 ms. Adding the overlay branch makes empty-visual
    # detection ~75× faster (200 ms vs 15 s default timeout) and turns
    # a "test dies on timeout" into "caller probes visual_is_empty and
    # short-circuits". The race is safe — a visual either has cells or
    # an empty overlay, never both.
    page.wait_for_function(
        """(title) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (!t || t.innerText.trim() !== title) continue;
                if (v.querySelector('[data-automation-id="sn-table-cell-0-0"]')) return true;
                if (v.querySelector(
                    '[data-automation-id="visual-overlay-title"]'
                    + '[data-automation-context="No data"]'
                )) return true;
                return false;
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


def table_is_paginated(page: Page, visual_title: str) -> bool:
    """Cheap DOM probe — does this visual have a QS pagination control?

    Returns True iff a ``simplePagedDisplayNav_dropdown_pageSize`` element
    exists in the DOM, scoped to the visual whose title matches. Small
    tables (≤ QS default page size, typically ~10–15 rows) render without
    pagination — the DOM holds every row, so ``count_table_rows`` is the
    exact total and no bump is needed.

    This is a pure read — no clicks, no timeouts, no re-render risk.
    Critical for callers that previously triggered a spurious re-fetch
    by clicking the page-size dropdown on small tables that didn't need
    it (the AA.H.11 root cause: the click → re-fetch → ``getMaxRow`` read
    the empty container mid-refetch → returned 0 even though cells were
    about to mount).
    """
    return page.evaluate(
        """(title) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (!t || t.innerText.trim() !== title) continue;
                return v.querySelector(
                    '[data-automation-id="simplePagedDisplayNav_dropdown_pageSize"]'
                ) !== null;
            }
            return false;
        }""",
        visual_title,
    )


def bump_table_page_size_to_10000(
    page: Page, visual_title: str, timeout_ms: int,
) -> bool:
    """Click the visual's page-size dropdown → 10000, no scrolling or
    counting. Returns True if the click sequence completed; False if the
    pagination dropdown never appeared (table isn't actually paginated —
    use ``table_is_paginated`` to pre-check and skip the call entirely).

    **Does not wait for the post-bump re-fetch.** QS fires a fresh data
    query when page size changes; caller must settle that re-fetch
    separately (via ``QsEmbedDriver._settle_after_param_change``, which
    keys off the WebSocket START/STOP frames — *not* a fixed
    ``wait_for_timeout``). The original implementation's
    ``wait_for_timeout(500)`` here was the AA.H.11 race trigger: 500 ms
    wasn't enough for the re-fetch to land on cold visuals, and the
    follow-up scroll-accumulate dance read the empty container.

    The focus-click on the visual title (required to reveal the
    pagination control) is the same JS-dispatched ``.click()`` pattern
    used elsewhere — bypasses Playwright's actionability checks that
    race against QS's re-render churn.
    """
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
    if not clicked:
        return False
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
        return True
    except Exception:
        return False


def count_table_total_rows(page: Page, visual_title: str, timeout_ms: int) -> int:
    """Return the full (post-filter) row count of a QS table visual via
    the scroll-accumulate dance.

    QS tables virtualize — ``count_table_rows`` only sees the ~10 rows
    currently mounted in the DOM. For filter-narrowing assertions where
    both pre and post totals exceed the viewport, DOM counts stay flat
    and the assertion silently passes. This helper:

    1. Scrolls the inner ``.grid-container`` to the bottom, tracking the
       highest ``sn-table-cell-N-*`` index seen.

    Returns:
        ``-1`` if the visual isn't on the page.
        ``-2`` if the visual is present but has no ``.grid-container``
        (e.g. a one-row table that QS renders without a scroll container).
        The post-bump count otherwise (``max + 1``, or ``0`` if no cells).

    **Caller must page-size-bump first** for tables exceeding the QS
    default page size (~10–15 rows) — this helper only scrolls,
    no longer bumps (the bump was extracted to
    ``bump_table_page_size_to_10000``). For small tables (no pagination
    control), ``count_table_rows`` is the exact total — pre-check via
    ``table_is_paginated`` and skip the bump + scroll entirely.

    Pre-AA.H.11 this helper bundled the focus-click + page-size-bump +
    scroll into one call with a fixed ``wait_for_timeout(500)`` after
    the bump. The 500 ms wasn't enough for the post-bump re-fetch to
    land, so the scroll read an empty container and returned 0 —
    causing the audit-agreement test to report ``qs_count=0`` on cold
    sheets where the table actually had 2+ rows (verified via screenshot
    + DOM capture). The fix split the orchestration so callers can:

    1. Cheap-path-skip via ``table_is_paginated`` (no clicks, no risk).
    2. Bump + WS-settle deterministically when overflow is real.
    3. Read via the scroll-accumulate dance below.
    """
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



def read_all_table_rows_via_scroll(
    page: Page, visual_title: str,
) -> list[dict[str, str]]:
    """AA.A.l2ft-rails-inverse.2.c — return EVERY rendered row as a
    list of dicts keyed by column-header display text, walking the
    scroll-accumulate dance so virtualized rows below the fold get
    mounted before they're read.

    Same scroll loop as ``count_table_total_rows`` — scroll the inner
    ``.grid-container`` 400px at a time, settle 120ms per step,
    exit on stable-for-3 or scroll-to-bottom. At each step,
    accumulate ``{row_idx -> {col_idx -> value}}`` keyed by the
    ``sn-table-cell-{r}-{c}`` automation IDs so re-mounted rows
    don't double-count. After scrolling completes, project headers
    (left-to-right ``sn-table-column-N`` titles) and zip them with
    each row's cells (sorted by column index) to produce the per-
    row dict.

    Use case: the inverse-picker test needs to verify the anchor row
    is NOT in the post-pick result. The picker may filter the table
    from 88 → 174 rows (different rail, different rows); reading only
    the DOM-visible window misses rows below the fold and the test
    can't distinguish "anchor excluded" from "anchor below the fold".

    Returns ``[]`` if the visual isn't on the page or has no
    ``.grid-container`` (one-row table).
    """
    return page.evaluate(
        """async (title) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            let target = null;
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (t && t.innerText.trim() === title) { target = v; break; }
            }
            if (!target) return [];
            const container = target.querySelector('.grid-container');
            // Cells map across scroll steps. Keyed by row_idx then col_idx
            // so re-mounted rows just overwrite with the same value.
            const cellsByRow = {};
            const collect = () => {
                target.querySelectorAll('[data-automation-id^="sn-table-cell-"]').forEach(c => {
                    const m = c.getAttribute('data-automation-id').match(/sn-table-cell-(\\d+)-(\\d+)/);
                    if (!m) return;
                    const r = parseInt(m[1], 10), col = parseInt(m[2], 10);
                    (cellsByRow[r] = cellsByRow[r] || {})[col] = c.innerText.trim();
                });
            };
            const getMaxRow = () => {
                let max = -1;
                for (const r of Object.keys(cellsByRow)) {
                    const n = parseInt(r, 10);
                    if (n > max) max = n;
                }
                return max;
            };
            collect();
            if (container) {
                let max = getMaxRow();
                let stable = 0;
                for (let step = 0; step < 500; step++) {
                    const prev = max;
                    container.scrollTop = container.scrollTop + 400;
                    await new Promise(r => setTimeout(r, 120));
                    collect();
                    const now = getMaxRow();
                    if (now > max) max = now;
                    if (container.scrollTop + container.clientHeight >= container.scrollHeight - 1) {
                        await new Promise(r => setTimeout(r, 400));
                        collect();
                        break;
                    }
                    if (now === prev) { stable++; if (stable > 3) break; }
                    else { stable = 0; }
                }
            }
            // Project headers left-to-right; zip with cells per row.
            const headers = [];
            target.querySelectorAll('[data-automation-id^="sn-table-column-"]').forEach(c => {
                if (!/sn-table-column-\\d+$/.test(c.getAttribute('data-automation-id'))) return;
                const titleEl = c.querySelector('.table-title .title')
                    || c.querySelector('.title');
                headers.push(titleEl ? titleEl.innerText.trim() : c.innerText.trim());
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
        }""",
        visual_title,
    ) or []


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


def set_parameter_slider_value(
    page: Page, control_title: str, value: float, timeout_ms: int,
) -> None:
    """Set a single-value ``ParameterSliderControl`` by its title.

    QS renders each ParameterSliderControl as its own ``sheet_control``
    card scoped by ``data-automation-context`` to the control title. The
    card carries an MUI slider (a draggable thumb — fragile to drive
    pixel-accurately in Playwright) AND a typable text box that commits
    the value when it *loses focus* (typing alone doesn't take effect).
    So the reliable path: find the one non-hidden ``<input>`` in the card
    (the MUI slider's own ``<input type="hidden">`` value carrier is the
    other one), fill it, blur it.

    ``value`` is the numeric slider position — an int for the typical
    step-1 control; rendered without a trailing ``.0``.
    """
    card_selector = (
        f'[data-automation-id="sheet_control"]'
        f'[data-automation-context="{control_title}"]'
    )
    page.wait_for_selector(card_selector, timeout=timeout_ms, state="visible")
    loc = page.locator(f'{card_selector} input:not([type="hidden"])').first
    loc.click(timeout=timeout_ms)
    loc.fill(f"{value:g}", timeout=timeout_ms)
    # AA.H.10 — `el.blur()` alone fires the JS blur event but doesn't satisfy
    # MUI's controlled-text-input commit path; the React onChange wiring waits
    # for an Enter key OR a Tab key (which simulates the user moving focus off
    # the field). Without this, the input shows value=4 in the DOM but QS's
    # analysis-param state never updates → no MappedDataSetParameters bridge
    # fire → dashboard re-fetches with the default σ. Verified against the
    # σ-slider DOM dump where the input had value=4 but the KPI stayed at the
    # default-σ count.
    loc.press("Enter", timeout=timeout_ms)
    loc.blur(timeout=timeout_ms)



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

    Each control renders as
    ``[data-automation-id="sheet_control"][data-automation-context="<title>"]``
    but QuickSight uses **two different DOM shapes** for the value
    picker depending on the dropdown's option-count:

    - **Simple variant** (small option universe, e.g. Account Network's
      25-account picker): the trigger is
      ``[data-automation-id="sheet_control_value"]``, the popover lands
      at ``[data-automation-id="sheet_control_value-menu"][data-automation-context="<title>"]``
      with options as ``[role="option"]`` children.
    - **Search-enabled variant** (large option universe, e.g. Money
      Trail's 8080-root-transfer picker): QS swaps to a Material-UI
      Autocomplete with a built-in search input. Trigger is
      ``[data-automation-id="sheet_control_search_results_dropdown"]``;
      ``sheet_control_value`` is **not in the DOM at all**. Popover
      lands at
      ``[data-automation-id="sheet_control_search_results_dropdown-menu"]``
      (suffix ``-menu``, NOT ``sheet_control_menu_dropdown`` — that
      was an earlier wrong guess from AA.H.7), and the popover holds
      a ``MuiAutocomplete`` widget whose ``[role="option"]`` items are
      **not rendered on open**. The widget lazy-mounts the listbox
      only after the search input receives focus + an ArrowDown press
      (or typed input). Per AA.H.8 (DOM dump verified 2026-05-16), we
      detect the search-variant popover after the trigger click and
      focus its input + press ArrowDown to force the listbox to
      render before the option-wait fires.

    Cardinality threshold isn't documented; QS picks the variant
    client-side based on declared values count. Dispatch on selector
    presence: simple trigger first, fall back to search trigger.
    """
    card_selector = (
        f'[data-automation-id="sheet_control"]'
        f'[data-automation-context="{control_title}"]'
    )
    page.wait_for_selector(card_selector, timeout=timeout_ms, state="visible")
    # Dispatch on whichever trigger this control rendered (small option
    # universe vs large + search-enabled). Both render the popover
    # contents lazily on first click; the next wait_for_selector handles
    # whichever container ended up in the DOM.
    simple_trigger = (
        f'{card_selector} [data-automation-id="sheet_control_value"]'
    )
    search_trigger = (
        f'{card_selector} [data-automation-id="sheet_control_search_results_dropdown"]'
    )
    # ``count()`` is a fast DOM-only check (no event loop wait). Pick the
    # variant that's actually rendered. If both somehow match, prefer
    # the simple variant — that's the click target the historical helpers
    # were written against.
    if page.locator(simple_trigger).count() > 0:
        trigger = simple_trigger
    else:
        trigger = search_trigger
    # MUI mounts the listbox in a portal; aria-haspopup="listbox" expands.
    # The first click sometimes no-ops if the sheet just mounted and the
    # combobox's onClick handler hasn't attached — retry until the listbox
    # appears or timeout.
    page.locator(trigger).first.click(timeout=timeout_ms)
    popover_simple = (
        f'[data-automation-id="sheet_control_value-menu"]'
        f'[data-automation-context="{control_title}"]'
    )
    popover_search = (
        f'[data-automation-id="sheet_control_search_results_dropdown-menu"]'
        f'[data-automation-context="{control_title}"]'
    )
    # Search-variant only — MUI Autocomplete inside the popover
    # lazy-mounts its listbox; type or ArrowDown forces it to render.
    # Probe globally (not gated on the popover container being visible
    # by automation-id, because the simple-variant menu container
    # doesn't always carry the ``data-automation-context`` attribute
    # and the gate misfires there; AA.H.8 regression observed for the
    # Account Network anchor). Short, non-fatal: if no search input
    # mounts within 500 ms, this is the simple variant and ArrowDown
    # is unnecessary.
    from playwright.sync_api import TimeoutError as _PWTimeout
    search_input_selector = (
        '[data-automation-id="sheet_control_search_results_dropdown-menu"] input'
    )
    try:
        page.wait_for_selector(
            search_input_selector, timeout=1_000, state="visible",
        )
        page.locator(search_input_selector).first.click(timeout=timeout_ms)
        page.keyboard.press("ArrowDown")
    except _PWTimeout:
        pass  # Simple variant — no search input to focus.
    # Wait for at least one ``[role="option"]`` under either popover
    # shape OR loose in a ``[role="listbox"]`` (some controls put
    # options directly under the popover without listbox role). The
    # global ``[role="listbox"]`` clause is the safety net when the
    # popover container omits ``data-automation-context``.
    #
    # AA.H.10.followon — also accept the MUI Autocomplete's
    # ``.MuiAutocomplete-noOptions`` empty-state element. An empty
    # dropdown (the dataset returned zero rows for the deployed L2)
    # surfaces that element instead of any ``[role="option"]``; without
    # this branch the wait times out with an unhelpful "click target not
    # found" instead of letting ``read_dropdown_options`` return ``[]``
    # so the caller can ``pytest.skip`` cleanly. Caught on the L2FT
    # Bundle dropdown + Money Trail Chain-root-transfer anchor against
    # the ``spec_example`` deploy.
    page.wait_for_selector(
        f'{popover_simple} [role="option"], '
        f'{popover_search} [role="option"], '
        f'[role="listbox"] [role="option"], '
        f'.MuiAutocomplete-noOptions',
        timeout=timeout_ms, state="visible",
    )


def set_dropdown_value(
    page: Page, control_title: str, value: str, timeout_ms: int,
) -> None:
    """Pick a single value from a SINGLE_SELECT FilterControl by title.

    Opens the dropdown for ``control_title`` and clicks the option
    whose text equals ``value``. Use ``clear_dropdown`` to reset to
    "All". Dismisses the popover with Escape so subsequent visual
    interactions aren't blocked by the listbox overlay.

    Handles both ``_open_control_dropdown`` variants transparently:

    - **Simple variant** — listbox is fully rendered on open;
      ``has_text`` finds the option directly.
    - **Search-enabled variant** (MUI Autocomplete) — options are
      virtualized and ``value`` may live outside the rendered window.
      Type ``value`` into the autocomplete search input first so the
      Autocomplete narrows to the matching subset, then click. This
      is the operator's actual flow for an 8000-option dropdown
      (you can't scroll to your target — you type to find it). Per
      AA.H.8 the driver carries this dance so every ``pick_filter``
      / ``set_dropdown_value`` caller works against both variants
      without renderer-specific code in the test.
    """
    _open_control_dropdown(page, control_title, timeout_ms)
    popover_search = (
        f'[data-automation-id="sheet_control_search_results_dropdown-menu"]'
        f'[data-automation-context="{control_title}"]'
    )
    search_input = page.locator(f'{popover_search} input').first
    if search_input.count() > 0:
        # MUI Autocomplete: narrow the listbox via the search input.
        # ``fill`` debounces + repaints the option list; wait for the
        # filtered ``[role="option"]`` to appear before clicking so
        # we don't race a stale option from the pre-filter listbox.
        search_input.fill(value, timeout=timeout_ms)
        page.wait_for_selector(
            f'{popover_search} [role="option"], '
            f'[role="listbox"] [role="option"]',
            timeout=timeout_ms, state="visible",
        )
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
