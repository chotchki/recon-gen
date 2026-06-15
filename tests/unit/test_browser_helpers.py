"""Unit tests for ``common/browser/helpers.py``.

W.4 — ``get_user_arn`` historically silently fell back to a
hardcoded account-specific ARN when ``RECON_E2E_USER_ARN`` was unset.
That masked CI misconfiguration (Phase W's ``ci-bot`` has a
different ARN — the fallback produced an embed URL the bot
couldn't view) and burned a project account ID into the source.
The contract is now: env var unset = ``RuntimeError`` at the call
site, fail loud.
"""

from __future__ import annotations

import duckdb

import re
from pathlib import Path

import pytest

from recon_gen.common.browser.helpers import (
    SCREENSHOT_DIR,
    _capture_dir_for,
    _capture_path,
    _sanitize_test_id,
    _test_id_from_pytest_env,
    get_user_arn,
)
from recon_gen.common.env_keys import RECON_E2E_USER_ARN, RECON_GEN_RUN_DIR


class TestGetUserArn:
    def test_returns_env_var_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            RECON_E2E_USER_ARN.name,
            "arn:aws:quicksight:us-east-1:111122223333:user/default/test-user",
        )
        assert get_user_arn() == (
            "arn:aws:quicksight:us-east-1:111122223333:user/default/test-user"
        )

    def test_raises_when_env_var_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(RECON_E2E_USER_ARN.name, raising=False)
        with pytest.raises(RuntimeError, match="RECON_E2E_USER_ARN is not set"):
            get_user_arn()

    def test_raises_when_env_var_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # An empty string is treated as unset — same fail-loud path.
        # Otherwise an unset-via-``export RECON_E2E_USER_ARN=`` shell
        # idiom would slip through with an empty UserArn that AWS
        # rejects with a less obvious error.
        monkeypatch.setenv(RECON_E2E_USER_ARN.name, "")
        with pytest.raises(RuntimeError, match="RECON_E2E_USER_ARN is not set"):
            get_user_arn()

    def test_error_message_points_at_e2e_setup_runbook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The runbook reference is the documented path for fixing
        # this in CI; if the doc moves, this test fails loud and
        # reminds the editor to update the message.
        monkeypatch.delenv(RECON_E2E_USER_ARN.name, raising=False)
        with pytest.raises(RuntimeError) as exc_info:
            get_user_arn()
        assert ".github/E2E_SETUP.md" in str(exc_info.value)


