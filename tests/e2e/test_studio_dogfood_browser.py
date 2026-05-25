"""AI.2.d.2 — Studio editor dogfood via real WebKit (Playwright).

The HTTP transport (`tests/unit/test_studio_editor_driver.py`) drives
the editor over a Starlette ``TestClient`` — proves the server
accepts the form payload but doesn't exercise the actual HTML render,
inline JS toggles (BB.2's create-new sub-form show/hide), or the
browser's form-submit encoding. Per the user (2026-05-25): "is the
editor running via playwright? its not real until this is working" +
"without this making any other changes is ripe to break the tool"
(any UI refactor — AM Tailwind sweep, etc. — could silently regress
the operator workflow while the HTTP transport stayed green).

**Two operator-fidelity constraints** (user 2026-05-25):

1. **Discovery-only.** "The playwright can be the only source of
   info for the test. Meaning if you have to check the database to
   get data to fill something out, that's missing UI." Every value
   the test types is either (a) operator-fresh (a new identifier
   the operator invents) or (b) read from what the editor RENDERS.
   Never reach into the Python-side cache / reference YAML to grab
   a value to type — that'd be a missing-UI bug masquerading as a
   passing test.

2. **No URL editing past the initial entry.** "You are simulating
   a real user." The test enters one URL (the studio root); every
   subsequent navigation happens via clicking links or submitting
   forms. A `page.goto(URL)` after entry would prove a route works
   in isolation but not that an operator can discover it through
   the editor's nav.

The real Studio app (``make_studio_routes`` + ``make_app``) is
served — not the minimal ``build_editor_app`` stub — because that's
the surface the operator actually lands on.

AI.2.d.2 ship sequence (piece-by-piece per user 2026-05-25):
1. **Now (this file)** — minimum end-to-end smoke: spin uvicorn +
   real studio app + WebKit, click from home → account create form,
   submit, click home → account list, assert the new entity
   surfaces. Proves the operator-facing wire end-to-end.
2. **Next** — extend to multi-entity flows (rail with multi-select
   role checkboxes; reconciler create-new sub-form with the BB.2
   inline JS toggle; XOR groups).
3. **Then** — full `create_l2` walk for spec_example, with the same
   AI.4 + AI.5 equivalence assertions on the saved YAML.

Gated behind ``RECON_GEN_E2E=1`` per conftest.
"""

from __future__ import annotations

import pytest


playwright_sync_api = pytest.importorskip("playwright.sync_api")


from pathlib import Path

from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.l2.cache import L2InstanceCache
from decimal import Decimal

from recon_gen.common.l2.primitives import (
    Identifier,
    L2Instance,
    SingleLegRail,
    TwoLegRail,
)
from tests.e2e._drivers.studio_browser_editor import (
    StudioBrowserEditorDriver,
)


def _empty_l2() -> L2Instance:
    return L2Instance(
        accounts=(),
        account_templates=(),
        rails=(),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )


def _build_studio_asgi(cache: L2InstanceCache) -> object:
    """Mount the real Studio routes onto a bare Starlette app.

    ``make_app`` requires at least one dashboard (it's the
    production dashboards+studio entry point); the AI.2.d.2 surface
    is studio-only — no dashboards, no DB pool. Wrap the real
    `make_studio_routes(cache)` directly so the test exercises the
    SAME routes the operator hits, just without the dashboards mount
    the test doesn't need."""
    from starlette.applications import Starlette  # noqa: PLC0415 — lazy

    return Starlette(routes=make_studio_routes(cache))  # type: ignore[arg-type]: Starlette accepts Route | Mount list; make_studio_routes returns exactly that


@pytest.mark.browser
def test_browser_operator_creates_account_via_studio_nav(
    tmp_path: Path,
) -> None:
    """The smallest meaningful Playwright proof: a real operator
    lands on the studio home, navigates to the account-create form
    via the home page's nav link, fills the form, submits, returns
    home, navigates to the account list via the same nav, and sees
    the new entity surface. End-to-end via UI affordances only.

    Constraints both hold:
    - Discovery-only: every typed value is operator-fresh.
    - No URL editing past entry: only ``page.goto(base_url)`` once
      (inside ``driver.open()``); every subsequent navigation is
      a click on a rendered link or a form submit, expressed via
      driver verbs.
    - No Playwright in the test body — verbs only, per X.2.q's
      no-playwright-leak lint."""
    cache = L2InstanceCache(tmp_path / "smoke.yaml", _empty_l2())
    asgi = _build_studio_asgi(cache)
    with StudioBrowserEditorDriver.serving(asgi) as driver:
        driver.create_account(
            account_id="acct_smoke", role="SmokeRole",
        )
        driver.goto_account_list()
        assert driver.account_list_contains("acct_smoke"), (
            "Account create round-trip failed: 'acct_smoke' didn't "
            "surface on the account list page after the form submit. "
            "Either the form's commit silently failed or the list "
            "page doesn't render newly-created accounts.\n"
            f"List page body (first 2KB):\n{driver.page_body()[:2048]}"
        )
        # The role surfaces too — separate assert so the failure
        # message disambiguates "commit failed" vs "list-view UI
        # gap on the role column".
        assert "SmokeRole" in driver.page_body(), (
            "Account role 'SmokeRole' didn't surface on the list "
            "page — the form-fill carried the value but the list "
            "view may not project it. (Indicates a UI gap in the "
            "list-view rendering rather than a commit failure, since "
            "'acct_smoke' check above passed.)"
        )


