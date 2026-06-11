"""BX.10 — Composite-key opaque URL IDs (hash + slug + 301-redirect).

Direction E (operator lock per ``docs/audits/bx_0_8_design_mockups/bx_10.md``):
chain + limit_schedule URLs go from ``/l2_shape/chain/Foo::Bar,Baz`` to
``/l2_shape/chain/<hash6>-<slug>``. The hash is the authoritative
lookup key; the slug is a human-grep affordance. Stale slugs (after
a rename) 301-redirect to the canonical form so bookmarks self-heal
on next visit.

YAML is unchanged — only the URL surface goes opaque. The composite
key still drives ``_entity_id`` (used by HTML id generation, search-
by-name filtering, validation paths, the L2 mutate API).

Test coverage:
1. ``hash_composite_key`` — deterministic + collision-resistant for
   the cell's ~30-100 composite-key entity scale.
2. ``slugify_for_url`` — kebab-case folding for PascalCase / mixed
   case sources.
3. ``build_opaque_url_id`` / ``parse_opaque_url_id`` — round-trip
   shape + the hash6-only fallback when slug source is empty.
4. ``_url_entity_id`` — opaque shape for chain / limit_schedule;
   passthrough for single-key kinds (account / rail / etc.).
5. ``_resolve_url_entity_id`` — exact-match path, hash-match-with-
   stale-slug path (returns canonical url for the 301 redirect),
   not-found path, composite-form fallback (legacy URLs hit the
   canonical opaque form via redirect).
6. Route flow — GET /l2_shape/chain/<hash6>-<slug> returns 200;
   stale-slug version returns 301 to canonical; legacy composite-key
   URL returns 301 to canonical.
7. YAML round-trip unchanged — the opaque URL doesn't bleed into the
   saved YAML (operator lock from BX.0.7).
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.html._components import (
    build_opaque_url_id,
    hash_composite_key,
    parse_opaque_url_id,
    slugify_for_url,
)
from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html._studio_editor_routes import (
    _entity_id,
    _resolve_url_entity_id,
    _url_entity_id,
)
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.loader import load_instance
from tests._test_helpers import make_test_config


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    """Copy spec_example.yaml to a tempfile so write-side tests don't
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
# Hash + slug primitives — pure functions, no L2 dependency.
# ---------------------------------------------------------------------------


def test_hash_composite_key_is_deterministic() -> None:
    """Same input always produces same hash. The hash is the URL's
    addressing authority; non-determinism would break bookmarks the
    moment the process restarted (operator persistence concern)."""
    composite = "CustomerInboundACH::ChildA,ChildB"
    h1 = hash_composite_key(composite)
    h2 = hash_composite_key(composite)
    assert h1 == h2
    # 6 hex chars per the locked digest_size=3 blake2s bytes.
    assert len(h1) == 6
    assert all(c in "0123456789abcdef" for c in h1)


def test_hash_composite_key_distinct_inputs_produce_distinct_hashes() -> None:
    """At sasquatch's ~30-chain scale we're nowhere near birthday
    collision. Sanity-check that close-but-different inputs hash to
    different 6-char digests — catches a "constant hash" bug
    immediately."""
    h_a = hash_composite_key("Foo::Bar")
    h_b = hash_composite_key("Foo::Baz")  # one char different
    h_c = hash_composite_key("Bar::Foo")  # ordering swapped
    assert h_a != h_b
    assert h_a != h_c
    assert h_b != h_c


def test_hash_composite_key_no_collisions_across_spec_example_composites(
    writable_l2_yaml: Path,
) -> None:
    """The collision-detect contract: at the cell's ~100-entity scale
    the 6-char hash should never collide. If it does in spec_example,
    we'd need to widen the digest. Failure here is operator-visible —
    catch loud at test time rather than burn the operator's bookmark."""
    inst = load_instance(writable_l2_yaml)
    composites: list[str] = []
    for ch in inst.chains:
        children_csv = ",".join(sorted(str(c.name) for c in ch.children))
        composites.append(f"{ch.parent}::{children_csv}")
    for ls in inst.limit_schedules:
        composites.append(
            f"{ls.parent_role}::{ls.rail}::{ls.direction}"
        )
    hashes = [hash_composite_key(c) for c in composites]
    assert len(hashes) == len(set(hashes)), (
        f"hash collision in spec_example — composites: {composites}"
    )


