"""BU.1 — Vertical slice tests for the registry-driven Trainer.

Pins the registry shape, the canonical render shells, the routes,
and the dashboard_check round-trip for the single `phantom_rail`
entry. Lock 9's full parameterized anti-drift suite lands in BU.2b
once the registry is populated; BU.1 ships the scaffolding so the
abstraction is testable from day one.
"""

from __future__ import annotations

import duckdb

import shutil
from collections.abc import Iterator
from datetime import datetime
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
from recon_gen.common.html._studio_training_v2 import (
    coerce_form_to_kwargs,
    render_training_landing,
    render_training_plant_page,
    render_training_tour_page,
    resolve_section,
)
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.demo_etl_gaps import PHANTOM_RAIL_NAME
from recon_gen.common.l2.plant_registry import (
    PLANT_REGISTRY,
    PlantCategory,
    PlantKindEntry,
    PrimitiveIntField,
    PrimitiveStringField,
    entries_by_family,
    get_entry,
)
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


# -- Registry shape --------------------------------------------------------


def test_registry_has_phantom_rail_entry() -> None:
    """BU.1 vertical slice — the registry ships ONE entry to start.
    BU.2b populates the rest."""
    assert len(PLANT_REGISTRY) >= 1
    entry = get_entry("phantom_rail")
    assert entry is not None
    assert entry.category == PlantCategory.L2_TRIAGE
    assert entry.family == "L2 Triage gaps"


def test_registry_primitives_are_typed() -> None:
    """Per Lock 8 — primitives are typed dataclasses, not strings.
    Adding a new primitive shape = one new dataclass + one renderer
    branch, no per-kind hand coding."""
    entry = get_entry("phantom_rail")
    assert entry is not None
    by_name = {p.name: p for p in entry.primitives}
    assert isinstance(by_name["count"], PrimitiveIntField)
    assert by_name["count"].default == 3
    assert by_name["count"].min_value == 1
    assert isinstance(by_name["rail_name"], PrimitiveStringField)
    assert by_name["rail_name"].default == PHANTOM_RAIL_NAME


def test_registry_entry_has_no_display_strings() -> None:
    """Lock 8 anti-drift — PlantKindEntry MUST NOT carry
    display_name / title / description. Those live on the typed
    section resolved at render time. A future regression where
    someone adds a `display_name` field to the dataclass fires
    this test."""
    entry = get_entry("phantom_rail")
    assert entry is not None
    field_names = {f for f in entry.__slots__}
    for forbidden in ("display_name", "title", "description", "help_text"):
        assert forbidden not in field_names, (
            f"PlantKindEntry should not carry display string {forbidden!r} "
            f"per Lock 8 — typed-section is the SoT"
        )


def test_resolve_section_returns_protocol_shape() -> None:
    """The renderer reads `section.title`, `.short_statement`,
    `.what_to_do` — every category's resolver must return that
    protocol shape."""
    entry = get_entry("phantom_rail")
    assert entry is not None
    section = resolve_section(entry)
    assert section.title
    assert section.short_statement
    assert section.what_to_do


def test_entries_by_family_groups_correctly() -> None:
    groups = entries_by_family()
    assert "L2 Triage gaps" in groups
    assert all(
        isinstance(e, PlantKindEntry) for e in groups["L2 Triage gaps"]
    )


# -- Render shells ---------------------------------------------------------


def test_render_landing_renders_accordion_with_registry_entries() -> None:
    """The landing iterates the registry; adding an entry adds a
    row with zero new render code."""
    html = render_training_landing()
    assert 'data-test-training-family="L2 Triage gaps"' in html
    assert 'data-test-training-kind="phantom_rail"' in html
    # Each entry's title comes from the resolved section, not a
    # hard-coded display string.
    # BU.2a — section title comes from the typed L2_Triage_Gaps.md SoT;
    # registry kind "phantom_rail" maps to GapKind "unmatched_rail" via
    # section_kind override.
    assert "Unmatched rail_name" in html


def test_render_plant_page_renders_form_from_primitives() -> None:
    """One canonical template; primitives data-drive the form fields."""
    entry = get_entry("phantom_rail")
    assert entry is not None
    html = render_training_plant_page(entry)
    # Section title at the top.
    # BU.2a — section title comes from the typed L2_Triage_Gaps.md SoT;
    # registry kind "phantom_rail" maps to GapKind "unmatched_rail" via
    # section_kind override.
    assert "Unmatched rail_name" in html
    # Form action points back to the same URL.
    assert 'action="/training/plant/phantom_rail"' in html
    # Both primitives render with the right input type.
    assert '<input type="number" name="count"' in html
    assert 'value="3"' in html
    assert '<input type="text" name="rail_name"' in html
    assert 'value="legacy_card_swipe"' in html
    # Submit button + tour link both wire to the registry kind.
    assert 'id="training-plant-btn"' in html
    assert 'href="/training/tour/phantom_rail"' in html