@pytest.mark.browser
def test_browser_operator_creates_rail_with_role_checkbox(
    tmp_path: Path,
) -> None:
    """Piece 2a — exercises the rail subtype picker click-through +
    multi-select role-checkbox encoding (source_role +
    destination_role on a TwoLegRail). Picks two-leg because it's
    self-standing under S5 when `expected_net` is set — no
    BB.1/BB.2 reconciler attach needed (that's piece 2b).

    Discovery-only contract holds: the test creates two accounts
    with roles 'RoleA' / 'RoleB' FIRST so the rail-create form's
    source_role + destination_role checkbox groups have those
    options to discover. If the editor's multi-select field
    rendered roles from somewhere other than declared accounts,
    those checkboxes wouldn't appear and the rail-create would
    400 — exactly the kind of regression HTTP TestClient with
    hand-built form payloads can't catch."""
    cache = L2InstanceCache(tmp_path / "rail_smoke.yaml", _empty_l2())
    asgi = _build_studio_asgi(cache)
    with StudioBrowserEditorDriver.serving(asgi) as driver:
        # Plant two roles via account-create so the rail form's
        # checkbox groups have options to discover.
        driver.create_account(account_id="acct_src", role="RoleA")
        driver.create_account(account_id="acct_dst", role="RoleB")
        # Build a two-leg rail. expected_net=0 makes it self-standing
        # (S5 doesn't fire for two-leg with expected_net set).
        rail = TwoLegRail(
            name=Identifier("Rail_Smoke"),
            metadata_keys=(),
            # AI.9 (resolved 2026-05-25 via __post_init__ coerce):
            # tuples + bare Identifier both work now; keep tuples to
            # match the strict type hint (and stay pyright-clean if
            # this file ever lands in strict-scope).
            source_role=(Identifier("RoleA"),),
            destination_role=(Identifier("RoleB"),),
            expected_net=Decimal("0.00"),  # type: ignore[arg-type]: Money accepts Decimal at runtime
            origin="InternalInitiated",  # type: ignore[arg-type]: Origin literal accepts validated str at runtime
        )
        driver.create_rail(rail)
        # Verify via the rail list — same operator-facing UI surface
        # as the account test, no cache reads.
        driver.goto_rail_list()
        assert driver.rail_list_contains("Rail_Smoke"), (
            "Rail create round-trip failed: 'Rail_Smoke' didn't "
            "surface on the rail list page after the form submit. "
            "Either the role-checkbox-pick failed (missing role "
            "discovery), the validator rejected the commit, or the "
            "list view doesn't project new rails.\n"
            f"List page body (first 2KB):\n{driver.page_body()[:2048]}"
        )


@pytest.mark.skip(
    reason=(
        "AI.2.d.2 piece 3 infrastructure (driver + create_l2 walk + "
        "l2_path plumbing) is in place; the full walk currently "
        "hits BB.2 sub-form gaps: AI.10 (xor_groups form-pairing), "
        "AI.13 (aggregator-two-leg missing expected_net field). Fix "
        "those + re-enable. Per the no-skip-without-pointer rule, "
        "this skip is the AI.10+AI.13 pointer."
    ),
)
@pytest.mark.browser
def test_browser_full_create_l2_structural_equality(
    tmp_path: Path,
) -> None:
    """Piece 3 — the AI.2.d.2 acceptance gate.

    Drive the full ``create_l2(reference)`` walk via real WebKit:
    every account / template / rail / transfer_template / chain /
    limit_schedule / instance settings gets created through real
    form fills + submits. Then assert the saved YAML loads back
    STRUCTURALLY equal to the reference
    (`_assert_l2_structurally_equal` factored from the AI.4 HTTP
    test — Lock 1 granularity: parsed L2Instance dataclasses
    normalizing tuple order + description whitespace as
    non-structural).

    First-pass fixture is a fuzz seed (12345) rather than
    `spec_example` because `spec_example` exercises the AI.10 UI
    gap (xor_groups form-pairing absent on the rail-create form;
    the HTTP driver sneaks `leg_rail_xor_groups_*` into the POST
    body but the browser can only submit what the form renders).
    Per `tests/l2/fuzz.py`'s docstring, the fuzzer declines to
    generate Variable-direction rails — sidesteps the AI.10 case.
    spec_example coverage lands once AI.10 ships the form-pairing.

    Per Lock 3 amendment: Playwright pass runs ONCE on a
    deterministic L2 (HTTP transport covers `spec_example` +
    sasquatch_pr + 5 fuzz seeds). The browser pass proves the
    operator-facing form-render + submit fidelity that the HTTP
    TestClient can't.

    Long-running (~3-5 min): the walk creates ~30 entities
    sequentially via real browser form-fills, each with a 303
    redirect."""
    from tests.l2.fuzz import random_l2_yaml
    from tests.unit.test_studio_editor_driver import (
        _assert_l2_structurally_equal,
    )
    from recon_gen.common.l2.loader import load_instance

    reference_path = tmp_path / "fuzz_12345_reference.yaml"
    reference_path.write_text(random_l2_yaml(12345))
    reference = load_instance(reference_path)
    dogfood_path = tmp_path / "dogfood_fuzz_12345.yaml"

    cache = L2InstanceCache(dogfood_path, _empty_l2())
    asgi = _build_studio_asgi(cache)
    with StudioBrowserEditorDriver.serving(
        asgi, l2_path=dogfood_path,
    ) as driver:
        driver.create_l2(reference)
        driver.save_l2_to_path(dogfood_path)

    rebuilt = load_instance(dogfood_path)
    _assert_l2_structurally_equal(rebuilt, reference)


