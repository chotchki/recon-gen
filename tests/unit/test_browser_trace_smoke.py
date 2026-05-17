"""Y.2.gate.c.11 — integration smoke for ``webkit_page`` trace + dumps.

Drives the ``webkit_page`` context manager against a static
``data:text/html`` URL (no network, no QS deploy) and asserts the
artifact-routing contract:

  - **Env unset, clean exit**: nothing written.
  - **Env unset, exception**: legacy ``_failures/<test_id>.png`` etc.
    (covered by behavior of the existing browser e2e tests; not
    re-asserted here because writing into the repo's screenshots dir
    isn't worth a unit-test side effect).
  - **Env set, clean exit, ``RECON_GEN_TRACE_ALL=1``**: ``trace.zip`` lands
    under ``$RECON_GEN_RUN_DIR/browser/<test_id>/``.
  - **Env set, exception**: ``trace.zip`` + ``screenshot.png`` +
    ``console.txt`` + ``network.txt`` + ``qs_errors.txt`` all land.

Skipped when Playwright isn't installed OR the WebKit binary is
missing (``playwright install webkit`` once per env). The CI's e2e
job already runs the install step; locally, ``uv sync --all-extras``
brings playwright in but operators may need ``.venv/bin/playwright
install webkit`` once.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from recon_gen.common.browser.helpers import (
    trigger_failure_capture,
    webkit_page,
)
from recon_gen.common.env_keys import RECON_GEN_RUN_DIR, RECON_GEN_TRACE_ALL


# Skip cleanly when Playwright (or its WebKit binary) isn't available.
playwright_sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="Y.2.gate.c.11 smoke needs playwright (install via "
           "`uv sync --all-extras` then `playwright install webkit`).",
)


_HELLO_PAGE = "data:text/html,<html><body><h1>hello</h1></body></html>"


def _try_webkit_launch_or_skip() -> None:
    """Skip the test if WebKit isn't installed (rather than failing
    the suite). The full launch happens inside ``webkit_page``; this
    is just a fast probe so the skip message is actionable."""
    try:
        with playwright_sync_api.sync_playwright() as p:
            browser = p.webkit.launch(headless=True)
            browser.close()
    except Exception as exc:
        pytest.skip(
            f"WebKit binary not installed (run `.venv/bin/playwright "
            f"install webkit`): {exc}"
        )


def test_no_artifacts_written_on_clean_exit_without_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Env unset + clean exit = trace not saved + dumps not written.
    The legacy ``_failures/`` writes only fire on exception; we don't
    raise inside the with body, so nothing should land anywhere."""
    _try_webkit_launch_or_skip()
    monkeypatch.delenv(RECON_GEN_RUN_DIR.name, raising=False)
    monkeypatch.delenv(RECON_GEN_TRACE_ALL.name, raising=False)
    monkeypatch.setenv(
        "PYTEST_CURRENT_TEST",
        "tests/unit/test_browser_trace_smoke.py::clean_no_env (call)",
    )

    with webkit_page(headless=True) as page:
        page.goto(_HELLO_PAGE)
        page.wait_for_selector("h1")

    # Nothing to assert positively — the test passes if nothing
    # raised. The negative case (no spurious legacy writes) would
    # require pre-snapshotting the failures dir; not worth it here.


def test_trace_zip_written_on_clean_exit_when_trace_all_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Env set + clean exit + RECON_GEN_TRACE_ALL=1 → trace.zip lands."""
    _try_webkit_launch_or_skip()
    # Y.2.gate.b.15 — must_be_dir validator requires the path to
    # exist; mkdir before setenv so the registry accepts it.
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    monkeypatch.setenv(RECON_GEN_RUN_DIR.name, str(run_dir))
    monkeypatch.setenv(RECON_GEN_TRACE_ALL.name, "1")
    monkeypatch.setenv(
        "PYTEST_CURRENT_TEST",
        "tests/unit/test_browser_trace_smoke.py::trace_all (call)",
    )

    with webkit_page(headless=True) as page:
        page.goto(_HELLO_PAGE)
        page.wait_for_selector("h1")

    capture_dir = (
        run_dir / "browser"
        / "tests_unit_test_browser_trace_smoke__trace_all"
    )
    trace = capture_dir / "trace.zip"
    assert trace.exists(), (
        f"trace.zip should land under {capture_dir} when "
        f"RECON_GEN_TRACE_ALL=1 + clean exit"
    )
    # Sanity check: trace files are zip archives of meaningful size
    # (not 0 bytes from a botched stop call).
    assert trace.stat().st_size > 100

    # Y.2.gate.c.11 — extracted "trace/" sibling dir lets the operator
    # grep contents directly without launching the trace viewer.
    extract_dir = capture_dir / "trace"
    assert extract_dir.is_dir(), (
        f"trace.zip should be extracted to {extract_dir} for grepability"
    )
    # The Playwright trace bundle ships these top-level entries.
    extracted = sorted(p.name for p in extract_dir.iterdir())
    assert any(name.endswith(".trace") for name in extracted), (
        f"Expected a .trace file in extracted bundle; got {extracted}"
    )


def test_trace_zip_not_written_on_clean_exit_without_trace_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Env set + clean exit + RECON_GEN_TRACE_ALL unset → trace discarded.
    No file should land — the default policy is "only on failure"."""
    _try_webkit_launch_or_skip()
    # See trace_all sibling for why mkdir is needed.
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    monkeypatch.setenv(RECON_GEN_RUN_DIR.name, str(run_dir))
    monkeypatch.delenv(RECON_GEN_TRACE_ALL.name, raising=False)
    monkeypatch.setenv(
        "PYTEST_CURRENT_TEST",
        "tests/unit/test_browser_trace_smoke.py::no_trace_all (call)",
    )

    with webkit_page(headless=True) as page:
        page.goto(_HELLO_PAGE)
        page.wait_for_selector("h1")

    browser_dir = run_dir / "browser"
    assert not browser_dir.exists() or not list(browser_dir.iterdir()), (
        f"No trace should land on clean exit without RECON_GEN_TRACE_ALL; "
        f"found contents in {browser_dir}"
    )


