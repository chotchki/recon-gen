"""BX.6/11 — Editor role reframe (D1 wrapper-accordion).

Anti-drift tests for the BX.6 + BX.11 unified role reframe (cell
``bx-6-11-role-reframe``, design doc at
``docs/audits/bx_0_8_design_mockups/bx_6_11_role_reframe.md``).

Locks (per the design doc's `## Constraint summary (operator
locks)`):

1. Editor view only — YAML schema untouched. ``EntityKind`` still
   carries ``account`` + ``account_template`` distinct values.
2. Roles are the user-facing organizing principle on the home page.
   ``_HOME_SECTIONS`` no longer carries account / account_template;
   they nest inside a synthetic Roles wrapper at idx 0.
3. Single ``+ Add Role`` affordance opens a modal → picks 1:1 or
   1:N → routes to the existing per-kind create form.
4. CPA-readable: "1:1" / "1:N" on badges; "Singleton account" /
   "Templated role" as secondary-fg sublines.
5. ``data-*`` anchors (NEVER Tailwind utility classes).
6. Prior BX.6 Direction A chrome survives — numbered dependency
   order, completeness checkmarks per kind, singletons still
   above the building-blocks band.

What the tests pin (per the cell brief):

- ``_HOME_SECTIONS`` no longer contains ``account`` /
  ``account_template`` as top-level entries.
- The Roles wrapper renders first; carries ``data-section="roles"``
  + ``data-role-section="roles"`` + the [?] glossary trigger
  pointing at ``/studio/side-panel/glossary/roles-cardinality``.
- Sub-buckets render in 1:1-first order (1:1 ``Singleton accounts``
  before 1:N ``Templated roles``).
- ``+ Add Role`` modal has two cards with the correct hrefs +
  ``data-cardinality-choice="one-to-one" | "one-to-many"``.
- ``/l2_shape/account/`` h1 reads "Roles — 1:1";
  ``/l2_shape/account_template/`` reads "Roles — 1:N".
- Read cards carry ``data-cardinality-badge`` + the matching
  ``data-cardinality`` value + the secondary-fg subline.
- The instance_count_by_role helper returns the right shape (count
  from a seeded fixture; None on missing DB).
- GLOSSARY anchors all reachable via the standard side-panel route.
- Completeness rollup truth table — set/set→set; set/empty→partial;
  empty/empty→empty; etc.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.html._role_cardinality import (
    RoleCompleteness,
    compute_role_completeness,
    instance_count_by_role,
)
from recon_gen.common.html._side_panel import GLOSSARY
from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html._studio_routes import (
    ROLE_SUB_BUCKETS,
    _HOME_SECTIONS,
    make_studio_routes,
)
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.primitives import (
    Account,
    AccountTemplate,
    Identifier,
    L2Instance,
    Money,
    Name,
)
from tests._test_helpers import make_test_config


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    """Copy spec_example.yaml to a tempfile so PUT writes don't
    mutate the bundled fixture."""
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


# ---------------------------------------------------------------------------
# _HOME_SECTIONS structural pins
# ---------------------------------------------------------------------------


def test_home_sections_drops_account_kinds() -> None:
    """BX.6/11 lock — _HOME_SECTIONS no longer carries account or
    account_template as top-level entries. Both nest inside the
    synthetic Roles wrapper that the home renderer assembles
    outside this tuple."""
    kinds = tuple(k for k, _l, _a in _HOME_SECTIONS)
    assert "account" not in kinds, (
        "_HOME_SECTIONS leaks account; should nest under Roles wrapper"
    )
    assert "account_template" not in kinds, (
        "_HOME_SECTIONS leaks account_template; should nest under Roles"
    )


def test_home_sections_lead_with_rails() -> None:
    """Roles wrapper takes idx 0 in the rendered output (assembled
    outside _HOME_SECTIONS); rails is the first entry IN the tuple
    so it renders at idx 1 of the home page."""
    assert _HOME_SECTIONS[0][0] == "rail"


def test_role_sub_buckets_1_1_first() -> None:
    """Locked OQ1 — 1:1 first, then 1:N (simpler case teaches the
    concept; templated is the extension)."""
    kinds = tuple(k for k, _l, _a, _p, _c in ROLE_SUB_BUCKETS)
    assert kinds == ("account", "account_template")
    # Token values match what the render uses for data-cardinality.
    tokens = tuple(c for _k, _l, _a, _p, c in ROLE_SUB_BUCKETS)
    assert tokens == ("one-to-one", "one-to-many")


# ---------------------------------------------------------------------------
# Home page rendered HTML — wrapper accordion + sub-buckets
# ---------------------------------------------------------------------------


def test_home_page_renders_roles_wrapper_first(
    writable_l2_yaml: Path,
) -> None:
    """The Roles wrapper sits before every other section block."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    roles_idx = body.find('data-section="roles"')
    rails_idx = body.find('data-kind="rail"')
    chain_idx = body.find('data-kind="chain"')
    assert 0 <= roles_idx < rails_idx, "Roles wrapper must precede rails"
    assert roles_idx < chain_idx, "Roles wrapper must precede chains"


