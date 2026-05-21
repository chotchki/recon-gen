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
        """Recreate every entity in dependency order:
        AccountTemplates → Accounts → Rails → TransferTemplates →
        Chains → LimitSchedules → top-level instance settings.

        The order guarantees referenced entities exist before their
        referrers (a rail's roles via accounts/templates; a template's
        ``transfer_key`` keys via its leg rails' metadata_keys; a chain's
        parent/children via rails+templates), so each create's validator
        pass succeeds.
        """
        for template in reference.account_templates:
            self.create_account_template(template)
        for account in reference.accounts:
            self.create_account(account)
        for rail in reference.rails:
            self.create_rail(rail)
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

