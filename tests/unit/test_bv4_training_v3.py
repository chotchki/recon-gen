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


def test_post_reclone_303s_when_cfg_missing(
    writable_l2_yaml: Path,
) -> None:
    """BV.4.9 — Force rebuild from base route. Returns post-DL.9
    as the operator's "throw away v overlay" escape hatch."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:
        resp = c.post("/training/reclone", follow_redirects=False)
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


def test_failed_banner_exposes_per_kind_reason_inline() -> None:
    """CF.0 Fix B — banner ships a <details> expander listing each
    failing kind alongside the first line of its error message so
    operators don't have to hunt across cards to learn why a
    Session-Start Apply skipped a plant on their own L2."""
    failed = {
        "limit_breach_outbound": (
            "ValueError: limit_breach_outbound plant: no Account-class "
            "template with a Limits Schedule declared in this L2."
        ),
        "drift": (
            "ValueError: drift plant: no 2-leg Rail with destination "
            "matching the template role declared in this L2."
        ),
    }
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=True,
        failed_kinds=failed,
    )
    assert "show why each plant failed" in html
    assert "limit_breach_outbound" in html
    assert "no Account-class template with a Limits Schedule" in html
    assert "no 2-leg Rail with destination" in html


def test_first_line_of_error_helper() -> None:
    """Unit-level guard so future refactors of the per-kind banner
    keep stripping multi-line tracebacks. Single-line input passes
    through unchanged; multi-line collapses to the first line."""
    from recon_gen.common.html._studio_training_v3 import (
        _first_line_of_error,
    )

    assert _first_line_of_error("") == ""
    assert (
        _first_line_of_error("ValueError: no rail")
        == "ValueError: no rail"
    )
    multi = (
        "ValueError: no rail\n"
        '  File "auto_scenario.py", line 1338, in _pick_template\n'
        "    raise ValueError(...)"
    )
    assert _first_line_of_error(multi) == "ValueError: no rail"


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


def test_planted_badge_renders_for_enabled_kinds() -> None:
    """BV.4.10.a — currently-planted pill makes the v overlay state
    obvious. Only kinds in `enabled_kinds` get the badge; not-applied
    kinds show no badge so the green pill stays a high-signal cue."""
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=True,
        enabled_kinds=("phantom_rail", "limit_breach_outbound"),
    )
    assert 'data-test-planted-badge-phantom_rail' in html
    assert 'data-test-planted-badge-limit_breach_outbound' in html
    # Not-enabled kind shouldn't have the badge.
    assert 'data-test-planted-badge-overdraft' not in html


def test_planted_badge_absent_when_no_kinds_enabled() -> None:
    """Fresh post-Session-Start state — no plants applied, no badges."""
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=True,
    )
    assert 'data-test-planted-badge-' not in html


def test_session_start_running_banner_renders() -> None:
    """BV.4.10.d — when a Session Start task is in flight the
    landing renders a banner with collapsible live-tail wrapper
    polling `/training/session-start/stream`, plus an animated
    spinner (operator can't tell from a static emoji whether the
    page is alive)."""
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=False,
        session_start_running=True,
    )
    assert "data-test-training-session-start-banner" in html
    assert "Session Start in progress" in html
    assert 'hx-get="/training/session-start/stream"' in html
    assert "animate-spin" in html
    # The pre-spinner ⏳ emoji should be gone.
    assert "⏳" not in html


def test_op_in_flight_disables_all_action_buttons() -> None:
    """BV.4.10.d.3 — while ANY op runs (Session Start or Apply),
    every action button gets `disabled` + visual-affordance classes
    so the operator can't double-click. The server already no-ops
    re-POSTs, but "I clicked, nothing happened" is bad UX."""
    # Session Start running → all buttons (incl. Apply) disabled.
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=True,
        session_start_running=True,
    )
    # Find each control button + check it carries disabled + the
    # opacity-50 cursor-not-allowed affordance.
    for button_id in (
        "training-session-start-btn", "training-reclone-btn",
        "training-cleanup-btn", "training-apply-btn",
    ):
        marker = f'id="{button_id}"'
        idx = html.find(marker)
        assert idx > 0, f"button {button_id} missing"
        # Disabled attr + visual affordance must both be on the
        # button tag (next ~300 chars from the id marker).
        button_chunk = html[idx:idx + 600]
        assert "disabled" in button_chunk, (
            f"{button_id} should carry disabled while op runs"
        )
        assert "cursor-not-allowed" in button_chunk, (
            f"{button_id} should carry cursor-not-allowed affordance"
        )

    # Apply running → same disabled state.
    html_apply = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=True,
        apply_running=True,
        apply_pending_count=2,
    )
    for button_id in (
        "training-session-start-btn", "training-reclone-btn",
        "training-cleanup-btn", "training-apply-btn",
    ):
        idx = html_apply.find(f'id="{button_id}"')
        button_chunk = html_apply[idx:idx + 600]
        assert "disabled" in button_chunk, (
            f"{button_id} should be disabled during Apply"
        )

    # Idle: nothing running → no disabled attr on session-start /
    # reclone / cleanup (Apply still has its own pre-op-disabled
    # logic for the no-v-overlay case but here we have one).
    html_idle = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=True,
    )
    for button_id in (
        "training-session-start-btn", "training-reclone-btn",
        "training-cleanup-btn",
    ):
        idx = html_idle.find(f'id="{button_id}"')
        button_chunk = html_idle[idx:idx + 600]
        assert "disabled" not in button_chunk, (
            f"{button_id} shouldn't be disabled when nothing's running"
        )


def test_apply_running_banner_renders() -> None:
    """BV.4.10.d — Apply gets the same banner+spinner+live-tail
    treatment as Session Start. The hint reflects the pending plant
    count so the operator sees what's being applied."""
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=True,
        apply_running=True,
        apply_pending_count=3,
    )
    assert "data-test-training-apply-banner" in html
    assert "Apply in progress" in html
    assert 'hx-get="/training/apply/stream"' in html
    assert "animate-spin" in html
    assert "3 plant(s)" in html


def test_session_start_running_banner_absent_when_idle() -> None:
    """Default state: no in-flight Session Start, no banner."""
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=False,
    )
    assert "data-test-training-session-start-banner" not in html


def test_session_start_live_tail_fragment_running_arms_next_poll() -> None:
    """Stream endpoint's fragment carries `hx-trigger="every 1s"`
    while the task runs, then drops it on completion (the route
    handler also sends the HX-Trigger reload signal at that point)."""
    from recon_gen.common.html._studio_training_v3 import (
        render_training_session_start_live_tail,
    )
    running_html = render_training_session_start_live_tail(
        events=[{"event": "session_start:etl_begin"}], running=True,
    )
    assert 'hx-trigger="every 1s"' in running_html
    assert 'data-test-training-tail-state="running"' in running_html

    finished_html = render_training_session_start_live_tail(
        events=[
            {"event": "session_start:etl_begin"},
            {"event": "session_start:done"},
        ],
        running=False,
    )
    assert 'hx-trigger="every 1s"' not in finished_html
    assert 'data-test-training-tail-state="finished"' in finished_html
    assert 'data-test-training-tail-count="2"' in finished_html


def test_apply_diff_preview_element_rendered() -> None:
    """BV.4.10.b — the sticky Apply bar carries a diff-preview span
    the client-side JS updates on every checkbox toggle. The element
    must always be present (default text is "no changes pending").
    """
    html = render_training_v3_landing(
        base_prefix="recon-test",
        v_overlay_exists=True,
    )
    assert 'data-test-bv-apply-diff' in html
    assert 'no changes pending' in html


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
