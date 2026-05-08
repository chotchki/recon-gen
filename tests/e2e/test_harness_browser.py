"""Unit tests for ``tests/e2e/_harness_browser.py`` (M.4.1.g, M.4.1.i).

The retry helper's two contracts:

1. ``AssertionError`` short-circuits — the operation raises real
   failures and the helper MUST NOT swallow them. Real planted-row
   misses are how the harness catches regressions.

2. ``playwright.sync_api.TimeoutError`` triggers retry-with-fresh-URL.
   On each retry the helper calls ``generate_dashboard_embed_url``
   again so the second attempt has a fresh single-use token.

Both behaviors are exercised here against a fake boto3 client (the
helper builds the client internally now — M.4.1.i tightening — so we
monkeypatch boto3.client to return a tracking MagicMock) + a fake
``webkit_page`` substitute. No real Playwright, no real boto3 calls.

Lives at the project test root so it runs in default ``pytest``
(no ``QS_GEN_E2E`` gate needed — pure-data unit tests).
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Add tests/e2e to import path so the test can pull in the helper
# module directly without adding it to the package install.
sys.path.insert(0, str(Path(__file__).parent))


# Skip the entire module if Playwright isn't installed — the helper
# imports ``playwright.sync_api.TimeoutError`` lazily inside its
# function body, so it would fail in test envs without playwright.
playwright = pytest.importorskip(
    "playwright.sync_api",
    reason="harness retry tests need playwright (install via "
    "`pip install -e '.[dev]'`)",
)

from _harness_browser import run_dashboard_check_with_retry  # noqa: E402


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _patch_boto3_client(
    monkeypatch: pytest.MonkeyPatch,
    url_prefix: str = "https://quicksight.aws/embed/",
) -> MagicMock:
    """Monkeypatch ``boto3.client`` so the helper-under-test gets a
    tracking MagicMock instead of a real boto3 QuickSight client.

    Returns the MagicMock so the test can assert against
    ``call_count`` / ``call_args`` on
    ``generate_embed_url_for_registered_user``.

    Each ``generate_embed_url_for_registered_user`` call returns a
    monotonically-incrementing URL so a retry test can prove a second
    URL was actually issued (not a stale one reused).

    Asserts ``region_name`` is passed through — the M.4.1.i fix made
    the helper build the client itself, so the test needs to confirm
    the caller's region reaches the boto3.client call (otherwise the
    "make wrong-region bug unrepresentable" claim doesn't hold).
    """
    fake_client = MagicMock()
    counter = {"n": 0}

    def _gen(**kwargs: Any) -> dict[str, str]:
        counter["n"] += 1
        return {"EmbedUrl": f"{url_prefix}{counter['n']}"}

    fake_client.generate_embed_url_for_registered_user.side_effect = _gen

    captured_regions: list[str] = []

    def _fake_boto3_client(service: str, region_name: str | None = None, **kw: Any):
        assert service == "quicksight", (
            f"helper should only build QS clients, got {service!r}"
        )
        assert region_name is not None, (
            "helper must pass region_name to boto3.client (otherwise "
            "the wrong-region-client bug class is back)"
        )
        captured_regions.append(region_name)
        return fake_client

    monkeypatch.setattr("boto3.client", _fake_boto3_client)
    fake_client._captured_regions = captured_regions  # type: ignore[attr-defined]: monkey-patching test-only attr onto the fake
    return fake_client


class _FakeConsoleMessage:
    """Playwright ConsoleMessage substitute — minimal shape the helper
    consumes (``msg.type`` + ``msg.text``)."""

    def __init__(self, *, type: str, text: str) -> None:
        self.type = type
        self.text = text


class _FakePage:
    """Playwright Page substitute — records ``goto`` calls + supports
    ``wait_for_load_state`` + ``wait_for_selector`` + ``on`` listener
    registration + ``screenshot`` (no-ops by default).

    Tests that need to simulate a per-call timeout patch ``goto`` or
    install a side effect. Tests that simulate console events call
    ``emit_console`` / ``emit_pageerror`` to invoke the registered
    handlers — same shape as Playwright's real event dispatch.
    """

    def __init__(self) -> None:
        self.gotos: list[str] = []
        self.load_state_calls: list[Any] = []
        self.selector_calls: list[Any] = []
        self.screenshots: list[dict[str, Any]] = []
        self._listeners: dict[str, list[Any]] = {}

    def goto(self, url: str, timeout: int) -> None:
        self.gotos.append(url)

    def wait_for_load_state(self, state: str, timeout: int) -> None:
        self.load_state_calls.append((state, timeout))

    def wait_for_selector(self, selector: str, timeout: int, state: str) -> None:
        self.selector_calls.append((selector, timeout, state))

    def on(self, event: str, handler: Any) -> None:
        self._listeners.setdefault(event, []).append(handler)

    def screenshot(self, *, path: str, full_page: bool) -> None:
        self.screenshots.append({"path": path, "full_page": full_page})

    def emit_console(self, *, type: str, text: str) -> None:
        msg = _FakeConsoleMessage(type=type, text=text)
        for handler in self._listeners.get("console", []):
            handler(msg)

    def emit_pageerror(self, exc: Any) -> None:
        for handler in self._listeners.get("pageerror", []):
            handler(exc)


def _patch_webkit_page(monkeypatch: pytest.MonkeyPatch, page: _FakePage) -> None:
    """Replace ``common.browser.helpers.webkit_page`` with a context
    manager that yields the supplied fake page.

    Patch lives on the source module — the helper imports
    ``webkit_page`` lazily inside the function body, so we patch the
    attribute on ``quicksight_gen.common.browser.helpers``.
    """

    @contextmanager
    def _fake(headless: bool = True, viewport: tuple[int, int] = (1600, 1000)):
        yield page

    monkeypatch.setattr(
        "quicksight_gen.common.browser.helpers.webkit_page",
        _fake,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_first_attempt_success_calls_qs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No retry on success — exactly one embed URL generated, exactly
    one operation call, one ``page.goto``."""
    qs = _patch_boto3_client(monkeypatch)
    page = _FakePage()
    _patch_webkit_page(monkeypatch, page)

    op_calls: list[Any] = []

    def operation(p: Any) -> None:
        op_calls.append(p)

    run_dashboard_check_with_retry(
        aws_account_id="111122223333",
        aws_region="us-east-2",
        dashboard_id="dash-123",
        operation=operation,
        page_timeout_ms=30_000,
    )

    assert qs.generate_embed_url_for_registered_user.call_count == 1
    assert len(page.gotos) == 1
    assert len(op_calls) == 1
    # Region threaded through to the boto3.client call (proves the
    # M.4.1.i type-tightening still holds).
    assert qs._captured_regions == ["us-east-2"]