def test_render_plant_page_preserves_submitted_form_values() -> None:
    """BU.1.10 — when ``form_values`` is supplied (POST re-render),
    the inputs render the submitted value, not the primitive default.
    Cold-read trust-killer: banner said one thing, form showed another."""
    entry = get_entry("phantom_rail")
    assert entry is not None
    html = render_training_plant_page(
        entry,
        form_values={"count": "7", "rail_name": "bu1_cold_read_rail"},
    )
    assert 'value="7"' in html
    assert 'value="bu1_cold_read_rail"' in html
    # Default values must NOT leak through when an override was supplied.
    assert 'value="3"' not in html
    assert 'value="legacy_card_swipe"' not in html


def test_render_plant_page_partial_form_values_use_default_for_missing() -> None:
    """If only some fields were submitted (shouldn't happen with browser
    forms but defensive coding wins), missing fields fall back to default.
    Keeps the override semantics consistent with coerce_form_to_kwargs."""
    entry = get_entry("phantom_rail")
    assert entry is not None
    html = render_training_plant_page(
        entry,
        form_values={"rail_name": "only_rail_provided"},
    )
    assert 'value="only_rail_provided"' in html
    assert 'value="3"' in html  # count primitive falls back to default


def test_render_plant_page_no_form_values_uses_defaults() -> None:
    """Initial GET path — form_values=None means primitives use their
    own defaults exactly as the BU.1 vertical-slice render did."""
    entry = get_entry("phantom_rail")
    assert entry is not None
    html = render_training_plant_page(entry)
    assert 'value="3"' in html
    assert 'value="legacy_card_swipe"' in html


def test_render_tour_page_embeds_iframe_at_destination() -> None:
    entry = get_entry("phantom_rail")
    assert entry is not None
    html = render_training_tour_page(entry)
    assert 'data-test-tour-iframe="phantom_rail"' in html
    assert 'src="/etl/triage"' in html


# -- Form coercion --------------------------------------------------------


def test_coerce_form_int_field_parses_string() -> None:
    entry = get_entry("phantom_rail")
    assert entry is not None
    kwargs = coerce_form_to_kwargs(entry, {"count": "7", "rail_name": "foo"})
    assert kwargs == {"count": 7, "rail_name": "foo"}


def test_coerce_form_missing_field_uses_default() -> None:
    entry = get_entry("phantom_rail")
    assert entry is not None
    kwargs = coerce_form_to_kwargs(entry, {})
    assert kwargs == {"count": 3, "rail_name": PHANTOM_RAIL_NAME}


def test_coerce_form_invalid_int_falls_back_to_default() -> None:
    entry = get_entry("phantom_rail")
    assert entry is not None
    kwargs = coerce_form_to_kwargs(
        entry, {"count": "not-a-number", "rail_name": "x"},
    )
    assert kwargs["count"] == 3


# -- Routes ----------------------------------------------------------------


def test_training_landing_route_returns_200(writable_l2_yaml: Path) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/training/")
        assert resp.status_code == 200
        body = resp.text
    assert "Studio · Training" in body
    assert 'data-test-training-kind="phantom_rail"' in body


def test_training_plant_get_renders_form(writable_l2_yaml: Path) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/training/plant/phantom_rail")
        assert resp.status_code == 200
        body = resp.text
    assert 'name="count"' in body
    assert 'name="rail_name"' in body


def test_training_plant_unknown_kind_404s(writable_l2_yaml: Path) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/training/plant/not_a_real_kind")
        assert resp.status_code == 404


def test_training_tour_route_renders(writable_l2_yaml: Path) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/training/tour/phantom_rail")
        assert resp.status_code == 200
        body = resp.text
    assert 'data-test-tour-iframe="phantom_rail"' in body


