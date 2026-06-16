"""DK.7.e2e — integration tests for ``maybe_export_data_anchor``.

The CLI-side wire shape that ``recon-gen json apply`` (DK.4) and
``recon-gen audit apply`` (DK.6) both call at entry. Verifies the
operator-observable behavior end-to-end across the four code paths:

1. cfg.test.generator.end_date pinned → env untouched (operator override).
2. RECON_GEN_AS_OF_ANCHOR env pinned → env untouched (chain-determinism
   override; data-derived path doesn't fire).
3. Cold DB / empty data_anchor matview → loud warning, env untouched.
4. Connection failure → loud warning, env untouched.
5. Happy path → env exported to the matview value, source marker set
   to "data_anchor".

The real full-stack e2e (deploy → dashboards render with the anchor)
rides on the chain's qs_browser layer per POLICY 1 — these tests pin
the in-process resolution logic against drift so the chain doesn't
catch a regression that's already discoverable here.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date as _date
from pathlib import Path

import duckdb
import pytest

from recon_gen.cli._helpers import maybe_export_data_anchor
from recon_gen.common.db import execute_script, make_demo_database_url
from recon_gen.common.env_keys import (
    RECON_GEN_AS_OF_ANCHOR,
    RECON_GEN_AS_OF_ANCHOR_SOURCE,
)
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.sql import Dialect

from tests._test_helpers import make_test_config

_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)


@pytest.fixture(autouse=True)
def _clean_anchor_env(monkeypatch: pytest.MonkeyPatch) -> "Iterator[None]":  # pyright: ignore[reportUnusedFunction]: pytest autouse fixture — invoked by pytest via name, not directly accessed
    """Each test starts with both env vars cleared so the resolution-path
    branches fire deterministically. Also explicitly pops the env after
    the test — ``maybe_export_data_anchor`` sets env via raw
    ``os.environ[...] = ...`` which monkeypatch's restore-prior-state
    teardown does NOT track. The explicit pop closes the cross-suite
    leak that surfaced 2026-06-16 when CI's wall clock rolled past the
    hardcoded ``2026-06-15`` test posting and broke unrelated
    ``test_studio_data_route.py`` cells that read ``date.today()``
    through the leaked ``RECON_GEN_AS_OF_ANCHOR``."""
    monkeypatch.delenv(RECON_GEN_AS_OF_ANCHOR.name, raising=False)
    monkeypatch.delenv(RECON_GEN_AS_OF_ANCHOR_SOURCE.name, raising=False)
    yield
    os.environ.pop(RECON_GEN_AS_OF_ANCHOR.name, None)
    os.environ.pop(RECON_GEN_AS_OF_ANCHOR_SOURCE.name, None)


def _make_seeded_db(
    tmp_path: Path, *, prefix: str, posting: _date | None,
) -> Path:
    """Apply schema + insert one transaction at ``posting`` + refresh
    matviews. Returns the DuckDB file path. ``posting=None`` skips
    the insert so the matview projects data_anchor=NULL (cold DB)."""
    db_file = tmp_path / "demo.duckdb"
    conn = duckdb.connect(str(db_file))
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=prefix, dialect=Dialect.DUCKDB),
        dialect=Dialect.DUCKDB,
    )
    if posting is not None:
        ts_str = f"{posting.isoformat()} 10:00:00"
        conn.execute(
            f"""
            INSERT INTO {prefix}_transactions (
                id, account_id, account_name, account_role,
                account_scope, account_parent_role,
                amount_money, amount_direction, status, posting,
                transfer_id, rail_name, origin, metadata
            ) VALUES (
                'tx-1', 'acc-1', 'Account One', 'GLCash',
                'internal', 'GLCash', 100, 'Credit', 'Posted',
                TIMESTAMP '{ts_str}',
                'tx-1-tr', 'TestRail', 'InternalInitiated', '{{}}'
            )
            """
        )
        conn.commit()
    cur = conn.cursor()
    execute_script(
        cur, refresh_matviews_sql(instance, prefix=prefix, dialect=Dialect.DUCKDB),
        dialect=Dialect.DUCKDB,
    )
    conn.commit()
    conn.close()
    return db_file


def _make_cfg(db_file: Path, *, end_date: _date | None = None):
    """Build a TestConfig with the DuckDB at ``db_file``. ``end_date``
    pins ``cfg.test.generator.end_date``."""
    from recon_gen.common.config import TestConfig, TestGeneratorConfig
    return make_test_config(
        db_dialect=Dialect.DUCKDB,
        db_url=make_demo_database_url(Dialect.DUCKDB, db_file),
        db_table_prefix="dk_e2e",
        test=TestConfig(generator=TestGeneratorConfig(end_date=end_date)),
    )


def test_maybe_export_skips_when_end_date_pinned(tmp_path: Path) -> None:
    """cfg.test.generator.end_date is set → operator pinned in yaml.
    maybe_export_data_anchor is a no-op; env stays clear so the
    explicit-path branch in as_of_frame() (path 2) fires downstream."""
    db_file = _make_seeded_db(
        tmp_path, prefix="dk_e2e", posting=_date(2026, 6, 15),
    )
    cfg = _make_cfg(db_file, end_date=_date(2026, 5, 1))

    maybe_export_data_anchor(cfg)

    # Env untouched — operator pin wins.
    assert RECON_GEN_AS_OF_ANCHOR.name not in os.environ
    assert RECON_GEN_AS_OF_ANCHOR_SOURCE.name not in os.environ


def test_maybe_export_skips_when_env_already_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing RECON_GEN_AS_OF_ANCHOR (operator manual export or
    runner-driven chain determinism) wins. We don't second-guess."""
    monkeypatch.setenv(RECON_GEN_AS_OF_ANCHOR.name, "2026-04-01")
    db_file = _make_seeded_db(
        tmp_path, prefix="dk_e2e", posting=_date(2026, 6, 15),
    )
    cfg = _make_cfg(db_file)

    maybe_export_data_anchor(cfg)

    # Env retains operator pin; source marker NOT set (would imply
    # auto-derived).
    assert os.environ[RECON_GEN_AS_OF_ANCHOR.name] == "2026-04-01"
    assert RECON_GEN_AS_OF_ANCHOR_SOURCE.name not in os.environ