# ---------------------------------------------------------------------------
# AssertionError short-circuits — no retry
# ---------------------------------------------------------------------------


def test_assertion_error_propagates_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real test failure must NOT trigger the QS retry path —
    swallowing AssertionError would mask regressions."""
    qs = _patch_boto3_client(monkeypatch)
    page = _FakePage()
    _patch_webkit_page(monkeypatch, page)

    def operation(p: Any) -> None:
        raise AssertionError("planted row missing on Drift sheet")

    with pytest.raises(AssertionError, match="planted row missing"):
        run_dashboard_check_with_retry(
            aws_account_id="111122223333",
            aws_region="us-east-2",
            dashboard_id="dash-x",
            operation=operation,
            page_timeout_ms=30_000,
        )

    # Only one URL ever generated — no retry happened.
    assert qs.generate_embed_url_for_registered_user.call_count == 1


# ---------------------------------------------------------------------------
# TimeoutError → retry once with fresh URL
# ---------------------------------------------------------------------------


def test_timeout_then_success_retries_once_with_fresh_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First attempt raises Playwright TimeoutError → helper retries.
    Second attempt succeeds. Two URLs generated; second call sees a
    different URL (proves the retry didn't reuse the spent one)."""
    from playwright.sync_api import TimeoutError as PWTimeout

    qs = _patch_boto3_client(monkeypatch)
    page = _FakePage()
    _patch_webkit_page(monkeypatch, page)

    op_attempts = {"n": 0}

    def operation(p: Any) -> None:
        op_attempts["n"] += 1
        if op_attempts["n"] == 1:
            raise PWTimeout("visual didn't render in 30s")
        # Second attempt: succeed.

    run_dashboard_check_with_retry(
        aws_account_id="111122223333",
        aws_region="us-east-2",
        dashboard_id="dash-flake",
        operation=operation,
        page_timeout_ms=30_000,
    )

    # Two URLs generated, two pages opened, two op calls.
    assert qs.generate_embed_url_for_registered_user.call_count == 2
    assert len(page.gotos) == 2
    assert page.gotos[0] != page.gotos[1], (
        "second attempt should hit a fresh embed URL, not the spent one"
    )
    assert op_attempts["n"] == 2