def test_slugify_for_url_folds_pascalcase_and_special_chars() -> None:
    """PascalCase / camelCase / Title Case all flatten to ``-``-joined
    lowercase. Non-alphanumerics get dropped (folded to ``-``
    separator)."""
    assert slugify_for_url("CustomerInboundACH") == "customerinboundach"
    assert slugify_for_url("DDA Control") == "dda-control"
    assert slugify_for_url("gl-1010-cash-due-frb") == "gl-1010-cash-due-frb"
    assert slugify_for_url("snake_case_name") == "snake-case-name"
    assert slugify_for_url("") == ""
    # Repeated / leading / trailing dashes collapse cleanly.
    assert slugify_for_url("--foo--bar--") == "foo-bar"
    # Non-ASCII gets dropped (cell scope: ASCII identifiers per L2
    # invariant — non-ASCII would need a separate cell to design URL
    # handling for).
    assert slugify_for_url("café") == "caf"


def test_build_opaque_url_id_round_trip() -> None:
    """``build_opaque_url_id`` + ``parse_opaque_url_id`` form a
    lossless round-trip on the hash portion (the slug is cosmetic and
    not recovered by parse — only the hash is)."""
    composite = "CustomerInboundACH::ChildA,ChildB"
    slug_src = "CustomerInboundACH"
    url_id = build_opaque_url_id(composite, slug_src)
    # Shape: "<hash6>-<slug>".
    assert "-" in url_id
    hash_part, slug_part = url_id.split("-", 1)
    assert hash_part == hash_composite_key(composite)
    assert slug_part == "customerinboundach"
    # parse_opaque_url_id recovers both.
    parsed = parse_opaque_url_id(url_id)
    assert parsed == (hash_part, slug_part)


def test_build_opaque_url_id_empty_slug_source_falls_back_to_hash_only() -> None:
    """When slug source is empty, the URL is hash-only — still a
    valid opaque URL (parse_opaque_url_id accepts ``<hash6>`` with no
    suffix)."""
    composite = "Foo::Bar"
    url_id = build_opaque_url_id(composite, "")
    assert url_id == hash_composite_key(composite)
    parsed = parse_opaque_url_id(url_id)
    assert parsed is not None
    hash_part, slug_part = parsed
    assert hash_part == url_id
    assert slug_part == ""


def test_parse_opaque_url_id_rejects_non_opaque_shapes() -> None:
    """A composite-form URL (``Foo::Bar``) doesn't parse as opaque;
    the resolver uses ``parse_opaque_url_id is None`` as the signal
    to fall through to legacy lookup."""
    assert parse_opaque_url_id("Foo::Bar") is None
    assert parse_opaque_url_id("Foo::Bar,Baz") is None
    # A 5-char hex IS rejected (length is the lock — 6 chars exactly).
    assert parse_opaque_url_id("a3f2e") is None
    # A 7-char hex IS rejected too.
    assert parse_opaque_url_id("a3f2e1c") is None
    # Non-hex chars in the hash slot are rejected.
    assert parse_opaque_url_id("ZZZZZZ-foo") is None


# ---------------------------------------------------------------------------
# _url_entity_id + _entity_id — composite kinds go opaque, single-key
# kinds passthrough.
# ---------------------------------------------------------------------------


def test_url_entity_id_opaque_for_chain(writable_l2_yaml: Path) -> None:
    """Chain URL form is ``<hash6>-<parent-slug>`` — composite key NOT
    in the URL surface."""
    inst = load_instance(writable_l2_yaml)
    if not inst.chains:
        return
    chain = inst.chains[0]
    composite = _entity_id("chain", chain)
    url_id = _url_entity_id("chain", chain)
    # Composite key still drives `_entity_id` (operator lock — internal
    # addressing untouched).
    assert "::" in composite
    # URL form is opaque: NO ``::``, NO ``,`` in the URL.
    assert "::" not in url_id
    assert "," not in url_id
    # Shape: ``<hash6>-<parent-slug>``.
    parsed = parse_opaque_url_id(url_id)
    assert parsed is not None
    hash_part, slug_part = parsed
    assert hash_part == hash_composite_key(composite)
    # Slug derives from chain.parent.
    assert slug_part == slugify_for_url(str(chain.parent))