def test_training_plant_post_without_cfg_redirects_to_landing(
    writable_l2_yaml: Path,
) -> None:
    """Unit-test surface (no cfg) — POST gates redirect to /training/
    rather than crash. Mirrors the etl_run POST-without-cfg pattern."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.post(
            "/training/plant/phantom_rail",
            data={"count": "5", "rail_name": "x"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/training/"


# -- Lock 9 dashboard_check end-to-end -------------------------------------


def test_phantom_rail_plant_surfaces_on_etl_triage(
    writable_l2_yaml: Path,
) -> None:
    """Lock 9 contract: plant → query → assert dashboard surface lit up.

    For `phantom_rail` the dashboard_check is URL-based (Triage is
    computed at query time from `detect_gaps()` against live tables,
    not a stored matview). After the plant fires, GET /etl/triage
    should contain the planted rail name."""
    import asyncio
    import os
    import sqlite3
    import tempfile

    from recon_gen.common.db import (
        AsyncConnectionPool, execute_script, make_connection_pool,
    )
    from recon_gen.common.l2 import load_instance
    from recon_gen.common.l2.contract import derive_column_contracts
    from recon_gen.common.l2.triage import detect_gaps
    from recon_gen.common.sql.dialect import Dialect

    entry = get_entry("phantom_rail")
    assert entry is not None
    inst = load_instance(writable_l2_yaml)
    prefix = writable_l2_yaml.stem

    # Build a seeded sqlite with the spec_example schema + plant the
    # registry entry's SQL against it. Then run detect_gaps + assert
    # the plant surfaced as an unmatched_rail gap.
    fd, db_path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    conn = duckdb.connect(db_path)
    conn.execute(
        f"CREATE TABLE {prefix}_transactions ("
        "entry INTEGER PRIMARY KEY AUTOINCREMENT, "
        "id TEXT NOT NULL, account_id TEXT NOT NULL, "
        "account_role TEXT, account_parent_role TEXT, "
        "account_scope TEXT NOT NULL, "
        "amount_money BIGINT NOT NULL, amount_direction TEXT NOT NULL, "
        "status TEXT NOT NULL, posting TIMESTAMP NOT NULL, "
        "transfer_id TEXT NOT NULL, transfer_parent_id TEXT, "
        "rail_name TEXT NOT NULL, "
        "template_name TEXT, origin TEXT NOT NULL, metadata TEXT)"
    )
    conn.commit()

    # Invoke the registry's plant function exactly as the route would.
    sql = entry.plant_function(
        prefix=prefix,
        dialect=Dialect.DUCKDB,
        anchor=datetime(2026, 5, 30, 14, 0, 0),
        count=4,
        rail_name=PHANTOM_RAIL_NAME,
    )
    cur = conn.cursor()
    execute_script(cur, sql, dialect=Dialect.DUCKDB)
    conn.commit()
    conn.close()

    cfg = make_test_config(
        dialect=Dialect.DUCKDB, demo_database_url=db_path,
    )
    pool: AsyncConnectionPool = asyncio.run(make_connection_pool(cfg))
    try:
        contracts = derive_column_contracts(inst)
        gaps = asyncio.run(detect_gaps(
            pool, prefix, inst, contracts, dialect=Dialect.DUCKDB,
        ))
    finally:
        asyncio.run(pool.close())
        if os.path.exists(db_path):
            os.unlink(db_path)

    # The plant surfaces as an unmatched_rail gap with the operator-
    # supplied rail_name + count.
    phantom = [g for g in gaps if g.observed_value == PHANTOM_RAIL_NAME]
    assert len(phantom) == 1
    assert phantom[0].kind == "unmatched_rail"
    assert phantom[0].evidence.row_count == 4


# -- BU.1.6 — clean-baseline reset (Trainer-mode noise-free starting point)


# BU.1.6's "Reset to clean baseline" button is DELETED — BV.4.0's
# /training/ landing exposes Session Start / Apply / Cleanup instead.
# The v2 /training/reset route stays alive but is no longer linked
# from the landing.


def test_plant_page_also_carries_reset_button(
    writable_l2_yaml: Path,
) -> None:
    """The per-kind plant page also offers the reset so the operator
    can re-baseline mid-flow without bouncing back to the landing."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/training/plant/phantom_rail").text
    assert 'action="/training/reset"' in body
    assert "Re-baseline" in body


# BU's `/training/?reset=1` banner test is DELETED — v3's landing
# (BV.4.0) doesn't carry the v2 reset concept. /training/?status=…
# is the v3-equivalent (an arbitrary banner message); covered
# implicitly by the BV.4.1 route tests.


def test_landing_omits_reset_banner_when_not_set(
    writable_l2_yaml: Path,
) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/training/").text
    assert "data-test-training-reset-banner" not in body


def test_training_reset_without_cfg_redirects_to_landing(
    writable_l2_yaml: Path,
) -> None:
    """Unit-test surface (no cfg) — POST /training/reset bails to
    /training/ rather than crash, mirroring the etl_run POST pattern."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.post("/training/reset", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/training/"