def test_timeout_on_both_attempts_raises_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If every attempt times out, the helper exhausts retries and
    propagates the timeout — caller sees a real failure to triage."""
    from playwright.sync_api import TimeoutError as PWTimeout

    qs = _patch_boto3_client(monkeypatch)
    page = _FakePage()
    _patch_webkit_page(monkeypatch, page)

    def operation(p: Any) -> None:
        raise PWTimeout("permanently broken")

    with pytest.raises(PWTimeout, match="permanently broken"):
        run_dashboard_check_with_retry(
            aws_account_id="111122223333",
            aws_region="us-east-2",
            dashboard_id="dash-broken",
            operation=operation,
            page_timeout_ms=30_000,
        )

    # Default max_attempts = 2 → exactly 2 URL generations.
    assert qs.generate_embed_url_for_registered_user.call_count == 2


def test_max_attempts_param_controls_retry_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``max_attempts=1`` disables the retry — first timeout
    propagates immediately."""
    from playwright.sync_api import TimeoutError as PWTimeout

    qs = _patch_boto3_client(monkeypatch)
    page = _FakePage()
    _patch_webkit_page(monkeypatch, page)

    def operation(p: Any) -> None:
        raise PWTimeout("flaky")

    with pytest.raises(PWTimeout):
        run_dashboard_check_with_retry(
            aws_account_id="111122223333",
            aws_region="us-east-2",
            dashboard_id="dash-x",
            operation=operation,
            page_timeout_ms=30_000,
            max_attempts=1,
        )

    assert qs.generate_embed_url_for_registered_user.call_count == 1


# ---------------------------------------------------------------------------
# URL generation kwargs — wired through correctly
# ---------------------------------------------------------------------------


def test_user_arn_threaded_through_to_qs_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Custom ``user_arn`` reaches the QS embed-URL call — used by
    integrators with non-default user mappings."""
    qs = _patch_boto3_client(monkeypatch)
    page = _FakePage()
    _patch_webkit_page(monkeypatch, page)

    custom_arn = "arn:aws:quicksight:us-east-1:111122223333:user/default/test-user"

    run_dashboard_check_with_retry(
        aws_account_id="111122223333",
        aws_region="us-west-2",
        dashboard_id="dash-u",
        operation=lambda _p: None,
        page_timeout_ms=30_000,
        user_arn=custom_arn,
    )

    call_kwargs = qs.generate_embed_url_for_registered_user.call_args.kwargs
    assert call_kwargs["UserArn"] == custom_arn
    assert call_kwargs["AwsAccountId"] == "111122223333"
    assert call_kwargs["ExperienceConfiguration"]["Dashboard"][
        "InitialDashboardId"
    ] == "dash-u"
    # Region from caller reached boto3.client.
    assert qs._captured_regions == ["us-west-2"]


def test_page_goto_uses_generated_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The URL returned by the QS client is what ``page.goto`` opens —
    no caching / mutation between generation and use."""
    qs = _patch_boto3_client(monkeypatch, url_prefix="https://test.aws/embed/")
    page = _FakePage()
    _patch_webkit_page(monkeypatch, page)

    run_dashboard_check_with_retry(
        aws_account_id="111122223333",
        aws_region="us-east-2",
        dashboard_id="dash-x",
        operation=lambda _p: None,
        page_timeout_ms=30_000,
    )

    assert page.gotos == ["https://test.aws/embed/1"]


# ---------------------------------------------------------------------------
# JS console capture (M.4.4.11)
# ---------------------------------------------------------------------------


def test_console_capture_writes_sidecar_on_assertion_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When a real failure (AssertionError) fires after console messages
    were emitted, the helper writes both the screenshot AND a
    ``<dashboard_id>_attempt<n>_console.txt`` sidecar capturing the
    JS console output. Pairs the failure screenshot with the JS error
    log that drove the M.4.4.10d epochMilliseconds bug discovery."""
    _patch_boto3_client(monkeypatch)
    page = _FakePage()
    _patch_webkit_page(monkeypatch, page)

    def operation(p: _FakePage) -> None:
        # Page emits two real-world-shaped console events before the
        # assertion fires.
        p.emit_console(type="error", text="epochMilliseconds must be a number")
        p.emit_console(type="warning", text="deprecated API in use")
        raise AssertionError("planted row missing on Drift sheet")

    with pytest.raises(AssertionError):
        run_dashboard_check_with_retry(
            aws_account_id="111122223333",
            aws_region="us-east-2",
            dashboard_id="dash-c",
            operation=operation,
            page_timeout_ms=30_000,
            screenshot_dir=tmp_path,
        )

    console_path = tmp_path / "dash-c_attempt1_console.txt"
    assert console_path.exists(), (
        "console sidecar must be written on failure"
    )
    body = console_path.read_text(encoding="utf-8")
    assert "[error] epochMilliseconds must be a number" in body
    assert "[warning] deprecated API in use" in body

    # Screenshot pair lands too — same per-attempt naming.
    shot_path = tmp_path / "dash-c_attempt1.png"
    assert any(
        s["path"] == str(shot_path) for s in page.screenshots
    ), "screenshot must be written on failure (pairs 1:1 with console log)"


def test_console_capture_writes_sidecar_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Timeout failures (the QS spinner-forever case) get the sidecar
    too — both attempts on a permanent timeout each write their own
    pair."""
    from playwright.sync_api import TimeoutError as PWTimeout

    _patch_boto3_client(monkeypatch)
    page = _FakePage()
    _patch_webkit_page(monkeypatch, page)

    def operation(p: _FakePage) -> None:
        p.emit_console(type="error", text="failed to load")
        raise PWTimeout("visual didn't render")

    with pytest.raises(PWTimeout):
        run_dashboard_check_with_retry(
            aws_account_id="111122223333",
            aws_region="us-east-2",
            dashboard_id="dash-t",
            operation=operation,
            page_timeout_ms=30_000,
            screenshot_dir=tmp_path,
        )

    # max_attempts=2 default → both attempts dump their sidecar.
    a1 = tmp_path / "dash-t_attempt1_console.txt"
    a2 = tmp_path / "dash-t_attempt2_console.txt"
    assert a1.exists() and a2.exists()
    assert "[error] failed to load" in a1.read_text(encoding="utf-8")


