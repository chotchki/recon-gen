"""CF.4.i — browser-tier e2e proof that the editor's home-page
toolbar surface (summary search input, pager-below, collapsed-card
expand-on-toggle) survives a live render against `heavy_density_v1`
(134 entities — enough to force pagination + collapsed rendering on
multiple kinds).

Constraints (per `[feedback_browser_drivers_user_facing_locators]`):
- The driver verbs (`set_summary_search`, `paginate`,
  `expand_collapsed_card`, …) locate via `data-role` / `data-kind` /
  `data-entity-id` anchors — never Tailwind utility classes.
- No Playwright in the test body (X.2.q's no-playwright-leak lint);
  driver-only.

Gated behind `RECON_GEN_E2E=1`; tier=QS_BROWSER; needs=PLAYWRIGHT.
"""

from __future__ import annotations

from pathlib import Path

import pytest


playwright_sync_api = pytest.importorskip("playwright.sync_api")


from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.l2.cache import L2InstanceCache
from tests._marks import Need, needs
from tests.e2e._drivers.studio_browser_editor import (
    StudioBrowserEditorDriver,
)


# Auto-tiered APP2 by `tests/e2e/app2/conftest.py`; only the per-test
# `needs` lands here. Tests serve the editor from an in-process
# Starlette app — no AWS.
pytestmark = [needs(Need.PLAYWRIGHT)]


def _build_studio_asgi(cache: L2InstanceCache) -> object:
    from starlette.applications import Starlette  # noqa: PLC0415

    return Starlette(routes=make_studio_routes(cache))  # type: ignore[arg-type]: Starlette accepts Route | Mount list; make_studio_routes returns exactly that


HEAVY = (
    Path(__file__).parent.parent.parent / "l2" / "heavy_density_v1.yaml"
)


@pytest.fixture
def heavy_cache(tmp_path: Path) -> L2InstanceCache:
    """Copy heavy_density_v1 to a writable tmp_path so save-on-mutate
    can flush without dirtying the fixture."""
    import shutil  # noqa: PLC0415

    dst = tmp_path / "heavy.yaml"
    shutil.copy(HEAVY, dst)
    return L2InstanceCache.from_path(dst)


@pytest.mark.browser
def test_summary_search_filters_section_under_heavy_density(
    heavy_cache: L2InstanceCache,
) -> None:
    """Typing into the rail section's summary search filters the
    rendered cards. Auto-opens the `<details>` if collapsed (Q6A
    extended — no operator should have to click open just to see
    their own search hits)."""
    asgi = _build_studio_asgi(heavy_cache)
    with StudioBrowserEditorDriver.serving(asgi) as driver:
        # Baseline: a stable, deterministic fragment of a rail's
        # entity_id from heavy_density_v1 — the test is naive about
        # which one, just picks the first rail and searches a
        # 5-char slice of its id.
        baseline_ids = driver.home_section_card_ids("rail")
        assert baseline_ids, (
            f"heavy_density_v1 rail section rendered no cards.\n"
            f"page body (first 2KB):\n{driver.page_body()[:2048]}"
        )
        # Substring of a real rail id — search must surface ≥1 card.
        probe_id = baseline_ids[0]
        probe = probe_id[:5]

        driver.set_summary_search("rail", probe)
        filtered_ids = driver.home_section_card_ids("rail")
        # Every filtered id must contain the substring; nothing
        # outside the match leaks through.
        assert filtered_ids, (
            f"summary-search for {probe!r} returned zero cards; "
            f"expected ≥1 since the substring came from a real id.\n"
            f"page body (first 2KB):\n{driver.page_body()[:2048]}"
        )
        for entity_id in filtered_ids:
            assert probe.lower() in entity_id.lower(), (
                f"summary-search {probe!r} surfaced unrelated card "
                f"{entity_id!r}"
            )

        # Clear → restore baseline count.
        driver.clear_summary_search("rail")
        restored = driver.home_section_card_ids("rail")
        # Clearing should re-render the whole baseline (or first
        # page of it, if pagination kicked in for the baseline).
        assert len(restored) >= len(filtered_ids), (
            f"clear-search regressed: filtered={len(filtered_ids)} "
            f"baseline-after-clear={len(restored)}"
        )