def test_url_entity_id_opaque_for_limit_schedule(writable_l2_yaml: Path) -> None:
    """LimitSchedule URL form is ``<hash6>-<parent_role-slug>``."""
    inst = load_instance(writable_l2_yaml)
    if not inst.limit_schedules:
        return
    ls = inst.limit_schedules[0]
    composite = _entity_id("limit_schedule", ls)
    url_id = _url_entity_id("limit_schedule", ls)
    assert "::" in composite
    assert "::" not in url_id
    parsed = parse_opaque_url_id(url_id)
    assert parsed is not None
    hash_part, slug_part = parsed
    assert hash_part == hash_composite_key(composite)
    assert slug_part == slugify_for_url(str(ls.parent_role))


def test_url_entity_id_passthrough_for_single_key_kinds(writable_l2_yaml: Path) -> None:
    """Account / account_template / rail / transfer_template URLs are
    unchanged — BX.10 scope is composite-keyed kinds only (open
    question #2: operator left consistency open for a follow-up
    cell)."""
    inst = load_instance(writable_l2_yaml)
    for acct in inst.accounts:
        assert _url_entity_id("account", acct) == _entity_id("account", acct)
    for tmpl in inst.account_templates:
        assert _url_entity_id("account_template", tmpl) == _entity_id(
            "account_template", tmpl,
        )
    for rail in inst.rails:
        assert _url_entity_id("rail", rail) == _entity_id("rail", rail)
    for tt in inst.transfer_templates:
        assert _url_entity_id("transfer_template", tt) == _entity_id(
            "transfer_template", tt,
        )


# ---------------------------------------------------------------------------
# _resolve_url_entity_id — the hash-based reverse lookup.
# ---------------------------------------------------------------------------


def test_resolve_url_entity_id_exact_match_returns_entity(
    writable_l2_yaml: Path,
) -> None:
    """The canonical opaque URL form returns ``(entity, None)`` — no
    redirect needed."""
    inst = load_instance(writable_l2_yaml)
    if not inst.chains:
        return
    chain = inst.chains[0]
    url_id = _url_entity_id("chain", chain)
    entity, canonical_url = _resolve_url_entity_id(inst, "chain", url_id)
    assert entity is chain
    assert canonical_url is None


def test_resolve_url_entity_id_stale_slug_returns_redirect(
    writable_l2_yaml: Path,
) -> None:
    """A URL with the right hash but wrong slug resolves to the
    entity AND signals the canonical URL for the 301 redirect (the
    Slack-paste self-healing path)."""
    inst = load_instance(writable_l2_yaml)
    if not inst.chains:
        return
    chain = inst.chains[0]
    canonical = _url_entity_id("chain", chain)
    # Same hash, deliberately stale slug.
    parsed = parse_opaque_url_id(canonical)
    assert parsed is not None
    h, _slug = parsed
    stale_url = f"{h}-old-stale-slug-name"
    entity, canonical_url = _resolve_url_entity_id(inst, "chain", stale_url)
    assert entity is chain
    assert canonical_url == canonical


def test_resolve_url_entity_id_composite_form_returns_redirect(
    writable_l2_yaml: Path,
) -> None:
    """A legacy composite-form URL (pre-BX.10 bookmark) resolves to
    the entity AND signals the canonical opaque URL for the 301
    redirect. Old bookmarks self-heal on next visit."""
    inst = load_instance(writable_l2_yaml)
    if not inst.chains:
        return
    chain = inst.chains[0]
    composite = _entity_id("chain", chain)
    canonical = _url_entity_id("chain", chain)
    entity, canonical_url = _resolve_url_entity_id(inst, "chain", composite)
    assert entity is chain
    assert canonical_url == canonical


