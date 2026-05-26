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

        @dataclass
        class _Cfg:
            db_table_prefix: str
            dialect: Dialect
            demo_database_url: str

        return _Cfg(
            db_table_prefix=prefix,
            dialect=Dialect.SQLITE,
            demo_database_url=f"sqlite:///{db_path}",
        )

    def test_writes_per_table_counts_for_prefixed_tables(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import sqlite3

        from recon_gen.common.browser.helpers import _capture_failure_db_counts

        db_path = tmp_path / "smoke.db"
        with sqlite3.connect(db_path) as conn:
            cur = conn.cursor()
            cur.execute("CREATE TABLE smoke_transactions (id INTEGER)")
            cur.execute("INSERT INTO smoke_transactions VALUES (1), (2), (3)")
            cur.execute("CREATE TABLE smoke_daily_balances (id INTEGER)")
            # Non-prefixed: must be ignored.
            cur.execute("CREATE TABLE other_table (id INTEGER)")
            cur.execute("INSERT INTO other_table VALUES (99)")
            conn.commit()

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
        import sqlite3

        from recon_gen.common.browser.helpers import _capture_failure_db_counts

        db_path = tmp_path / "empty.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE unrelated_table (id INTEGER)")
            conn.commit()

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

    def test_sidecar_swallows_bad_dialect(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from recon_gen.common.browser.helpers import _capture_failure_db_counts

        # RECON_GEN_RUN_DIR takes priority over SCREENSHOT_DIR (set by
        # the runner per-cell); force the legacy branch.
        monkeypatch.delenv(RECON_GEN_RUN_DIR.name, raising=False)
        monkeypatch.setattr(
            "recon_gen.common.browser.helpers.SCREENSHOT_DIR", tmp_path,
        )

        class _BadCfg:
            db_table_prefix = "smoke"
            dialect = None  # missing → helper writes a "skipped" marker
            demo_database_url = ""

        # Must not raise — sidecar contract.
        _capture_failure_db_counts(_BadCfg(), "test_capture_bad_cfg")

        out = (tmp_path / "_failures" / "test_capture_bad_cfg_db_counts.txt").read_text()
        assert "capture skipped" in out


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
