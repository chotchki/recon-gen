"""AI.2.d — Studio L2 editor driver for the dogfood round-trip.

One verb protocol (``StudioEditorDriver``) recreates an ``L2Instance``
through the Studio editor's HTTP surface, in dependency order, so the
AI dogfood can assert the editor faithfully round-trips any L2 yaml.

Two transports behind the protocol (Lock 3 amendment, 2026-05-21):

- ``StudioHttpEditorDriver`` (this file) — drives a running studio
  server via form-POSTs. Fast + deterministic + fuzz-scalable; the
  workhorse for ``sasquatch_pr`` + the fuzz-sampled bulk.
- ``StudioBrowserEditorDriver`` (AI.2.d.2) — Playwright form-fill for
  ONE full ``spec_example`` pass (real render+submit fidelity).

**Form-data encoders walk the SRC FieldSpec lists** (``_FIELD_SPECS_BY_KIND``)
rather than hand-listing each kind's fields, so the driver auto-tracks
new editor fields — a new FieldSpec is exercised by the dogfood the
moment a corpus/fuzz L2 uses it (the "future entity-kind additions gain
dogfood coverage automatically" goal from the Phase AI rationale).

The client is duck-typed (``post`` / ``put`` returning ``.status_code``
+ ``.text``): Starlette ``TestClient`` for in-process unit coverage,
``httpx.Client(base_url=...)`` against a real uvicorn studio server for
the browser-adjacent dogfood run.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Protocol, runtime_checkable

import yaml

from recon_gen.common.html._studio_editor_routes import (
    _FIELD_SPECS_BY_KIND,
    _entity_id,
    _rail_subtype_of,
    _value_to_input_str,
)
from recon_gen.common.l2.editor import EntityKind
from recon_gen.common.l2.primitives import (
    Account,
    AccountTemplate,
    Chain,
    L2Instance,
    LimitSchedule,
    Rail,
    TransferTemplate,
)

# Form-data values are always lists so a single client.post(data=...)
# encodes multi_select repeats + scalar singletons uniformly (httpx /
# TestClient repeat a list value as duplicate keys).
FormData = dict[str, list[str]]


class StudioEditorError(RuntimeError):
    """A create / edit / save through the editor returned a non-redirect.

    Carries the kind + entity id + status + response body so the dogfood
    surfaces the FIRST broken editor verb + entity (per AI.3's "fail
    loudly on the first missing widget" contract) instead of a vague
    "rebuild diverged" downstream.
    """

    def __init__(
        self, kind: str, entity_id: str, status: int, body: str,
    ) -> None:
        self.kind = kind
        self.entity_id = entity_id
        self.status = status
        self.body = body
        # Trim the body — the inline form-global-error is what matters.
        snippet = body
        marker = 'class="form-global-error">'
        if marker in body:
            start = body.index(marker) + len(marker)
            snippet = body[start:start + 300].split("<")[0].strip()
        super().__init__(
            f"editor {kind} {entity_id!r}: HTTP {status} (expected 303 "
            f"redirect). Error: {snippet}",
        )


# ---------------------------------------------------------------------------
# Form-data encoders (transport-agnostic, pure)
# ---------------------------------------------------------------------------


def _encode_spec(spec: object, value: object, data: FormData) -> None:
    """Encode one FieldSpec's value into ``data`` per its ``kind``.

    multi_select → repeated key + a ``<name>__present`` marker (so the
    coerce treats an empty selection as "set to empty", not "absent").
    Scalar kinds (text / select / money / textarea / yaml_block) reuse
    the editor's own ``_value_to_input_str`` so the round-trip is exact
    (e.g. metadata_keys tuple → comma-join → split-back).
    """
    name = getattr(spec, "name")
    kind = getattr(spec, "kind")
    if kind == "multi_select":
        items = [str(v) for v in (value or ())]  # type: ignore[union-attr]: multi_select field values are always iterable tuples; `or ()` guards None
        data[f"{name}__present"] = ["1"]
        if items:
            data[name] = items
    elif kind in ("text", "select", "money", "textarea", "yaml_block"):
        rendered = _value_to_input_str(value)
        if rendered:
            data[name] = [rendered]
    # multi_select_groups (edit_only) + chain_children are handled by the
    # callers below — they don't come through the generic scalar walk.


def create_form_data(kind: EntityKind, entity: object) -> FormData:
    """Build the POST body that recreates ``entity`` via the create form.

    Walks ``_FIELD_SPECS_BY_KIND[kind]``: skips ``edit_only`` fields
    (e.g. ``leg_rail_xor_groups`` — authored via a follow-up edit) and
    subtype-mismatched rail fields. Chains get their per-child
    ``chain_children`` encoding appended (fan_in + expected_parent_count
    sub-inputs keyed by child name).
    """
    data: FormData = {}
    subtype = _rail_subtype_of(entity) if kind == "rail" else None
    if subtype is not None:
        data["subtype"] = [subtype]
    for spec in _FIELD_SPECS_BY_KIND[kind]:
        if getattr(spec, "edit_only", False):
            continue
        spec_subtype = getattr(spec, "subtype_only", None)
        if spec_subtype is not None and spec_subtype != subtype:
            continue
        if getattr(spec, "kind") == "chain_children":
            continue  # appended below for chains
        value = getattr(entity, getattr(spec, "name"), None)
        _encode_spec(spec, value, data)
    if kind == "chain":
        _append_chain_children(entity, data)
    return data


def _append_chain_children(chain: object, data: FormData) -> None:
    """Encode ``Chain.children`` as the chain_children widget submits it:
    a ``children`` repeat per child + per-child ``fan_in_<name>`` /
    ``epc_<name>`` sub-inputs (only when set), plus the present marker.
    """
    children = getattr(chain, "children", ())
    data["children__present"] = ["1"]
    data["children"] = [str(c.name) for c in children]
    for c in children:
        if getattr(c, "fan_in", False):
            data[f"fan_in_{c.name}"] = ["true"]
        epc = getattr(c, "expected_parent_count", None)
        if epc is not None:
            data[f"epc_{c.name}"] = [str(epc)]


def edit_xor_groups_form_data(template: TransferTemplate) -> FormData:
    """Build the edit-PUT body that adds ``leg_rail_xor_groups`` to an
    already-created template (the field is ``edit_only`` — operator
    authors ``leg_rails`` on create, then edits to add the XOR layer).

    Only the multi_select_groups keys are sent; leg_rails is omitted so
    ``mutate_l2`` leaves it untouched (dataclasses.replace of provided
    fields only).
    """
    groups = template.leg_rail_xor_groups
    data: FormData = {
        "leg_rail_xor_groups__present": ["1"],
        "leg_rail_xor_groups__num_groups": [str(len(groups))],
    }
    for i, group in enumerate(groups):
        data[f"leg_rail_xor_groups_{i}"] = [str(r) for r in group]
    return data


def instance_yaml_text(
    description: str | None,
    role_business_day_offsets: dict[str, int] | None,
) -> str:
    """Dump the top-level instance-settings singleton block (description
    + role_business_day_offsets). Mirrors the editor's own
    ``_singleton_yaml_text`` shape so it round-trips through
    ``singleton_save_l2``'s instance branch.
    """
    block: dict[str, object] = {}
    if description is not None:
        block["description"] = description
    if role_business_day_offsets:
        block["role_business_day_offsets"] = dict(role_business_day_offsets)
    if not block:
        return ""
    return yaml.safe_dump(block, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# Driver protocol + dependency-ordered bulk rebuild
# ---------------------------------------------------------------------------


@runtime_checkable
class StudioEditorDriver(Protocol):
    """Test vocabulary for recreating an L2 through the Studio editor.

    Per-kind create verbs + the two top-level settings + a bulk
    ``create_l2`` walk + ``save_l2_to_path``. Implemented by
    ``StudioHttpEditorDriver`` (HTTP) and ``StudioBrowserEditorDriver``
    (Playwright, AI.2.d.2).
    """

    def create_account(self, account: Account) -> None: ...
    def create_account_template(self, template: AccountTemplate) -> None: ...
    def create_rail(self, rail: Rail) -> None: ...
    def create_transfer_template(
        self, template: TransferTemplate,
    ) -> None: ...
    def create_chain(self, chain: Chain) -> None: ...
    def create_limit_schedule(self, schedule: LimitSchedule) -> None: ...
    def set_instance_settings(
        self,
        *,
        description: str | None,
        role_business_day_offsets: dict[str, int] | None,
    ) -> None: ...
    def create_l2(self, reference: L2Instance) -> None: ...
    def save_l2_to_path(self, path: Path) -> Path: ...


class _BaseStudioEditorDriver:
    """Shared ``create_l2`` walk over the per-kind verbs in dependency
    order. Concrete transports override the verbs; the base declares them
    so ``create_l2`` resolves statically (and a half-built transport
    fails loudly with NotImplementedError rather than AttributeError).
    """

    def create_account(self, account: Account) -> None:
        raise NotImplementedError

    def create_account_template(self, template: AccountTemplate) -> None:
        raise NotImplementedError

    def create_rail(self, rail: Rail) -> None:
        raise NotImplementedError

    def create_transfer_template(self, template: TransferTemplate) -> None:
        raise NotImplementedError

    def create_chain(self, chain: Chain) -> None:
        raise NotImplementedError

    def create_limit_schedule(self, schedule: LimitSchedule) -> None:
        raise NotImplementedError

    def set_instance_settings(
        self,
        *,
        description: str | None,
        role_business_day_offsets: dict[str, int] | None,
    ) -> None:
        raise NotImplementedError

    def create_l2(self, reference: L2Instance) -> None:
        """Recreate every entity in **reference-resolution dependency
        order** so each editor save's full ``validate()`` pass succeeds
        without seeing an undeclared reference (AI.2.d.1.a — the
        per-save validator was the blocker that originally surfaced as
        ``AccountTemplate.parent_role='X': role is not declared on any
        Account``).

        Order:

          1. **Accounts (parent role-holders first)** — Accounts with no
             ``parent_role`` are roots; child Accounts depend on a
             parent's ``role`` being declared first. A topological pass
             on ``parent_role -> role`` puts roots before children.
          2. **AccountTemplates** — their ``parent_role`` must resolve
             to an existing Account.role (the validator's reject case).
          3. **Rails** — split into two waves to honor the circular
             ``bundles_activity ↔ max_unbundled_age`` constraint:
             (3a) non-aggregating rails first, with ``max_unbundled_age``
             **deferred** (operator workflow: create the rail without
             the field that requires a not-yet-existing bundler);
             (3b) aggregating rails (their ``bundles_activity`` now
             resolves to declared rails);
             (3c) **edit-in** the deferred ``max_unbundled_age`` on
             each rail that had it — the validator now sees that some
             aggregating rail bundles it.
          4. **TransferTemplates** — their ``transfer_key`` resolves
             against leg rails' ``metadata_keys``.
          5. **Chains** — parent + children resolve against rails +
             templates.
          6. **LimitSchedules** — caps resolve against rails +
             parent_roles.
          7. **Top-level instance settings** — description +
             role_business_day_offsets (no references).

        This is the **dogfood operator workflow**: every save validates;
        no cheating with a defer-validation flag.
        """
        for account in _topo_accounts_by_parent(reference.accounts):
            self.create_account(account)
        for template in reference.account_templates:
            self.create_account_template(template)

        # Rails: 2-wave + edit-in for the bundles_activity circular pair.
        non_aggregating = [r for r in reference.rails if not r.aggregating]
        aggregating = [r for r in reference.rails if r.aggregating]
        deferred_max_unbundled_age: list[Rail] = []
        for rail in non_aggregating:
            if getattr(rail, "max_unbundled_age", None) is not None:
                # Create without the deferred field — the validator on
                # this save can't see any bundling rail yet (they come
                # next). Edit-in after the aggregators land.
                stripped = dataclasses.replace(rail, max_unbundled_age=None)
                self.create_rail(stripped)
                deferred_max_unbundled_age.append(rail)
            else:
                self.create_rail(rail)
        for rail in aggregating:
            self.create_rail(rail)
        for rail in deferred_max_unbundled_age:
            self._edit(
                "rail", str(rail.name),
                _max_unbundled_age_edit_form_data(rail),
            )

        for template in reference.transfer_templates:
            self.create_transfer_template(template)
        for chain in reference.chains:
            self.create_chain(chain)
        for schedule in reference.limit_schedules:
            self.create_limit_schedule(schedule)
        if (
            reference.description is not None
            or reference.role_business_day_offsets
        ):
            self.set_instance_settings(
                description=reference.description,
                role_business_day_offsets=reference.role_business_day_offsets,
            )


def _max_unbundled_age_edit_form_data(rail: Rail) -> FormData:
    """Build the edit PUT form payload that adds ``max_unbundled_age``
    to an already-created rail.

    The scalar field's value is the ISO 8601 duration literal —
    ``_value_to_input_str`` handles the `timedelta → "P1D"` /
    ``"PT24H"`` formatting that AI.2.d.1.a wired in. Only this one
    field is sent so the editor's PUT mutate leaves every other field
    untouched (dataclasses.replace semantics).
    """
    value_str = _value_to_input_str(rail.max_unbundled_age)
    return {"max_unbundled_age": [value_str]}


def _topo_accounts_by_parent(
    accounts: "tuple[Account, ...]",
) -> "list[Account]":
    """Order Accounts so a child's ``parent_role`` is declared on some
    earlier Account's ``role``. Roots (no parent_role) come first; then
    children grouped by depth from the role-DAG. Stable within a depth
    layer (input order preserved) so YAML authoring order survives when
    it's already topological — only re-orderings happen when needed.
    """
    by_role: dict[str, "Account"] = {}
    for a in accounts:
        if a.role:
            by_role.setdefault(a.role, a)

    ordered: list["Account"] = []
    placed: set[str] = set()

    def _depth(role: str | None, seen: frozenset[str]) -> int:
        """Reference-graph depth. Cycle / unresolved parent → 0 (root)
        so the validator can surface the real error rather than a
        cryptic ordering one."""
        if not role:
            return 0
        a = by_role.get(role)
        if a is None or a.parent_role is None or a.parent_role in seen:
            return 0
        return 1 + _depth(a.parent_role, seen | {role})

    indexed = [(i, a, _depth(a.role, frozenset())) for i, a in enumerate(accounts)]
    indexed.sort(key=lambda t: (t[2], t[0]))  # (depth asc, input idx asc)
    for _i, a, _d in indexed:
        ordered.append(a)
        if a.role:
            placed.add(a.role)
    return ordered


@runtime_checkable
class _HttpClient(Protocol):
    """Minimal client surface shared by Starlette ``TestClient`` (in-
    process) and ``httpx.Client`` (real server)."""

    def post(self, url: str, *, data: object, follow_redirects: bool): ...  # noqa: ANN201
    def put(self, url: str, *, data: object, follow_redirects: bool): ...  # noqa: ANN201


class StudioHttpEditorDriver(_BaseStudioEditorDriver):
    """Drive the Studio editor over HTTP form-POSTs.

    ``client`` is any object with ``post`` / ``put`` (TestClient or
    httpx.Client). ``l2_path`` is the server's bound ``--l2`` path —
    save-on-mutate flushes every successful create/edit there, so
    ``save_l2_to_path`` is a copy-if-needed confirmation rather than a
    distinct serialize step (per the AI.0 "save-on-mutate is the save
    mechanism" lock).
    """

    def __init__(self, client: _HttpClient, l2_path: str | Path) -> None:
        self._client = client
        self._l2_path = Path(l2_path)

    # -- transport primitives -------------------------------------------

    def _create(self, kind: EntityKind, entity: object) -> None:
        data = create_form_data(kind, entity)
        resp = self._client.post(
            f"/l2_shape/{kind}/", data=data, follow_redirects=False,
        )
        if resp.status_code != 303:
            raise StudioEditorError(
                kind, _entity_id(kind, entity), resp.status_code, resp.text,
            )

    def _edit(
        self, kind: EntityKind, entity_id: str, data: FormData,
    ) -> None:
        resp = self._client.put(
            f"/l2_shape/{kind}/{entity_id}", data=data,
            follow_redirects=False,
        )
        if resp.status_code != 303:
            raise StudioEditorError(
                kind, entity_id, resp.status_code, resp.text,
            )

    # -- per-kind verbs --------------------------------------------------

    def create_account(self, account: Account) -> None:
        self._create("account", account)

    def create_account_template(self, template: AccountTemplate) -> None:
        self._create("account_template", template)

    def create_rail(self, rail: Rail) -> None:
        self._create("rail", rail)

    def create_transfer_template(self, template: TransferTemplate) -> None:
        self._create("transfer_template", template)
        # leg_rail_xor_groups is edit_only — author it via a follow-up
        # edit once the template (and its leg_rails) exist.
        if template.leg_rail_xor_groups:
            self._edit(
                "transfer_template", str(template.name),
                edit_xor_groups_form_data(template),
            )

    def create_chain(self, chain: Chain) -> None:
        self._create("chain", chain)

    def create_limit_schedule(self, schedule: LimitSchedule) -> None:
        self._create("limit_schedule", schedule)

    def set_instance_settings(
        self,
        *,
        description: str | None,
        role_business_day_offsets: dict[str, int] | None,
    ) -> None:
        yaml_text = instance_yaml_text(description, role_business_day_offsets)
        resp = self._client.post(
            "/l2_shape/instance/",
            data={"yaml": [yaml_text]},
            follow_redirects=False,
        )
        if resp.status_code != 303:
            raise StudioEditorError(
                "instance", "(settings)", resp.status_code, resp.text,
            )

    def save_l2_to_path(self, path: Path) -> Path:
        """Confirm the rebuilt L2 is on disk at ``path``.

        Save-on-mutate already wrote the bound ``l2_path`` on every
        create/edit. If the caller wants the result elsewhere, copy it.
        """
        path = Path(path)
        if path != self._l2_path:
            import shutil  # noqa: PLC0415 — lazy
            shutil.copyfile(self._l2_path, path)
        return path


# ---------------------------------------------------------------------------
# Editor-only app harness (no DB / no dashboards)
# ---------------------------------------------------------------------------


def build_editor_app(cache: object) -> object:
    """A minimal Starlette app mounting just the editor routes bound to
    ``cache`` — enough to drive create/edit/save over HTTP.

    No DB pool, no dashboards (the dogfood's create+save path doesn't
    render dashboards — dashboard equivalence is a separate AI.5 step
    built from the saved L2). The trivial ``/`` route is the 303 target
    the create/save handlers redirect to on success.
    """
    from starlette.applications import Starlette  # noqa: PLC0415 — lazy
    from starlette.responses import PlainTextResponse  # noqa: PLC0415
    from starlette.routing import Route  # noqa: PLC0415

    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        make_editor_routes,
    )

    async def _home(_request: object) -> PlainTextResponse:
        return PlainTextResponse("studio")

    routes = [Route("/", _home), *make_editor_routes(cache)]  # type: ignore[arg-type]: cache is object-typed here to dodge the L2InstanceCache import cycle
    return Starlette(routes=routes)

