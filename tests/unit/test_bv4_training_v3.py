"""BV.4.6 — anti-drift tests for the new /training/ surface.

Focused gates that catch the regression shapes most likely to bite
in a future commit. Not exhaustive — BV.4.8 cold-read covers the
operator-visible polish; these tests cover the structural contracts
the spike's 15 design locks (DL.1-DL.15) depend on.
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
from recon_gen.common.html._studio_training_v3 import (
    render_training_v3_landing,
)
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.plant_registry import PLANT_REGISTRY
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


# -- Render-shape pins -----------------------------------------------------


def test_landing_renders_all_25_registry_kinds() -> None:
    """DL.8 — every registry kind gets a card on the landing.
    Skipping one silently would hide a plant from the operator."""
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=False,
    )
    for entry in PLANT_REGISTRY:
        marker = f'data-test-training-kind="{entry.kind}"'
        assert marker in html, (
            f"registry kind {entry.kind!r} missing from landing render"
        )


def test_landing_renders_all_8_families_in_spec_order() -> None:
    """Per-family accordions render in §0.5 spec order. Reordering
    is operator-visible churn — pin it."""
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=False,
    )
    families_in_order = [
        "L1 Conservation", "L1 Cap", "L1 Aging",
        "L1 Chain coherence", "L1 Audit",
        "L2 Triage gaps", "L2 Coverage gaps", "L2FT Hygiene",
    ]
    last_pos = -1
    for family in families_in_order:
        marker = f'data-test-training-family="{family}"'
        pos = html.find(marker)
        assert pos > last_pos, (
            f"family {family!r} rendered out of order — expected after "
            f"position {last_pos}, found at {pos}"
        )
        last_pos = pos


def test_form_field_names_collision_safe_per_kind() -> None:
    """DL.8 — form fields named `form_<kind>_<primitive>` so the same
    primitive name (count, days_ago) can appear on multiple cards
    without colliding when the form posts."""
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=False,
    )
    for entry in PLANT_REGISTRY:
        for primitive in entry.primitives:
            expected = f'name="form_{entry.kind}_{primitive.name}"'
            assert expected in html, (
                f"primitive {primitive.name!r} on entry {entry.kind!r} "
                f"missing collision-safe form field name; expected "
                f"{expected!r} in landing"
            )


def test_tour_links_carry_prefix_query_param() -> None:
    """DL.6/DL.7 — Clean dashboard links route to `?prefix=<base>`;
    Violation dashboard links route to `?prefix=<base>_v`. The
    second only renders when v overlay exists."""
    html_no_overlay = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=False,
    )
    # Clean link always renders.
    assert "?prefix=recon-test" in html_no_overlay
    # Violation link is gated.
    assert "?prefix=recon-test_v" not in html_no_overlay

    html_with_overlay = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=True,
    )
    assert "?prefix=recon-test" in html_with_overlay
    assert "?prefix=recon-test_v" in html_with_overlay


# -- POST handlers --------------------------------------------------------


def test_post_session_start_303s_when_cfg_missing(
    writable_l2_yaml: Path,
) -> None:
    """Studio without cfg can't run the lifecycle; route should 303
    back to landing rather than crash."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:
        resp = c.post("/training/session-start", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/training/"


def test_post_cleanup_303s_when_cfg_missing(
    writable_l2_yaml: Path,
) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:
        resp = c.post("/training/cleanup", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/training/"


def test_post_apply_303s_when_cfg_missing(writable_l2_yaml: Path) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:
        resp = c.post(
            "/training/apply",
            data={"enabled_kinds": "phantom_rail"},
            follow_redirects=False,
        )
    assert resp.status_code == 303


# -- Banner rendering ------------------------------------------------------


def test_renders_failed_banner_when_failed_kinds_present() -> None:
    """DL.12 — page-top failed banner summarizes the failing kinds."""
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=True,
        failed_kinds={"phantom_rail": "ValueError: picker can't satisfy"},
    )
    assert "data-test-failed-banner" in html
    assert "1 plant(s) failed" in html
    assert "phantom_rail" in html


def test_renders_l2_stale_banner_when_l2_stale_flag_set() -> None:
    """DL.14 — soft staleness banner when L2 yaml mutates since
    Session Start."""
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=True,
        l2_stale=True,
        session_start_time="2026-05-31T10:00:00",
    )
    assert "data-test-l2-stale-banner" in html
    assert "L2 yaml has changed" in html
    assert "2026-05-31T10:00:00" in html


def test_failed_banner_truncates_at_5_kinds() -> None:
    """Long failure lists collapse to first 5 + "+N more" so the
    banner doesn't wrap a screen."""
    failed = {f"kind_{i}": f"err {i}" for i in range(7)}
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=True,
        failed_kinds=failed,
    )
    assert "+2 more" in html


def test_renders_session_status_banner() -> None:
    """Session status banner (the 303-redirect ?status= param)
    renders as a success banner. P1 fix from BV.4.0 cold-read."""
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=True,
        session_status="Session started — v overlay ready.",
    )
    assert "data-test-training-banner" in html
    assert "Session started" in html


# -- Bulk-toggle data attrs -----------------------------------------------


def test_per_family_bulk_toggle_buttons_present() -> None:
    """Every family accordion summary carries `[all]` + `[none]`
    bulk-toggle chips with `data-test-family-all=<family_id>` /
    `data-test-family-none=<family_id>` attrs."""
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=False,
    )
    for fid in (
        "L1_Conservation", "L1_Cap", "L1_Aging",
        "L1_Chain_coherence", "L1_Audit",
        "L2_Triage_gaps", "L2_Coverage_gaps", "L2FT_Hygiene",
    ):
        assert f'data-test-family-all="{fid}"' in html
        assert f'data-test-family-none="{fid}"' in html


def test_card_short_statement_renders_markdown() -> None:
    """BV.4.8 followup — Supersession Audit's `short_statement` was
    being rendered with literal backticks + asterisks because the
    template ``escape()``-d the markdown source straight through.
    Cards must surface ``**bold**`` / `` `code` `` formatted, not raw."""
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=False,
    )
    # supersession_audit's short_statement carries inline `_supersession_*`
    # and **not** — both must render through markdown, not as literal text.
    sup_idx = html.find('data-test-training-kind="supersession_audit"')
    assert sup_idx > 0, "supersession_audit card missing"
    sup_card = html[sup_idx:sup_idx + 2500]
    # The literal source markers must be absent.
    assert "**not**" not in sup_card, (
        "card still emits raw `**not**` — markdown isn't being rendered"
    )
    assert "`_supersession_*`" not in sup_card, (
        "card still emits raw backticked text — markdown isn't being rendered"
    )
    # The rendered HTML must contain the formatted equivalents.
    assert "<strong>not</strong>" in sup_card
    assert "<code>_supersession_*</code>" in sup_card


def test_renders_empty_state_for_zero_match_filter() -> None:
    """BV.4.8.P1.3 — when ``Show: Only enabled`` is set before any
    plant is checked, the page used to render as blank with just the
    Apply button. The empty-state hint markup is now always emitted
    (kept hidden by CSS until the JS filter zero-matches) so the
    operator sees actionable copy instead of confusion."""
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=False,
    )
    assert "data-test-empty-state" in html
    assert "No plants match this filter" in html


def test_top_level_bulk_toggle_buttons_present() -> None:
    """Top-level Select all / None chips + density badge."""
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=False,
    )
    assert "data-test-top-all" in html
    assert "data-test-top-none" in html
    assert "data-test-top-density" in html