def test_home_page_role_wrapper_carries_data_role_section_anchor(
    writable_l2_yaml: Path,
) -> None:
    """Per memory feedback_browser_drivers_user_facing_locators —
    test selectors target data-* attributes, not Tailwind classes."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    assert 'data-section="roles"' in body
    assert 'data-role-section="roles"' in body


def test_home_page_role_wrapper_carries_glossary_trigger(
    writable_l2_yaml: Path,
) -> None:
    """Locked OQ2(a) — outer Roles header carries the [?] trigger
    pointing at the roles-cardinality glossary anchor."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    assert 'hx-get="/studio/side-panel/glossary/roles-cardinality"' in body


def test_home_page_role_sub_buckets_render_in_1_1_then_1_n_order(
    writable_l2_yaml: Path,
) -> None:
    """OQ1 lock — the 1:1 sub-bucket renders before 1:N inside the
    Roles wrapper."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    one_to_one_idx = body.find('data-role-cardinality="one-to-one"')
    one_to_many_idx = body.find('data-role-cardinality="one-to-many"')
    assert one_to_one_idx >= 0, "1:1 sub-bucket missing"
    assert one_to_many_idx >= 0, "1:N sub-bucket missing"
    assert one_to_one_idx < one_to_many_idx, (
        "1:1 must render before 1:N (OQ1 lock)"
    )


def test_home_page_sub_buckets_carry_per_kind_data_anchors(
    writable_l2_yaml: Path,
) -> None:
    """Sub-buckets keep their EntityKind anchor so the existing embed
    body lazy-load wiring + the search input continue to work."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    # Both kinds appear nested inside the wrapper.
    assert 'data-kind="account" data-role-cardinality="one-to-one"' in body
    assert (
        'data-kind="account_template" '
        'data-role-cardinality="one-to-many"'
    ) in body


# ---------------------------------------------------------------------------
# + Add Role modal
# ---------------------------------------------------------------------------


def test_home_page_renders_add_role_modal_with_two_cards(
    writable_l2_yaml: Path,
) -> None:
    """The modal carries two card-anchors: 1:1 → /l2_shape/account/new,
    1:N → /l2_shape/account_template/new. Both anchor data-attributes
    land for App2-driver targeting."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text

    # Modal container
    assert 'data-add-role-modal' in body
    assert 'data-role="role-cardinality-modal"' in body
    # 1:1 card
    assert 'href="/l2_shape/account/new"' in body
    assert 'data-cardinality-choice="one-to-one"' in body
    assert 'data-role="role-kind-1-1"' in body
    # 1:N card
    assert 'href="/l2_shape/account_template/new"' in body
    assert 'data-cardinality-choice="one-to-many"' in body
    assert 'data-role="role-kind-1-n"' in body


def test_add_role_modal_carries_inline_glossary_triggers(
    writable_l2_yaml: Path,
) -> None:
    """Locked OQ2(c) — modal-inline [?] triggers point at the
    matching per-cardinality glossary anchors."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    assert 'hx-get="/studio/side-panel/glossary/1-to-1"' in body
    assert 'hx-get="/studio/side-panel/glossary/1-to-n"' in body


def test_home_page_renders_add_role_button(
    writable_l2_yaml: Path,
) -> None:
    """The single ``+ Add Role`` button lands once in the Roles
    wrapper header (it opens the modal via the inline JS shim)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    assert 'data-add-role-open' in body
    assert 'data-role="add-role-button"' in body
    assert '+ Add Role' in body


# ---------------------------------------------------------------------------
# List page h1 rebrand
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind,want_h1", [
    ("account", "Roles — 1:1"),
    ("account_template", "Roles — 1:N"),
])
def test_list_page_h1_rebrands_for_role_cardinality(
    writable_l2_yaml: Path, kind: str, want_h1: str,
) -> None:
    """OQ4(a) lock — URLs unchanged; h1s rebrand."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/{kind}/").text
    assert f'data-list-h1="{kind}"' in body
    assert f">{want_h1}</h1>" in body


