"""CG-followup.1 + .2 — edit-page h1 + CG.21 title detail no longer
leak composite keys; account h1 uses middle-dot + display-name span.

Cold-read v5 P1:
- chain edit h1 leaked `MerchantSettlementCycle::PayoutACH,...` —
  100+ char browser tab via CG.21's title detail inheritance.
- limit_schedule edit h1 leaked `Role::Rail::Direction`.
- account edit h1 mixed em-dash + colon ambiguously.

This cell:
- `_edit_h1_parts(kind, entity, entity_id, title_suffix)` computes
  both the visible h1 HTML and the matching `<title>` detail.
- chain → "Edit chain · {parent}" (composite dropped).
- limit_schedule → "Edit limit schedule · {role} → {rail} ({direction})".
- account → "Edit account · {kebab}" + `data-role="edit-h1-display-name"`
  span carrying `Account.name`.
- account_template / rail / transfer_template → entity_id stays
  (already operator-readable).
"""

from __future__ import annotations

import re
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


REPO_ROOT = Path(__file__).parent.parent.parent
FIXTURES = REPO_ROOT / "tests" / "l2"


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


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    src = FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


_TITLE_RE = re.compile(r"<title>([^<]*)</title>")


def _title_of(body: str) -> str:
    match = _TITLE_RE.search(body)
    return match.group(1) if match else ""


def _h1_block(body: str) -> str:
    """Slice the form-page header strip's <h1>...</h1> block."""
    h1_start = body.index("<h1")
    h1_close = body.index("</h1>", h1_start)
    return body[h1_start:h1_close + len("</h1>")]


# ---------------------------------------------------------------------------
# Chain — composite gone from h1 + title
# ---------------------------------------------------------------------------

def test_chain_edit_h1_drops_composite(writable_l2_yaml: Path) -> None:
    """Chain edit h1 reads `Edit chain · {parent}` only — no `::`,
    no comma-separated child list. The list-card title (CG.12) and
    the edit-page h1 now agree on the same display key."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    # Pick a multi-child chain so the composite would be wide.
    chain = next(c for c in inst.chains if len(c.children) > 1)
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _entity_id,
    )
    composite = _entity_id("chain", chain)
    assert "::" in composite, "fixture chain should have composite id"
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/chain/{composite}/edit").text
    h1 = _h1_block(body)
    assert f"Edit chain · {chain.parent}" in h1
    assert "::" not in h1
    assert "," not in h1.split("</h1>")[0]


def test_chain_edit_title_drops_composite(writable_l2_yaml: Path) -> None:
    """The CG.21 <title> detail slot inherits the same display key
    so a stale-tab browser bar doesn't render the 100+ char
    composite."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    chain = next(c for c in inst.chains if len(c.children) > 1)
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _entity_id,
    )
    composite = _entity_id("chain", chain)
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        title = _title_of(c.get(f"/l2_shape/chain/{composite}/edit").text)
    assert (
        f"Recon-Gen · Studio · Editor · Edit chain · {chain.parent}" == title
    )
    assert "::" not in title


# ---------------------------------------------------------------------------
# Limit schedule — composite gone, role-arrow-rail shape
# ---------------------------------------------------------------------------

def test_limit_schedule_edit_h1_uses_role_arrow_rail(
    writable_l2_yaml: Path,
) -> None:
    """Limit schedule edit h1 reads `Edit limit schedule · {role}
    → {rail} ({direction})` — same display key as the list card
    title (CG.18) plus the direction parenthesized for clarity."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    ls = inst.limit_schedules[0]
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _entity_id,
    )
    composite = _entity_id("limit_schedule", ls)
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/limit_schedule/{composite}/edit").text
    h1 = _h1_block(body)
    assert (
        f"Edit limit schedule · {ls.parent_role} → {ls.rail} "
        f"({ls.direction})"
    ) in h1
    assert "::" not in h1


# ---------------------------------------------------------------------------
# Account — middle-dot + display-name span
# ---------------------------------------------------------------------------

def test_account_edit_h1_uses_middle_dot_and_display_name_span(
    writable_l2_yaml: Path,
) -> None:
    """Account edit h1 reads `Edit account · {kebab}` plus a
    secondary-fg `<span data-role="edit-h1-display-name">{name}</span>`
    after the kebab — matches CG.11's list-card-title idiom + drops
    the em-dash/colon ambiguity flagged in cold-read v5 P1."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    account = next(a for a in inst.accounts if a.name)
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/account/{account.id}/edit").text
    h1 = _h1_block(body)
    assert f"Edit account · {account.id}" in h1
    assert 'data-role="edit-h1-display-name"' in h1
    assert str(account.name) in h1
    # Em-dash ambiguity gone.
    assert " — " not in h1.split("data-role=")[0]


def test_account_edit_h1_no_display_name_means_no_span(
    writable_l2_yaml: Path,
) -> None:
    """Account with no `name` field renders the kebab alone, no
    span. We can't directly construct a nameless account via the
    HTTP surface, but we can assert the conditional via the helper
    directly."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _edit_h1_parts,
    )
    import dataclasses
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    base = next(a for a in inst.accounts if a.name)
    nameless = dataclasses.replace(base, name=None)
    h1_inner, _ = _edit_h1_parts("account", nameless, str(nameless.id), "")
    assert f"Edit account · {nameless.id}" in h1_inner
    assert 'data-role="edit-h1-display-name"' not in h1_inner


# ---------------------------------------------------------------------------
# Other kinds — entity_id stays (already operator-readable)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["rail", "account_template", "transfer_template"])
def test_other_kinds_edit_h1_keeps_entity_id(
    writable_l2_yaml: Path, kind: str,
) -> None:
    """For rail / account_template / transfer_template, entity_id
    is already the operator-readable key (role / name). The h1 reads
    `Edit {singular} · {entity_id}`."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _entities_for_kind, _entity_id, kind_label_singular,
    )
    entities = _entities_for_kind(inst, kind)  # type: ignore[arg-type]: parametrize fixture passes valid kinds
    eid = _entity_id(kind, entities[0])  # type: ignore[arg-type]: parametrize fixture passes valid kinds
    singular = kind_label_singular(kind)  # type: ignore[arg-type]: parametrize fixture passes valid kinds
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/{kind}/{eid}/edit").text
    h1 = _h1_block(body)
    assert f"Edit {singular} · {eid}" in h1


# ---------------------------------------------------------------------------
# CG-followup.3 — Account Reference prose includes scope
# ---------------------------------------------------------------------------

def test_account_reference_prose_lists_scope_as_required(
    writable_l2_yaml: Path,
) -> None:
    """The account create form's intro reference says
    "Required: `id` and `scope`" — matching the form's red-* markers
    on both fields (validator-enforced via the dataclass: both lack
    defaults). Pre-followup the prose said only `id`."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/account/new").text
    # Look for both `id` and `scope` flagged as Required.
    # The prose is "Required: <code>id</code> ... and <code>scope</code>"
    # (the exact phrasing may include intervening text — assert the
    # presence of the canonical fragments).
    assert "Required: <code>id</code>" in body
    assert "<code>scope</code>" in body
    # The previous text said only `id` was required; the new prose
    # must mention scope BEFORE the "Strongly recommended" stanza
    # (i.e. in the Required slot, not as a recommendation).
    body_prose = body.split("Strongly recommended")[0]
    assert "<code>scope</code>" in body_prose