def test_resolve_url_entity_id_not_found(
    writable_l2_yaml: Path,
) -> None:
    """A URL whose hash doesn't match anything returns
    ``(None, None)`` — caller 404s."""
    inst = load_instance(writable_l2_yaml)
    entity, canonical_url = _resolve_url_entity_id(
        inst, "chain", "ffffff-does-not-exist",
    )
    assert entity is None
    assert canonical_url is None


def test_resolve_url_entity_id_passthrough_for_single_key_kinds(
    writable_l2_yaml: Path,
) -> None:
    """Single-key kinds bypass the hash-based path — URL IS the
    addressing key verbatim (BX.10 scope is composite-keyed kinds)."""
    inst = load_instance(writable_l2_yaml)
    if not inst.accounts:
        return
    acct = inst.accounts[0]
    entity, canonical_url = _resolve_url_entity_id(
        inst, "account", str(acct.id),
    )
    assert entity is acct
    assert canonical_url is None


# ---------------------------------------------------------------------------
# Route-level flow — GET / 301-redirect end-to-end.
# ---------------------------------------------------------------------------


def test_get_chain_via_opaque_url_returns_200(writable_l2_yaml: Path) -> None:
    """The canonical opaque URL is the destination — direct GET
    returns 200 with the read card."""
    inst = load_instance(writable_l2_yaml)
    if not inst.chains:
        return
    chain = inst.chains[0]
    url_id = _url_entity_id("chain", chain)
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get(f"/l2_shape/chain/{url_id}")
    assert resp.status_code == 200
    # The read card renders the parent name in the title (CG.12 — chain
    # cards render parent only in the h3).
    assert str(chain.parent) in resp.text