@pytest.mark.parametrize("kind,sibling_href", [
    ("account", "/l2_shape/account_template/"),
    ("account_template", "/l2_shape/account/"),
])
def test_list_page_links_to_sibling_cardinality_page(
    writable_l2_yaml: Path, kind: str, sibling_href: str,
) -> None:
    """Each per-cardinality page surfaces a cross-link to the
    sibling cardinality so operators can pivot 1:1 ↔ 1:N without
    bouncing through the home."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/{kind}/").text
    assert sibling_href in body


# ---------------------------------------------------------------------------
# Cardinality badge on read cards
# ---------------------------------------------------------------------------


def test_read_card_renders_1_1_badge_on_accounts(
    writable_l2_yaml: Path,
) -> None:
    """Account read cards carry the 1:1 badge + secondary-fg
    "Singleton account" subline."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/account/?embed=1").text
    assert 'data-cardinality-badge data-cardinality="one-to-one"' in body
    assert '>1:1<' in body
    assert 'data-role="card-cardinality-subline"' in body
    assert "Singleton account" in body


def test_read_card_renders_1_n_badge_on_account_templates(
    writable_l2_yaml: Path,
) -> None:
    """AccountTemplate read cards carry the 1:N badge + secondary-fg
    "Templated role" subline. With no DB wired the count suffix
    is empty (math notation alone)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/account_template/?embed=1").text
    assert 'data-cardinality-badge data-cardinality="one-to-many"' in body
    assert "1:N" in body
    assert 'data-role="card-cardinality-subline"' in body
    assert "Templated role" in body


def test_read_card_no_cardinality_badge_on_non_role_kinds(
    writable_l2_yaml: Path,
) -> None:
    """The badge is role-only — Rails / Chains / Transfer templates
    / Limit schedules don't carry it."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        for kind in (
            "rail", "chain", "transfer_template", "limit_schedule",
        ):
            body = c.get(f"/l2_shape/{kind}/?embed=1").text
            assert 'data-cardinality-badge' not in body, (
                f"{kind} read cards leak a cardinality badge"
            )


# ---------------------------------------------------------------------------
# instance_count_by_role helper
# ---------------------------------------------------------------------------


def test_instance_count_by_role_returns_none_on_missing_db(
    tmp_path: Path,
) -> None:
    """Helper falls back to None when no DB is reachable —
    caller renders the math notation alone (the no-DB shape per
    OQ3 lock)."""
    cfg = make_test_config()
    # Connection factory that always raises — simulates "no DB".
    def _factory() -> object:
        raise RuntimeError("no DB wired")

    result = instance_count_by_role(
        "CustomerDDA", cfg, connection_factory=_factory,
    )
    assert result is None


def test_instance_count_by_role_returns_count_from_seeded_fixture(
    tmp_path: Path,
) -> None:
    """Helper returns the COUNT(DISTINCT account_id) for the
    latest balance_date.

    Uses an in-process SQLite connection (DB-API 2.0 compliant
    with qmark paramstyle, matching the helper's primary attempt)
    to seed a fixture without spinning a DuckDB/PG container.
    """
    cfg = make_test_config()
    prefix = cfg.db_table_prefix

    conn = sqlite3.connect(":memory:")
    try:
        cur = conn.cursor()
        cur.execute(
            f"CREATE TABLE {prefix}_daily_balances ("
            "  account_id TEXT NOT NULL,"
            "  account_role TEXT NOT NULL,"
            "  balance_date TEXT NOT NULL"
            ")"
        )
        # 3 distinct CustomerDDA rows on the latest date,
        # 1 on an earlier date (should NOT count).
        # Plus 5 MerchantDDA rows on the latest date (cross-role
        # isolation check).
        cur.executemany(
            f"INSERT INTO {prefix}_daily_balances "
            "(account_id, account_role, balance_date) VALUES (?, ?, ?)",
            [
                ("cust-001", "CustomerDDA", "2030-01-01"),
                ("cust-002", "CustomerDDA", "2030-01-01"),
                ("cust-003", "CustomerDDA", "2030-01-01"),
                ("cust-004", "CustomerDDA", "2029-12-01"),
                ("merch-001", "MerchantDDA", "2030-01-01"),
                ("merch-002", "MerchantDDA", "2030-01-01"),
                ("merch-003", "MerchantDDA", "2030-01-01"),
                ("merch-004", "MerchantDDA", "2030-01-01"),
                ("merch-005", "MerchantDDA", "2030-01-01"),
            ],
        )
        conn.commit()
        # Reuse the same conn for every call — factory returns it,
        # then the helper closes it; for the second call we'd
        # need a fresh conn but only one assertion is made.
        result = instance_count_by_role(
            "CustomerDDA", cfg, connection_factory=lambda: conn,
        )
        assert result == 3
    finally:
        # `instance_count_by_role` closes the conn after use; the
        # explicit close here is defensive (idempotent in sqlite3).
        try:
            conn.close()
        except sqlite3.Error:
            pass