@pytest.mark.browser
def test_browser_operator_creates_rail_with_bb2_create_new_reconciler(
    tmp_path: Path,
) -> None:
    """Piece 2c — THE highest-value Playwright catch: the BB.2
    inline JS toggle for the create-new reconciler sub-form.

    The form's default reconciler mode is "Attach existing"; the
    operator must click the "Create new reconciler" radio for the
    inline JS to swap visibility (attach-block hidden →
    create-new block revealed). NO other test layer exercises that
    JS — HTTP TestClient submits whatever payload it likes
    regardless of which fields are visible. A regression to the JS
    (selector typo, event-name change, hidden-attribute drift)
    would silently leave operators with a form whose create-new
    fields can't be reached → composite atomic mutation
    un-invocable → BB.0's whole motivation broken.

    Test shape: create one account (provides a role), then create
    a non-aggregating single-leg rail that needs a reconciler
    (S3/C3 bilateral). Use create-new mode for the reconciler:
    operator clicks the radio → fills the TT's create-new fields →
    submits. Both the rail AND the new TT must surface on their
    respective list pages."""
    cache = L2InstanceCache(
        tmp_path / "bb2_smoke.yaml", _empty_l2(),
    )
    asgi = _build_studio_asgi(cache)
    with StudioBrowserEditorDriver.serving(asgi) as driver:
        driver.create_account(account_id="acct_leg", role="LegRole")
        rail = SingleLegRail(
            name=Identifier("Rail_BB2"),
            metadata_keys=(),
            # AI.9 (resolved 2026-05-25 via __post_init__ coerce):
            # tuples match the strict type hint; bare Identifier also
            # works via the guard.
            leg_role=(Identifier("LegRole"),),
            leg_direction="Credit",  # type: ignore[arg-type]: LegDirection literal accepts validated str
            origin="InternalInitiated",  # type: ignore[arg-type]: Origin literal accepts validated str
        )
        # Click "Create new" radio → fill TT's required fields →
        # submit. Both the rail AND the new TT land atomically (BB.1
        # composite mutation) — same as the BB.2 unit test, but via
        # real browser interaction with the inline JS toggle.
        driver.create_rail_with_new_reconciler(
            rail,
            reconciler_kind="transfer_template",
            reconciler_new_name="TT_BB2",
            reconciler_new_fields={
                "reconciler_new_expected_net": "0.00",
                "reconciler_new_completion": "business_day_end",
            },
        )
        # Verify both halves of the composite via the UI: rail
        # surfaces on rail list, TT surfaces on TT list.
        driver.goto_rail_list()
        assert driver.rail_list_contains("Rail_BB2"), (
            "BB.2 create-new composite failed: 'Rail_BB2' didn't "
            "surface on the rail list. Either the inline JS toggle "
            "left the create-new fields hidden, the radio click "
            "didn't register, or the server rejected the composite."
            f"\nList page (first 2KB):\n{driver.page_body()[:2048]}"
        )
        # AI.8 workaround — sub-pages don't link to other entity
        # lists; browser back gets us home so we can click the next
        # list link.
        driver.go_back()
        driver.goto_transfer_template_list()
        assert driver.transfer_template_list_contains("TT_BB2"), (
            "BB.2 create-new composite failed: 'TT_BB2' didn't "
            "surface on the TT list. The rail landed (above) but "
            "the new TT didn't — atomicity contract violated, OR "
            "the BB.2 sub-form's fields weren't read by the server "
            "(would indicate a name/wire mismatch between BB.2 UI "
            "+ _create_new_reconciler_with_rail)."
            f"\nList page (first 2KB):\n{driver.page_body()[:2048]}"
        )
