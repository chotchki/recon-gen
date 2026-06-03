"""BT.4 — ``/studio/etl/triage`` route integration tests.

Covers the GET shape (header, empty-state when no gaps detected,
gap-card structure when gaps surface) + the no-DB-pool branch (unit
test surface). Gap-detector semantics are exhaustively covered in
``test_l2_triage``; this file narrows to the rendering wire shape.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from tests._test_helpers import make_test_config


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    src = _FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


def _build_app(yaml_path: Path) -> object:
    cache = L2InstanceCache.from_path(yaml_path)
    cfg = make_test_config()
    tree_app, sheet = build_smoke_app(cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    return make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(cache),
    )


def test_etl_triage_returns_200_with_header(writable_l2_yaml: Path) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/etl/triage")
        assert resp.status_code == 200
        body = resp.text
    assert "<title>Studio · ETL · Triage" in body
    assert "Studio · ETL · Triage" in body


def test_etl_triage_no_db_pool_renders_banner(writable_l2_yaml: Path) -> None:
    """Unit surface (no db_pool) — the page surfaces a 'No DB pool
    wired' banner instead of crashing."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/triage").text
    assert "No DB pool wired" in body


def test_etl_triage_carries_top_nav_with_triage_route_active(
    writable_l2_yaml: Path,
) -> None:
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    cfg = make_test_config()

    def fake_nav(active_href: str) -> str:
        return f'<nav data-test-active="{active_href}">NAV</nav>'

    routes = make_studio_routes(cache, top_nav_fn=fake_nav)
    tree_app, sheet = build_smoke_app(cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    app = make_app(
        dashboards={"smoke": served},
        studio_routes=routes,
    )
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/triage").text
    assert 'data-test-active="/etl/triage"' in body


def test_etl_triage_landing_card_for_triage_drops_coming_in_hint(
    writable_l2_yaml: Path,
) -> None:
    """BT.1 landing card for Triage drops its 'coming in BT.4' hint
    once BT.4 ships."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/").text
    assert "coming in BT.4" not in body
    assert 'href="/etl/triage"' in body


# -- Render-shape tests via in-process call to _render_etl_triage_page -------

# Direct render tests bypass the route's db_pool requirement; let us
# pin the gap-card shape without booting a full pool.

import asyncio
import os
import duckdb
import tempfile

from recon_gen.common.db import AsyncConnectionPool, make_connection_pool
from recon_gen.common.html._studio_routes import _render_etl_triage_page  # noqa: PLC0415
from recon_gen.common.sql.dialect import Dialect


def _seeded_pool_with_unmatched_rail(yaml_path: Path) -> tuple[AsyncConnectionPool, str]:
    """Build a seeded pool whose transactions table has one row whose
    rail_name doesn't resolve in spec_example.yaml."""
    cache = L2InstanceCache.from_path(yaml_path)
    prefix = yaml_path.stem  # path.stem matches make_studio_routes' default
    fd, db_path = tempfile.mkstemp(suffix=".duckdb")

    os.close(fd)

    os.unlink(db_path)
    conn = duckdb.connect(db_path)
    conn.execute(
        f"CREATE TABLE {prefix}_transactions ("
        "id TEXT PRIMARY KEY, "
        "rail_name TEXT, "
        "template_name TEXT, "
        "account_role TEXT, "
        "account_parent_role TEXT, "
        "amount_direction TEXT NOT NULL, "
        "transfer_parent_id TEXT, "
        "posting TIMESTAMP NOT NULL, "
        "metadata TEXT)"
    )
    # spec_example.yaml doesn't declare "phantom_rail" → gap.
    conn.execute(
        f"INSERT INTO {prefix}_transactions VALUES "
        "('tx-1', 'phantom_rail', NULL, 'X', NULL, 'Credit', NULL, "
        "'2030-01-05 09:00:00', NULL)"
    )
    conn.commit()
    conn.close()
    cfg = make_test_config(dialect=Dialect.DUCKDB, demo_database_url=db_path)
    pool = asyncio.run(make_connection_pool(cfg))
    _ = cache  # cache loaded but the render reads via cache parameter
    return pool, db_path


def test_etl_triage_with_pool_renders_gap_cards(
    writable_l2_yaml: Path,
) -> None:
    """Drive the render through with a seeded pool that produces one
    unmatched-rail gap; assert the card lands."""
    pool, db_path = _seeded_pool_with_unmatched_rail(writable_l2_yaml)
    try:
        cache = L2InstanceCache.from_path(writable_l2_yaml)
        body = asyncio.run(_render_etl_triage_page(
            cache, dev_log=False,
            db_pool=pool, dialect=Dialect.DUCKDB,
            prefix_override=None, cfg=None,
            demo_mode=False, top_nav_html="",
        ))
    finally:
        asyncio.run(pool.close())
        if os.path.exists(db_path):
            os.unlink(db_path)
    # Card surfaces with the right discriminator + CTA.
    assert 'data-test-gap-kind="unmatched_rail"' in body
    assert 'phantom_rail' in body
    assert '/l2_shape/rail/' in body
    # BTa.4 — header counts gaps + kinds; sole kind ⇒ section open.
    assert '1 gap across 1 kind' in body
    # Accordion section per kind.
    assert 'data-test-gap-kind-section="unmatched_rail"' in body
    # Single kind ⇒ default-open <details>.
    assert ' open>' in body or 'open data-test' in body


def test_etl_triage_empty_state_when_no_gaps(
    writable_l2_yaml: Path,
) -> None:
    """An empty transactions table means zero gaps → the success
    affirmation lands instead of cards."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    prefix = writable_l2_yaml.stem
    fd, db_path = tempfile.mkstemp(suffix=".duckdb")

    os.close(fd)

    os.unlink(db_path)
    conn = duckdb.connect(db_path)
    conn.execute(
        f"CREATE TABLE {prefix}_transactions ("
        "id TEXT PRIMARY KEY, rail_name TEXT, template_name TEXT, "
        "account_role TEXT, account_parent_role TEXT, "
        "amount_direction TEXT NOT NULL, transfer_parent_id TEXT, "
        "posting TIMESTAMP NOT NULL, metadata TEXT)"
    )
    conn.commit()
    conn.close()
    cfg = make_test_config(dialect=Dialect.DUCKDB, demo_database_url=db_path)
    pool = asyncio.run(make_connection_pool(cfg))
    try:
        body = asyncio.run(_render_etl_triage_page(
            cache, dev_log=False,
            db_pool=pool, dialect=Dialect.DUCKDB,
            prefix_override=None, cfg=None,
            demo_mode=False, top_nav_html="",
        ))
    finally:
        asyncio.run(pool.close())
        if os.path.exists(db_path):
            os.unlink(db_path)
    assert 'id="triage-empty"' in body
    assert 'No gaps detected' in body


# -- BTa.4 — accordion shape + retry banner -------------------------------


from recon_gen.common.html._studio_routes import (  # noqa: E402, PLC0415
    _render_triage_body,
    _render_triage_recomputing_banner,
)
from recon_gen.common.l2.triage import Gap, GapEvidence  # noqa: E402, PLC0415


def _gap(kind: str, observed: str, row_count: int, **extras: str) -> Gap:
    """Test factory — Gap.kind is a GapKind Literal in production, but
    the test exercise needs to construct unknown kinds + arbitrary
    strings on the fly. Cast at the construction boundary."""
    from typing import cast  # noqa: PLC0415

    from recon_gen.common.l2.triage import GapKind  # noqa: PLC0415

    return Gap(
        kind=cast(GapKind, kind),
        diagnosis=f"{observed} not declared ({row_count} rows).",
        observed_value=observed,
        evidence=GapEvidence(
            row_count=row_count,
            sample_transaction_id=f"tx-{observed}",
            extras=extras,
        ),
        link_target="/l2_shape/rail/new",
    )


def test_triage_body_groups_gaps_into_per_kind_accordion_sections() -> None:
    """BTa.4 — 4 distinct kinds across the gaps tuple ⇒ 4 accordion
    sections; multi-kind ⇒ all default-collapsed (so the operator
    sees the kind distribution before drilling)."""
    gaps = (
        _gap("unmatched_rail", "phantom", 5),
        _gap("unmatched_template", "phantom_tt", 2),
    )
    body = _render_triage_body(gaps)
    assert 'data-test-gap-kind-section="unmatched_rail"' in body
    assert 'data-test-gap-kind-section="unmatched_template"' in body
    # Multi-kind ⇒ no `open` attribute on the details elements.
    assert "open>" not in body.replace("<details", "<X")  # crude — no leftover " open>"


def test_triage_body_sorts_within_kind_by_row_count_desc() -> None:
    """High-volume gaps surface first within their kind — the cards
    that fix the most rows get the operator's eye first."""
    gaps = (
        _gap("unmatched_rail", "low_volume", 1),
        _gap("unmatched_rail", "high_volume", 999),
        _gap("unmatched_rail", "mid_volume", 50),
    )
    body = _render_triage_body(gaps)
    # high_volume card surfaces before mid_volume which surfaces before
    # low_volume in the rendered HTML.
    assert body.index("high_volume") < body.index("mid_volume") < body.index("low_volume")


def test_triage_body_volume_badge_in_section_header() -> None:
    """Section header carries the per-kind volume badge: total rows +
    distinct count."""
    gaps = (
        _gap("unmatched_rail", "a", 10),
        _gap("unmatched_rail", "b", 20),
        _gap("unmatched_rail", "c", 30),
    )
    body = _render_triage_body(gaps)
    # 60 rows total / 3 distinct gaps.
    assert "60 rows total" in body
    assert "3 distinct" in body


def test_triage_body_header_counts_gaps_and_kinds() -> None:
    """Top-of-page count says BOTH total + kind-count so the operator
    knows the spread before they collapse / expand sections."""
    gaps = (
        _gap("unmatched_rail", "a", 5),
        _gap("unmatched_template", "b", 3),
    )
    body = _render_triage_body(gaps)
    assert "2 gaps across 2 kinds" in body


def test_triage_gap_card_carries_per_kind_color_stripe() -> None:
    """Each card ships a `border-l-4 border-l-<token>` stripe per
    kind for visual scanning. Distinct kinds get distinct tokens."""
    gaps = (
        _gap("unmatched_rail", "a", 1),
        _gap("missing_metadata_key", "b", 1),
    )
    body = _render_triage_body(gaps)
    # Per-kind colors (defined in _GAP_KIND_STRIPES).
    assert "border-l-warning" in body
    assert "border-l-danger" in body


def test_triage_gap_card_renders_observed_value_as_title() -> None:
    """The card title IS the observed value (the operator-readable
    identifier of what's broken). Kind label moved up to the
    accordion section header — no redundant per-card banner."""
    gaps = (_gap("unmatched_rail", "phantom_rail_name", 7),)
    body = _render_triage_body(gaps)
    # Observed value is the card's h3.
    assert ">phantom_rail_name</h3>" in body
    # Volume badge sits next to the title.
    assert "7 rows" in body


def test_triage_gap_card_renders_evidence_as_dl_table_not_ul() -> None:
    """BTa.4 — evidence renders as a 2-column `<dl>` grid (key /
    value pairs), replacing the prior JSON-style `<ul>` dump."""
    gaps = (
        _gap(
            "unmatched_rail", "phantom", 3,
            declared_rails="rail_a, rail_b",
        ),
    )
    body = _render_triage_body(gaps)
    assert "<dl" in body
    assert ">declared_rails</dt>" in body
    assert ">rail_a, rail_b</dd>" in body


def test_triage_body_unknown_kind_still_renders() -> None:
    """Defensive — a new GapKind that lands without `_GAP_KIND_RENDER_ORDER`
    coverage falls through to a section at the end; never silently
    dropped."""
    gaps = (
        _gap("unmatched_rail", "a", 1),
        _gap("brand_new_kind", "b", 1),
    )
    body = _render_triage_body(gaps)
    assert 'data-test-gap-kind-section="brand_new_kind"' in body


def test_render_triage_recomputing_banner_renders_retry_link() -> None:
    """BTa.4 — transient gap-detector failure surfaces as a retry
    prompt rather than a raw 500 page."""
    exc = RuntimeError("lock held by matview refresh")
    html = _render_triage_recomputing_banner(exc)
    assert 'data-test-triage-state="recomputing"' in html
    assert "recomputing" in html
    assert 'href="/etl/triage"' in html
    assert "Retry now" in html
    # Error details disclose in a <details> for diagnostics.
    assert "<details" in html
    assert "lock held by matview refresh" in html


def test_render_triage_recomputing_banner_escapes_exception_repr() -> None:
    """Defensive — exception messages can carry user-controlled
    fragments (table names, SQL params). Escape before injection."""
    exc = RuntimeError("<script>alert(1)</script>")
    html = _render_triage_recomputing_banner(exc)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