class TestTestIdFromPytestEnv:
    """X.1.a — auto-failure-screenshot hook derives a filename-safe
    test ID from ``PYTEST_CURRENT_TEST`` so each failing test gets a
    distinct screenshot in ``_failures/<test_id>.png``."""

    def test_strips_phase_suffix(self):
        assert _test_id_from_pytest_env(
            "tests/e2e/test_foo.py::test_bar (call)"
        ) == "tests_e2e_test_foo__test_bar"

    def test_handles_setup_and_teardown_phases(self):
        # Failures during fixture setup / teardown also produce sensible
        # filenames — same test_id regardless of phase, so the latest
        # snapshot wins (acceptable; setup/teardown failures are rare
        # and call-phase is the common case anyway).
        assert _test_id_from_pytest_env(
            "tests/e2e/test_foo.py::test_bar (setup)"
        ) == "tests_e2e_test_foo__test_bar"

    def test_handles_parametrized_test(self):
        # Parametrization brackets ``[case_x]`` stay in the filename —
        # they're filename-safe on every target FS we care about
        # (macOS APFS, ext4, NTFS, GHA artifact zip) and disambiguate
        # different parameter sets that fail in the same run.
        assert _test_id_from_pytest_env(
            "tests/e2e/test_foo.py::test_bar[case_x] (call)"
        ) == "tests_e2e_test_foo__test_bar[case_x]"

    def test_sanitizes_parametrize_id_with_spaces_and_emdash(self):
        # The real-world failure that bit us: an [qs, app2]-parametrized
        # test whose parametrize ID interpolates sheet titles and visual
        # names that contain spaces and em-dashes. The filename can land
        # OK on macOS APFS, but downstream consumers (GHA artifact zip,
        # Windows, shell-glob patterns, ``zipfile`` round-trips) break
        # on the special chars. Sanitize them to ``_`` here so the
        # captured artifact name is portable everywhere.
        raw = (
            "tests/e2e/test_parameter_anchored_sheets.py::"
            "test_inv_anchor_control_present_and_populated"
            "[qs-Money Trail-Chain root transfer-Money Trail — Hop-by-Hop] (call)"
        )
        out = _test_id_from_pytest_env(raw)
        # Every char in the result is in the portable charset
        # ``[A-Za-z0-9_\-\[\].]``.
        assert re.fullmatch(r"[A-Za-z0-9_\-\[\].]+", out), (
            f"sanitized id leaked non-portable chars: {out!r}"
        )
        # Brackets stay (disambiguates parametrize IDs).
        assert "[" in out and "]" in out
        # Spaces / em-dash / parens are gone.
        assert " " not in out and "—" not in out
        assert "(" not in out and ")" not in out

    def test_handles_class_based_test(self):
        assert _test_id_from_pytest_env(
            "tests/e2e/test_foo.py::TestFoo::test_bar (call)"
        ) == "tests_e2e_test_foo__TestFoo__test_bar"

    def test_returns_unknown_when_env_var_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        assert _test_id_from_pytest_env() == "unknown"

    def test_returns_unknown_when_env_var_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "")
        assert _test_id_from_pytest_env() == "unknown"

    def test_reads_env_var_when_no_arg_supplied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "PYTEST_CURRENT_TEST",
            "tests/foo.py::bar (call)",
        )
        assert _test_id_from_pytest_env() == "tests_foo__bar"