def test_instance_count_by_role_swallows_query_errors(
    tmp_path: Path,
) -> None:
    """Helper returns None when the query fails (e.g. the table
    doesn't exist yet)."""
    cfg = make_test_config()
    # Connection with no tables — query will raise.
    conn = sqlite3.connect(":memory:")
    result = instance_count_by_role(
        "CustomerDDA", cfg, connection_factory=lambda: conn,
    )
    assert result is None


# ---------------------------------------------------------------------------
# Glossary reachability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("term", [
    "roles-cardinality",
    "1-to-1",
    "1-to-n",
])
def test_new_glossary_entries_present_and_lookupable(term: str) -> None:
    """The three new BX.6/11 entries land in GLOSSARY + are
    reachable via the side-panel route shape every trigger uses."""
    assert term in GLOSSARY, f"missing glossary entry: {term}"
    body = GLOSSARY[term]
    assert body.strip()
    # Cross-reference: the lead bolds the math notation (per OQ5
    # lock — `1:1` / `1:N` leads on the cards + in prose).
    assert "**" in body, f"no bold in {term} body"


@pytest.mark.parametrize("term", [
    "roles-cardinality",
    "1-to-1",
    "1-to-n",
])
def test_glossary_term_route_returns_200_for_new_entries(
    writable_l2_yaml: Path, term: str,
) -> None:
    """Every new glossary anchor must be reachable via
    ``/studio/side-panel/glossary/<term>`` — same route the
    [?] triggers hx-get against. Sessionstart parity per BX.12."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get(f"/studio/side-panel/glossary/{term}")
    assert resp.status_code == 200, (
        f"glossary route 404'd for {term}"
    )


# ---------------------------------------------------------------------------
# Completeness rollup truth table
# ---------------------------------------------------------------------------


def _build_instance(
    *,
    accounts: int = 0,
    account_templates: int = 0,
) -> L2Instance:
    """Build a minimal L2Instance with the requested counts of
    Account / AccountTemplate. Other fields stay empty — they
    don't affect the role-completeness rollup."""
    acct_tuple = tuple(
        Account(
            id=Identifier(f"a{i}"),
            scope="internal",
            role=Identifier(f"Role{i}"),
            name=Name(f"Account {i}"),
            description=None,
            parent_role=None,
            expected_eod_balance=Money(0),
        )
        for i in range(accounts)
    )
    tpl_tuple = tuple(
        AccountTemplate(
            role=Identifier(f"Tpl{i}"),
            description=None,
            scope="internal",
            parent_role=None,
            expected_eod_balance=Money(0),
        )
        for i in range(account_templates)
    )
    return L2Instance(
        accounts=acct_tuple,
        account_templates=tpl_tuple,
        rails=(),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )


@pytest.mark.parametrize("accts,tpls,want", [
    (0, 0, "empty"),
    (1, 0, "partial"),
    (0, 1, "partial"),
    (1, 1, "set"),
    (5, 3, "set"),
])
def test_compute_role_completeness_rollup_truth_table(
    accts: int, tpls: int, want: str,
) -> None:
    """Truth table per the BX.6/11 doc §Recommendation — the
    Roles rollup is `set` iff BOTH children are set; `partial`
    if either is partial-or-better but they don't both clear
    "set"; `empty` if both are empty."""
    inst = _build_instance(accounts=accts, account_templates=tpls)
    out = compute_role_completeness(inst)
    assert out.roles == want


def test_compute_role_completeness_returns_typed_dataclass() -> None:
    """The helper returns a `RoleCompleteness` dataclass — typed
    rollup per memory feedback_invariants_in_types."""
    inst = _build_instance(accounts=1, account_templates=1)
    out = compute_role_completeness(inst)
    assert isinstance(out, RoleCompleteness)
    assert out.account == "set"
    assert out.account_template == "set"
    assert out.roles == "set"