def test_get_chain_with_stale_slug_redirects_301(writable_l2_yaml: Path) -> None:
    """Slack-paste self-healing: an old URL with the right hash but
    stale slug (entity renamed) 301-redirects to the canonical URL.
    The pattern matches GitHub-issues URLs (``/issues/42-old-title``
    redirects to ``/issues/42-new-title``)."""
    inst = load_instance(writable_l2_yaml)
    if not inst.chains:
        return
    chain = inst.chains[0]
    canonical = _url_entity_id("chain", chain)
    parsed = parse_opaque_url_id(canonical)
    assert parsed is not None
    h, _slug = parsed
    stale_url = f"{h}-stale-old-name"
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get(f"/l2_shape/chain/{stale_url}", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == f"/l2_shape/chain/{canonical}"


def test_get_chain_via_legacy_composite_url_redirects_301(
    writable_l2_yaml: Path,
) -> None:
    """Pre-BX.10 bookmarks (raw ``Foo::Bar,Baz`` composite key in the
    URL) 301-redirect to the canonical opaque URL. The operator's
    saved bookmark self-heals on next click — no broken-link banner,
    no dead-letter page."""
    inst = load_instance(writable_l2_yaml)
    if not inst.chains:
        return
    chain = inst.chains[0]
    composite = _entity_id("chain", chain)
    canonical = _url_entity_id("chain", chain)
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get(f"/l2_shape/chain/{composite}", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == f"/l2_shape/chain/{canonical}"


def test_get_chain_edit_via_stale_slug_redirects_301(
    writable_l2_yaml: Path,
) -> None:
    """Same 301 contract on the edit URL — a stale-slug bookmark on
    ``/edit`` redirects to the canonical ``/edit`` URL (not just the
    bare read card)."""
    inst = load_instance(writable_l2_yaml)
    if not inst.chains:
        return
    chain = inst.chains[0]
    canonical = _url_entity_id("chain", chain)
    parsed = parse_opaque_url_id(canonical)
    assert parsed is not None
    h, _slug = parsed
    stale_url = f"{h}-stale-old-name"
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get(
            f"/l2_shape/chain/{stale_url}/edit", follow_redirects=False,
        )
    assert resp.status_code == 301
    assert resp.headers["location"] == f"/l2_shape/chain/{canonical}/edit"


def test_get_limit_schedule_via_opaque_url_returns_200(
    writable_l2_yaml: Path,
) -> None:
    """Same opaque-URL contract for limit_schedule (the second
    composite-keyed kind)."""
    inst = load_instance(writable_l2_yaml)
    if not inst.limit_schedules:
        return
    ls = inst.limit_schedules[0]
    url_id = _url_entity_id("limit_schedule", ls)
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get(f"/l2_shape/limit_schedule/{url_id}")
    assert resp.status_code == 200


def test_get_nonexistent_opaque_url_returns_404(
    writable_l2_yaml: Path,
) -> None:
    """A URL with a hash that doesn't match any entity 404s — the
    operator gets a clear "this entity doesn't exist" signal rather
    than a fuzzy match or a stale-bookmark heuristic (operator lock
    on Q3: no fuzzy)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get(
            "/l2_shape/chain/ffffff-no-such-chain", follow_redirects=False,
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# YAML round-trip — opaque URL must NOT bleed into the saved YAML.
# ---------------------------------------------------------------------------


def test_yaml_round_trip_does_not_leak_hash_or_slug(
    writable_l2_yaml: Path,
) -> None:
    """Operator lock from BX.0.7: ``URL only, YAML untouched``. After
    loading the YAML, the in-memory chain composite key is the same
    parent + children pair — no hash, no slug, no opaque-URL artifact.
    Verifies the BX.10 contract is URL-side only."""
    pre = load_instance(writable_l2_yaml)
    # Read raw YAML text and confirm no hash leakage.
    yaml_text = writable_l2_yaml.read_text()
    if pre.chains:
        chain = pre.chains[0]
        composite = _entity_id("chain", chain)
        url_id = _url_entity_id("chain", chain)
        # Pull just the hash6 portion.
        parsed = parse_opaque_url_id(url_id)
        assert parsed is not None
        hash6 = parsed[0]
        # Composite key components ARE in the YAML (parent name + child
        # names appear as YAML scalars).
        assert str(chain.parent) in yaml_text
        # The hash + opaque URL ID must NOT appear in the YAML.
        assert hash6 not in yaml_text
        assert url_id not in yaml_text
        # And the composite-key string with the ``::`` separator must
        # NOT appear (it's a URL convention, not a YAML convention).
        assert composite not in yaml_text


def test_yaml_unchanged_after_chain_save_via_opaque_url(
    writable_l2_yaml: Path,
) -> None:
    """End-to-end: editing a chain via its opaque URL doesn't change
    the YAML shape — the saved YAML still addresses chains by parent +
    children (not by hash). The in-memory composite-key contract for
    `mutate_l2` remains unchanged.
    """
    inst = load_instance(writable_l2_yaml)
    if not inst.chains:
        return
    chain = inst.chains[0]
    url_id = _url_entity_id("chain", chain)
    composite = _entity_id("chain", chain)
    app = _build_app(writable_l2_yaml)
    # Round-trip the chain's existing fields back through the save
    # endpoint (this exercises the resolve → mutate path without
    # changing any field values).
    children = [str(c.name) for c in chain.children]
    data: dict[str, str | list[str]] = {
        "parent": str(chain.parent),
        "children__present": "1",
        "children": children,
        "description": str(chain.description or ""),
    }
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.post(
            f"/l2_shape/chain/{url_id}", data=data, follow_redirects=False,
        )
    assert resp.status_code == 303, resp.text
    # The save-success redirect target is the canonical opaque URL.
    assert resp.headers["location"] == f"/l2_shape/chain/{url_id}"

    # Reload + assert composite addressing key unchanged.
    reloaded = load_instance(writable_l2_yaml)
    reloaded_chain = next(
        ch for ch in reloaded.chains if _entity_id("chain", ch) == composite
    )
    assert _entity_id("chain", reloaded_chain) == composite
    # Saved YAML still has no opaque-URL artifacts.
    yaml_text = writable_l2_yaml.read_text()
    parsed = parse_opaque_url_id(url_id)
    assert parsed is not None
    assert parsed[0] not in yaml_text  # hash6 absent
    assert composite not in yaml_text  # `::` separator absent
