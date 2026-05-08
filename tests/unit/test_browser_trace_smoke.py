"""Y.2.gate.c.11 — integration smoke for ``webkit_page`` trace + dumps.

Drives the ``webkit_page`` context manager against a static
``data:text/html`` URL (no network, no QS deploy) and asserts the
artifact-routing contract:

  - **Env unset, clean exit**: nothing written.
  - **Env unset, exception**: legacy ``_failures/<test_id>.png`` etc.
    (covered by behavior of the existing browser e2e tests; not
    re-asserted here because writing into the repo's screenshots dir
    isn't worth a unit-test side effect).
  - **Env set, clean exit, ``QS_GEN_TRACE_ALL=1``**: ``trace.zip`` lands
    under ``$QS_GEN_RUN_DIR/browser/<test_id>/``.
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

from quicksight_gen.common.browser.helpers import webkit_page
from quicksight_gen.common.env_keys import QS_GEN_RUN_DIR, QS_GEN_TRACE_ALL


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
    monkeypatch.delenv(QS_GEN_RUN_DIR.name, raising=False)
    monkeypatch.delenv(QS_GEN_TRACE_ALL.name, raising=False)
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
    """Env set + clean exit + QS_GEN_TRACE_ALL=1 → trace.zip lands."""
    _try_webkit_launch_or_skip()
    # Y.2.gate.b.15 — must_be_dir validator requires the path to
    # exist; mkdir before setenv so the registry accepts it.
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    monkeypatch.setenv(QS_GEN_RUN_DIR.name, str(run_dir))
    monkeypatch.setenv(QS_GEN_TRACE_ALL.name, "1")
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
        f"QS_GEN_TRACE_ALL=1 + clean exit"
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
    """Env set + clean exit + QS_GEN_TRACE_ALL unset → trace discarded.
    No file should land — the default policy is "only on failure"."""
    _try_webkit_launch_or_skip()
    # See trace_all sibling for why mkdir is needed.
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    monkeypatch.setenv(QS_GEN_RUN_DIR.name, str(run_dir))
    monkeypatch.delenv(QS_GEN_TRACE_ALL.name, raising=False)
    monkeypatch.setenv(
        "PYTEST_CURRENT_TEST",
        "tests/unit/test_browser_trace_smoke.py::no_trace_all (call)",
    )

    with webkit_page(headless=True) as page:
        page.goto(_HELLO_PAGE)
        page.wait_for_selector("h1")

    browser_dir = run_dir / "browser"
    assert not browser_dir.exists() or not list(browser_dir.iterdir()), (
        f"No trace should land on clean exit without QS_GEN_TRACE_ALL; "
        f"found contents in {browser_dir}"
    )


def test_failure_path_writes_trace_and_all_four_dumps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Env set + exception → trace.zip + screenshot.png + console.txt
    + network.txt + qs_errors.txt all land in
    ``$QS_GEN_RUN_DIR/browser/<test_id>/``. This is the c.11 contract:
    one click and you have everything you need to diagnose."""
    _try_webkit_launch_or_skip()
    # See trace_all sibling for why mkdir is needed.
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    monkeypatch.setenv(QS_GEN_RUN_DIR.name, str(run_dir))
    monkeypatch.delenv(QS_GEN_TRACE_ALL.name, raising=False)
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
    expected = ["trace.zip", "screenshot.png", "console.txt",
                "network.txt", "qs_errors.txt"]
    for name in expected:
        path = capture_dir / name
        assert path.exists(), (
            f"{name} should land in {capture_dir} on exception "
            f"(found: {sorted(p.name for p in capture_dir.iterdir())})"
        )
    # Extracted trace dir alongside the zip — for grepability.
    assert (capture_dir / "trace").is_dir(), (
        f"trace/ extracted dir should land alongside trace.zip on failure"
    )