class TestCaptureDirAndPath:
    """Y.2.gate.c.11 — failure dumps + Playwright trace.zip route to
    ``$RECON_GEN_RUN_DIR/browser/<test_id>/`` when running under the
    test layer chain runner; fall back to the legacy
    ``<SCREENSHOT_DIR>/_failures/`` flat dir otherwise."""

    def test_capture_dir_runner_mode(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # Y.2.gate.b.15 — registry's must_be_dir validator requires the
        # path to exist; mkdir before setting the env so the test
        # exercises the runner-mode path (not the soft-fall legacy
        # branch that triggers on validator failure).
        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True)
        monkeypatch.setenv(RECON_GEN_RUN_DIR.name, str(run_dir))
        out = _capture_dir_for("tests_e2e_test_foo__bar")
        assert out == run_dir / "browser" / "tests_e2e_test_foo__bar"

    def test_capture_dir_legacy_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(RECON_GEN_RUN_DIR.name, raising=False)
        out = _capture_dir_for("any_test_id")
        assert out == SCREENSHOT_DIR / "_failures"

    def test_capture_path_runner_mode_uses_short_filenames(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """Per-test directory means we don't need the test_id prefix
        on every file — names like ``screenshot.png`` are already
        scoped by their parent dir."""
        # See test_capture_dir_runner_mode for why mkdir is needed.
        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True)
        monkeypatch.setenv(RECON_GEN_RUN_DIR.name, str(run_dir))
        test_id = "tests_e2e_test_foo__bar"
        assert _capture_path("screenshot.png", test_id) == (
            run_dir / "browser" / test_id / "screenshot.png"
        )
        assert _capture_path("console.txt", test_id) == (
            run_dir / "browser" / test_id / "console.txt"
        )
        assert _capture_path("trace.zip", test_id) == (
            run_dir / "browser" / test_id / "trace.zip"
        )

    def test_capture_path_legacy_mode_keeps_test_id_prefix(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Flat-dir legacy mode needs the test_id prefix so files
        from concurrent test runs don't collide. Special-case:
        ``screenshot.png`` lands at ``<test_id>.png`` (no underscore
        prefix) per the M.4.4.11-era convention."""
        monkeypatch.delenv(RECON_GEN_RUN_DIR.name, raising=False)
        test_id = "tests_e2e_test_foo__bar"
        assert _capture_path("screenshot.png", test_id) == (
            SCREENSHOT_DIR / "_failures" / f"{test_id}.png"
        )
        assert _capture_path("console.txt", test_id) == (
            SCREENSHOT_DIR / "_failures" / f"{test_id}_console.txt"
        )
        assert _capture_path("network.txt", test_id) == (
            SCREENSHOT_DIR / "_failures" / f"{test_id}_network.txt"
        )
        assert _capture_path("qs_errors.txt", test_id) == (
            SCREENSHOT_DIR / "_failures" / f"{test_id}_qs_errors.txt"
        )


class TestSanitizeTestId:
    """``_sanitize_test_id`` is the gate that keeps the test_id portable
    after pytest hands us a parametrized ID with arbitrary user-content
    interpolations (sheet titles, visual names, error messages — any of
    which can carry spaces / em-dashes / colons / brackets nested inside
    brackets / etc.). Each non-portable char in the input collapses to a
    single ``_``; a run of N non-portable chars also collapses to one
    ``_`` (no double-underscore explosions)."""

    def test_keeps_alphanumerics_underscores_hyphens_brackets_dots(self):
        # All portable chars survive untouched.
        assert _sanitize_test_id("test_foo-bar[qs-Rail].py") == "test_foo-bar[qs-Rail].py"

    def test_collapses_space_to_underscore(self):
        assert _sanitize_test_id("foo bar") == "foo_bar"

    def test_collapses_emdash_to_underscore(self):
        assert _sanitize_test_id("foo — bar") == "foo_bar"

    def test_collapses_run_of_special_chars_to_single_underscore(self):
        # 3 specials in a row → 1 underscore, not 3. Keeps filenames short
        # and predictable.
        assert _sanitize_test_id("foo   bar") == "foo_bar"
        assert _sanitize_test_id("foo — — bar") == "foo_bar"

    def test_strips_parens_colons_quotes(self):
        assert _sanitize_test_id("foo(bar)") == "foo_bar_"
        assert _sanitize_test_id("foo:bar") == "foo_bar"
        assert _sanitize_test_id("foo'bar\"") == "foo_bar_"


class TestCaptureFailureDbCounts:
    """v11.0.0a4 — db_counts.txt artifact answers "is the data even
    there?" for blank-visual triage. Sidecar contract: never raise."""

    def _make_cfg(self, db_path: Path, prefix: str) -> object:
        """Build a tiny duck-typed cfg sufficient for the helper's
        attribute reads + connect_demo_db(SQLITE) path. Real Config
        carries other fields the helper doesn't touch.
        """
        from dataclasses import dataclass

        from recon_gen.common.sql.dialect import Dialect

        # DE.2 — _capture_failure_db_counts reads cfg.db.{table_prefix,
        # dialect, url}; mirror the v14 nested shape inline.
        @dataclass
        class _DbCfg:
            table_prefix: str
            dialect: Dialect
            url: str

        @dataclass
        class _Cfg:
            db: _DbCfg

        from recon_gen.common.db import make_demo_database_url

        return _Cfg(
            db=_DbCfg(
                table_prefix=prefix,
                dialect=Dialect.DUCKDB,
                url=make_demo_database_url(Dialect.DUCKDB, db_path),
            ),
        )

    def test_writes_per_table_counts_for_prefixed_tables(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:

        from recon_gen.common.browser.helpers import _capture_failure_db_counts

        db_path = tmp_path / "smoke.db"
        # `with duckdb.connect(...) as c` only commits on exit — it
        # does NOT close the connection (Python stdlib foot-gun).
        # Explicit try/finally so the conn actually closes (otherwise
        # the leak gate fires + each test's leftover Connection
        # accumulates memory across the suite).
        conn = duckdb.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("CREATE TABLE smoke_transactions (id INTEGER)")
            cur.execute("INSERT INTO smoke_transactions VALUES (1), (2), (3)")
            cur.execute("CREATE TABLE smoke_daily_balances (id INTEGER)")
            # Non-prefixed: must be ignored.
            cur.execute("CREATE TABLE other_table (id INTEGER)")
            cur.execute("INSERT INTO other_table VALUES (99)")
            conn.commit()
        finally:
            conn.close()

        # Route capture output to tmp_path via the legacy SCREENSHOT_DIR
        # path. RECON_GEN_RUN_DIR must be unset (it takes priority over
        # SCREENSHOT_DIR in `_capture_path`); under the runner the env
        # var is set per-cell, so explicit delenv here forces the
        # legacy branch the assertions key off.
        monkeypatch.delenv(RECON_GEN_RUN_DIR.name, raising=False)
        monkeypatch.setattr(
            "recon_gen.common.browser.helpers.SCREENSHOT_DIR", tmp_path,
        )
        cfg = self._make_cfg(db_path, prefix="smoke")
        _capture_failure_db_counts(cfg, "test_capture_one")

        out = (tmp_path / "_failures" / "test_capture_one_db_counts.txt").read_text()
        lines = out.strip().split("\n")
        assert lines == [
            "smoke_daily_balances: 0",
            "smoke_transactions: 3",
        ], f"unexpected output:\n{out}"
        # other_table is non-prefixed so it must NOT appear.
        assert "other_table" not in out

    def test_empty_file_when_no_prefixed_tables(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:

        from recon_gen.common.browser.helpers import _capture_failure_db_counts

        db_path = tmp_path / "empty.db"
        # See sibling test: `with duckdb.connect(...) as c` commits but
        # doesn't close. Explicit try/finally for the actual close.
        conn = duckdb.connect(db_path)
        try:
            conn.execute("CREATE TABLE unrelated_table (id INTEGER)")
            conn.commit()
        finally:
            conn.close()

        # RECON_GEN_RUN_DIR takes priority over SCREENSHOT_DIR (set by
        # the runner per-cell); force the legacy branch.
        monkeypatch.delenv(RECON_GEN_RUN_DIR.name, raising=False)
        monkeypatch.setattr(
            "recon_gen.common.browser.helpers.SCREENSHOT_DIR", tmp_path,
        )
        cfg = self._make_cfg(db_path, prefix="absent")
        _capture_failure_db_counts(cfg, "test_capture_empty")

        out = (tmp_path / "_failures" / "test_capture_empty_db_counts.txt").read_text()
        # Empty file IS the signal — schema was never applied / prefix is wrong.
        assert out == ""

    def test_sidecar_swallows_bad_cfg_without_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sidecar contract: a malformed cfg passed to the capture helper
        must be swallowed via stderr warning, not raised. v14 Config has
        typed required fields so the prior "missing db.table_prefix /
        db.dialect" sentinel file path is unreachable; instead exercise
        the outer try/except by handing the helper a bare object."""
        from recon_gen.common.browser.helpers import _capture_failure_db_counts

        monkeypatch.delenv(RECON_GEN_RUN_DIR.name, raising=False)
        monkeypatch.setattr(
            "recon_gen.common.browser.helpers.SCREENSHOT_DIR", tmp_path,
        )

        # Must not raise — outer try/except wraps everything in the helper.
        _capture_failure_db_counts(object(), "test_capture_bad_cfg")
        # No file should be written (the exception fired before the
        # dialect-dispatch write paths).
        assert not (
            tmp_path / "_failures" / "test_capture_bad_cfg_db_counts.txt"
        ).exists()


class _FakePage:
    """Minimal Playwright Page stand-in for unit-testing the Python
    side of ``assert_no_literal_html_entities``. The real JS body runs
    via Playwright in browser-tier tests; here we just verify the
    Python raise / return contract given canned findings."""

    def __init__(self, findings: list[dict[str, object]]) -> None:
        self._findings = findings
        self.calls: list[tuple[str, object]] = []

    def evaluate(self, js: str, arg: object = None) -> list[dict[str, object]]:
        self.calls.append((js, arg))
        return self._findings


class TestAssertNoLiteralHtmlEntities:
    """DK.11 — driver-level scanner for visible double-escaped HTML in
    rendered DOM text. Catches double-escape bugs the moment any
    browser-tier test navigates / waits."""

    def test_returns_silently_on_empty_findings(self) -> None:
        from recon_gen.common.browser.helpers import (
            assert_no_literal_html_entities,
        )
        page = _FakePage(findings=[])
        # Should not raise.
        assert_no_literal_html_entities(
            page,  # pyright: ignore[reportArgumentType]: structural duck-type matches Page surface (evaluate only)
            context="open",
        )
        assert len(page.calls) == 1
        # The JS body must include the entity regex + the skip-tag list
        # so the lock catches a refactor that loses one of them.
        js, _ = page.calls[0]
        assert "ENTITY_RE" in js
        assert "amp|lt|gt|quot|apos" in js
        assert "CODE" in js and "PRE" in js and "TEXTAREA" in js

    def test_raises_with_actionable_message_on_findings(self) -> None:
        from recon_gen.common.browser.helpers import (
            assert_no_literal_html_entities,
        )
        page = _FakePage(findings=[
            {"tag": "H2", "text": "Bob&#x27;s Bank — Daily Statement",
             "entities": ["&#x27;"]},
            {"tag": "P", "text": "Cash &amp; Due From Federal Reserve",
             "entities": ["&amp;"]},
        ])
        with pytest.raises(RuntimeError) as excinfo:
            assert_no_literal_html_entities(
                page,  # pyright: ignore[reportArgumentType]: same duck-type rationale
                context="wait_loaded('Daily Statement')",
            )
        msg = str(excinfo.value)
        # The error names BOTH the verb context (so the operator knows
        # which call triggered the scan) AND the first finding's text +
        # entity (so they can grep the source).
        assert "wait_loaded('Daily Statement')" in msg
        assert "Bob&#x27;s Bank" in msg
        assert "&#x27;" in msg
        # Surfaces the double-escape root cause in plain English so a
        # cold-read operator doesn't need to know what the test does.
        assert "double-escaped" in msg or "double-escape" in msg

    def test_root_selector_threads_through_to_js_arg(self) -> None:
        from recon_gen.common.browser.helpers import (
            VISUAL_SELECTOR,
            assert_no_literal_html_entities,
        )
        page = _FakePage(findings=[])
        assert_no_literal_html_entities(
            page,  # pyright: ignore[reportArgumentType]: same duck-type rationale
            context="x",
            root_selector=VISUAL_SELECTOR,
        )
        _, arg = page.calls[0]
        assert isinstance(arg, dict)
        assert arg["rootSelector"] == VISUAL_SELECTOR


class TestNoHardcodedArnInSource:
    """W.4 hygiene: the helpers module must not retain a hardcoded
    AWS account ID. The previous silent fallback baked a real account
    ID into source — this test guards against regression."""

    def test_no_aws_account_id_literal_in_helpers_module(self) -> None:
        from recon_gen.common.browser import helpers as helpers_mod
        from pathlib import Path

        source = Path(helpers_mod.__file__).read_text()
        # Any 12-digit run that looks like an AWS account ID inside
        # an ARN string. Tightened to ``arn:`` context so we don't
        # false-positive on, e.g., timeouts or unrelated digit runs.
        matches = re.findall(r"arn:aws:[^\s\"]+:\d{12}:", source)
        assert not matches, (
            f"helpers.py contains hardcoded ARN(s) with embedded "
            f"AWS account IDs: {matches}. Read the user ARN from "
            f"``RECON_E2E_USER_ARN`` instead."
        )