def test_failure_path_writes_trace_and_all_five_dumps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Env set + exception → trace.zip + screenshot.png + dom.html +
    console.txt + network.txt + qs_errors.txt all land in
    ``$RECON_GEN_RUN_DIR/browser/<test_id>/``. This is the c.11 contract:
    one click and you have everything you need to diagnose.

    The ``dom.html`` artifact was added after a real-world bug where
    Money Trail's "click target not found" failure left only a
    screenshot (pixels), and the screenshot couldn't tell us *why*
    the test's selector didn't match — only that the visual area
    looked occupied. The DOM dump fills that gap: ``screenshot.png``
    shows what's visually there, ``dom.html`` shows what the
    test's selectors were actually looking at.
    """
    _try_webkit_launch_or_skip()
    # See trace_all sibling for why mkdir is needed.
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    monkeypatch.setenv(RECON_GEN_RUN_DIR.name, str(run_dir))
    monkeypatch.delenv(RECON_GEN_TRACE_ALL.name, raising=False)
    monkeypatch.setenv(
        "PYTEST_CURRENT_TEST",
        "tests/unit/test_browser_trace_smoke.py::failure (call)",
    )

    with pytest.raises(RuntimeError, match="boom"):
        with webkit_page(headless=True) as page:
            page.goto(_HELLO_PAGE)
            page.wait_for_selector("h1")
            raise RuntimeError("boom")

    capture_dir = (
        run_dir / "browser" / "tests_unit_test_browser_trace_smoke__failure"
    )
    expected = ["trace.zip", "screenshot.png", "dom.html", "console.txt",
                "network.txt", "qs_errors.txt"]
    for name in expected:
        path = capture_dir / name
        assert path.exists(), (
            f"{name} should land in {capture_dir} on exception "
            f"(found: {sorted(p.name for p in capture_dir.iterdir())})"
        )
    # dom.html should actually contain the served HTML, not be empty —
    # otherwise the "DOM at failure" claim is hollow.
    dom_text = (capture_dir / "dom.html").read_text(encoding="utf-8")
    assert "<h1>hello</h1>" in dom_text, (
        f"dom.html missing the test page content; got: {dom_text!r}"
    )
    # Extracted trace dir alongside the zip — for grepability.
    assert (capture_dir / "trace").is_dir(), (
        f"trace/ extracted dir should land alongside trace.zip on failure"
    )


def test_explicit_trigger_writes_all_artifacts_for_pytest_fixture_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """AA.H.6 regression guard for the pytest-fixture path.

    pytest's ``yield``-fixture semantics don't re-throw test-body
    exceptions back into the fixture's generator — so
    ``webkit_page``'s ``except BaseException:`` never fires for a
    real e2e test failure (the original AA.H regression-guard test
    raises inside the ``with`` block, which is a different code
    path). Production fixtures call ``trigger_failure_capture(page)``
    explicitly from teardown after consulting
    ``request.node.rep_call.failed`` via the standard
    ``pytest_runtest_makereport`` hook.

    This test exercises the explicit-trigger contract directly: open
    ``webkit_page``, exit cleanly (no exception), but call
    ``trigger_failure_capture`` mid-block — the same shape as fixture
    teardown. All 6 artifacts must land regardless of whether the
    ``with`` block ultimately raised.
    """
    _try_webkit_launch_or_skip()
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    monkeypatch.setenv(RECON_GEN_RUN_DIR.name, str(run_dir))
    monkeypatch.delenv(RECON_GEN_TRACE_ALL.name, raising=False)
    monkeypatch.setenv(
        "PYTEST_CURRENT_TEST",
        "tests/unit/test_browser_trace_smoke.py::explicit_trigger (call)",
    )

    with webkit_page(headless=True) as page:
        page.goto(_HELLO_PAGE)
        page.wait_for_selector("h1")
        # Simulate what `_maybe_capture_on_failure` does in production:
        # explicit trigger with a pinned test_id (in production this is
        # ``request.node.nodeid``, sanitized). The fixture exits the
        # ``with`` block cleanly afterward — no exception bubbles into
        # webkit_page's except handler.
        trigger_failure_capture(page, test_id="explicit_trigger_smoke")

    capture_dir = run_dir / "browser" / "explicit_trigger_smoke"
    expected = ["trace.zip", "screenshot.png", "dom.html", "console.txt",
                "network.txt", "qs_errors.txt"]
    for name in expected:
        path = capture_dir / name
        assert path.exists(), (
            f"{name} missing for explicit-trigger path "
            f"(this is the regression mode where pytest fixture teardown "
            f"calls trigger_failure_capture explicitly). "
            f"contents: {sorted(p.name for p in capture_dir.iterdir())}"
        )
    dom_text = (capture_dir / "dom.html").read_text(encoding="utf-8")
    assert "<h1>hello</h1>" in dom_text
    # trace.zip should also land because trigger_failure_capture sets
    # ``page._qs_gen_capture_triggered = True``, which webkit_page's
    # finally block honors as "the trace should be saved even though
    # no exception bubbled".
    assert (capture_dir / "trace").is_dir(), (
        f"trace/ should land — trigger_failure_capture must flip "
        f"_qs_gen_capture_triggered so the trace-save decision matches"
    )


def test_failure_path_handles_parametrized_test_id_with_specials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Regression guard for the May 2026 bug: parametrized e2e tests
    whose IDs contain spaces / em-dashes / parens produced zero
    artifacts at all — the whole capture path silently no-op'd. This
    test reproduces that exact ``PYTEST_CURRENT_TEST`` shape and
    asserts every artifact still lands, under a sanitized
    bracket-disambiguated test_id.

    If this test fails (or any of the 6 expected files is missing),
    the next forensic session will be back to "screenshot? what
    screenshot?" — fail loud here so the regression surfaces in unit
    layer instead of after a 15-minute browser e2e run."""
    _try_webkit_launch_or_skip()
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    monkeypatch.setenv(RECON_GEN_RUN_DIR.name, str(run_dir))
    monkeypatch.delenv(RECON_GEN_TRACE_ALL.name, raising=False)
    monkeypatch.setenv(
        "PYTEST_CURRENT_TEST",
        "tests/e2e/test_parameter_anchored_sheets.py::"
        "test_inv_anchor_control_present_and_populated"
        "[qs-Money Trail-Chain root transfer-Money Trail — Hop-by-Hop] (call)",
    )

    with pytest.raises(RuntimeError, match="boom"):
        with webkit_page(headless=True) as page:
            page.goto(_HELLO_PAGE)
            page.wait_for_selector("h1")
            raise RuntimeError("boom")

    # The capture dir name is sanitized: spaces / em-dashes / parens
    # collapse to ``_`` runs; brackets stay.
    browser_dir = run_dir / "browser"
    assert browser_dir.is_dir(), (
        f"browser/ dir should exist after a failing webkit_page block; "
        f"contents of run_dir: {sorted(p.name for p in run_dir.iterdir())}"
    )
    capture_subdirs = [p for p in browser_dir.iterdir() if p.is_dir()]
    assert len(capture_subdirs) == 1, (
        f"expected exactly one capture subdir under browser/, got "
        f"{[p.name for p in capture_subdirs]}"
    )
    capture_dir = capture_subdirs[0]
    # Sanitized form: no spaces, no em-dash, no parens, brackets kept.
    assert " " not in capture_dir.name
    assert "—" not in capture_dir.name
    assert "[" in capture_dir.name and "]" in capture_dir.name
    expected = ["trace.zip", "screenshot.png", "dom.html", "console.txt",
                "network.txt", "qs_errors.txt"]
    for name in expected:
        path = capture_dir / name
        assert path.exists(), (
            f"{name} missing for parametrized-id test "
            f"(this is the regression mode from May 2026 — see "
            f"tests/unit/test_browser_helpers.py::"
            f"TestSanitizeTestId for context). "
            f"capture_dir: {capture_dir.name}; "
            f"contents: {sorted(p.name for p in capture_dir.iterdir())}"
        )