def test_console_capture_writes_sentinel_when_no_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If the page logged nothing before the failure, the sidecar still
    lands carrying a clear ``<no console output captured>`` sentinel.
    Empty file + present file are both diagnostics; a missing file
    would force the human to wonder whether capture even ran."""
    _patch_boto3_client(monkeypatch)
    page = _FakePage()
    _patch_webkit_page(monkeypatch, page)

    def operation(p: _FakePage) -> None:
        # No console emission at all — straight to assertion.
        raise AssertionError("missing row")

    with pytest.raises(AssertionError):
        run_dashboard_check_with_retry(
            aws_account_id="111122223333",
            aws_region="us-east-2",
            dashboard_id="dash-s",
            operation=operation,
            page_timeout_ms=30_000,
            screenshot_dir=tmp_path,
        )

    console_path = tmp_path / "dash-s_attempt1_console.txt"
    assert console_path.exists()
    body = console_path.read_text(encoding="utf-8")
    assert "<no console output captured>" in body


def test_console_capture_includes_pageerror(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``page.on("pageerror")`` events (uncaught JS exceptions that
    don't go through the console.* API) MUST also reach the sidecar
    — those are exactly the failure mode M.4.4.10d turned out to be
    (the QS UI silently swallowed the error; only pageerror fired)."""
    _patch_boto3_client(monkeypatch)
    page = _FakePage()
    _patch_webkit_page(monkeypatch, page)

    def operation(p: _FakePage) -> None:
        p.emit_pageerror(
            "TypeError: Cannot read property 'foo' of undefined",
        )
        raise AssertionError("data missing")

    with pytest.raises(AssertionError):
        run_dashboard_check_with_retry(
            aws_account_id="111122223333",
            aws_region="us-east-2",
            dashboard_id="dash-pe",
            operation=operation,
            page_timeout_ms=30_000,
            screenshot_dir=tmp_path,
        )

    body = (tmp_path / "dash-pe_attempt1_console.txt").read_text(
        encoding="utf-8",
    )
    assert "[pageerror] TypeError: Cannot read property" in body


def test_no_artifacts_when_screenshot_dir_omitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Without ``screenshot_dir``, neither a screenshot nor a console
    sidecar is written — the helper stays usable in standalone
    debugging mode that doesn't want any disk artifacts."""
    _patch_boto3_client(monkeypatch)
    page = _FakePage()
    _patch_webkit_page(monkeypatch, page)

    def operation(p: _FakePage) -> None:
        p.emit_console(type="error", text="something")
        raise AssertionError("x")

    with pytest.raises(AssertionError):
        run_dashboard_check_with_retry(
            aws_account_id="111122223333",
            aws_region="us-east-2",
            dashboard_id="dash-n",
            operation=operation,
            page_timeout_ms=30_000,
            # screenshot_dir intentionally omitted.
        )

    # tmp_path is unused here on purpose — confirms no incidental writes.
    assert list(tmp_path.iterdir()) == []
    assert page.screenshots == []


def test_no_artifacts_on_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Clean runs leave no console sidecar or screenshot — the helper
    only writes failure artifacts."""
    _patch_boto3_client(monkeypatch)
    page = _FakePage()
    _patch_webkit_page(monkeypatch, page)

    def operation(p: _FakePage) -> None:
        # Some consoles are emitted during success too — must NOT
        # write a sidecar in this case.
        p.emit_console(type="log", text="loaded")

    run_dashboard_check_with_retry(
        aws_account_id="111122223333",
        aws_region="us-east-2",
        dashboard_id="dash-ok",
        operation=operation,
        page_timeout_ms=30_000,
        screenshot_dir=tmp_path,
    )

    assert list(tmp_path.iterdir()) == []
    assert page.screenshots == []