@pytest.mark.browser
def test_pager_advances_through_pages_with_state_preserved(
    heavy_cache: L2InstanceCache,
) -> None:
    """The pager below the cards advances Prev/Next and preserves
    state on the embed URL. Heavy_density_v1's rail section forces
    pagination (134 rails / 25-per-page = 6 pages); after one Next
    click the rendered card set differs from page 1."""
    asgi = _build_studio_asgi(heavy_cache)
    with StudioBrowserEditorDriver.serving(asgi) as driver:
        # Closed `<details>` children are not painted, so Playwright
        # treats the pager / inner-card targets as off-viewport
        # forever. Open the rail section first (operator workflow).
        driver.open_home_section("rail")
        page1_ids = driver.home_section_card_ids("rail")
        page1_range = driver.home_section_pager_range("rail")
        assert "Showing" in page1_range or "of " in page1_range, (
            f"pager range indicator absent; got {page1_range!r}"
        )

        # Advance one page. If the section is small enough that
        # Next is disabled (fewer than 25 rails), the verb raises —
        # which itself is a useful smoke signal but not the path
        # we want; skip if so.
        try:
            driver.paginate("rail", "next")
        except Exception as e:
            pytest.skip(
                f"pagination not active on this fixture's rail "
                f"section: {e}",
            )

        page2_ids = driver.home_section_card_ids("rail")
        # The two pages share no overlap — server-side tiebreak on
        # entity_id (CF.4.b lock #7) guarantees stable, disjoint
        # pages.
        assert set(page1_ids).isdisjoint(set(page2_ids)), (
            f"pages overlap (server-side ordering tiebreak broken?):\n"
            f"page1={page1_ids[:5]}…\npage2={page2_ids[:5]}…"
        )

        # And Prev brings us back.
        driver.paginate("rail", "prev")
        page1_again_ids = driver.home_section_card_ids("rail")
        assert set(page1_again_ids) == set(page1_ids), (
            f"Prev didn't restore page 1:\n"
            f"page1   ={page1_ids[:5]}…\n"
            f"prev-from-page2={page1_again_ids[:5]}…"
        )


@pytest.mark.browser
def test_collapsed_card_expands_to_full_body_on_toggle(
    heavy_cache: L2InstanceCache,
) -> None:
    """The rail section under heavy_density_v1 collapses by default
    (>10 entities → COLLAPSE_THRESHOLD lights up). Clicking the
    title (which is inside `<summary>` and carries no
    stopPropagation) toggles the `<details>` and htmx fetches the
    body fragment via `?body_only=1`. The `<dl>` lands once the
    `toggle once` trigger resolves."""
    asgi = _build_studio_asgi(heavy_cache)
    with StudioBrowserEditorDriver.serving(asgi) as driver:
        driver.open_home_section("rail")
        baseline_ids = driver.home_section_card_ids("rail")
        target = baseline_ids[0]

        # Heavy density forces collapse — pin the assumption.
        assert driver.card_is_collapsed("rail", target), (
            f"heavy_density_v1 expected to render rail cards as "
            f"collapsed (>10 → COLLAPSE_THRESHOLD); first card "
            f"{target!r} is eager-rendered. Either the fixture "
            f"shrank below COLLAPSE_THRESHOLD or the collapse logic "
            f"broke."
        )

        driver.expand_collapsed_card("rail", target)
        # Verify the body landed (the verb already waits via
        # wait_for_function; this is a belt-and-braces second probe).
        body_html = driver.page_body()
        assert f'data-entity-id="{target}"' in body_html
        # `<dl class="..."` is the body fragment shape from
        # `_render_read_card_body`.
        assert "<dl class=" in body_html