def test_maybe_export_warns_and_skips_on_cold_db(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Cold-DB case: schema applied but no rows. Matview projects
    data_anchor=NULL; maybe_export warns loudly + leaves env unset.
    Downstream AsOfFrame.live() falls through to date.today() — the
    DK.3-deprecated path; the warning is what catches the operator's
    eye in CI output."""
    db_file = _make_seeded_db(
        tmp_path, prefix="dk_e2e", posting=None,
    )
    cfg = _make_cfg(db_file)

    maybe_export_data_anchor(cfg)

    err = capsys.readouterr().err
    assert "warning" in err.lower()
    assert "data_anchor" in err
    assert "data apply" in err  # actionable: tells operator the fix
    assert RECON_GEN_AS_OF_ANCHOR.name not in os.environ
    assert RECON_GEN_AS_OF_ANCHOR_SOURCE.name not in os.environ


def test_maybe_export_warns_and_skips_on_connection_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Connection failure (DB URL points at a path that doesn't exist
    or matview missing on legacy deploys). Same shape as cold-DB —
    warn + leave env unset. Pre-DK deploys don't error-out on the
    v14.4.0 upgrade."""
    cfg = _make_cfg(
        db_file=tmp_path / "missing.duckdb"  # never seeded — no matview
    )

    maybe_export_data_anchor(cfg)

    err = capsys.readouterr().err
    assert "warning" in err.lower()
    # Either branch (connect-fail or matview-missing) emits "fall back"
    # or "data apply" / "live(wall-clock)" guidance.
    assert (
        "fall" in err.lower()
        or "live" in err.lower()
        or "data apply" in err.lower()
    )
    assert RECON_GEN_AS_OF_ANCHOR.name not in os.environ
    assert RECON_GEN_AS_OF_ANCHOR_SOURCE.name not in os.environ


def test_maybe_export_happy_path_exports_anchor_and_marker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Happy path: feed has data, matview reflects it, maybe_export
    exports both the anchor date AND the source marker. Downstream
    Info-sheet ``_resolve_as_of_at_emit`` reads (env, marker) and
    distinguishes auto-derived from operator-pinned env."""
    posting = _date(2026, 6, 15)
    db_file = _make_seeded_db(tmp_path, prefix="dk_e2e", posting=posting)
    cfg = _make_cfg(db_file)

    maybe_export_data_anchor(cfg)

    assert os.environ[RECON_GEN_AS_OF_ANCHOR.name] == "2026-06-15"
    assert os.environ[RECON_GEN_AS_OF_ANCHOR_SOURCE.name] == "data_anchor"
    # User-facing echo confirms which path resolved + locks the value.
    out = capsys.readouterr().out
    assert "2026-06-15" in out
    assert "data_anchor" in out
