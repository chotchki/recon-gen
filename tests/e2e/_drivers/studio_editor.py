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

    def create_rail(
        self,
        rail: Rail,
        *,
        reconciler: "tuple[str, tuple[str, str]] | None" = None,
        reference: "L2Instance | None" = None,
        partial_xor_groups: "tuple[tuple[str, ...], ...] | None" = None,
    ) -> None:
        """Create a Rail. ``reconciler`` (BB.3) is
        ``(mode, (kind, name))`` where ``mode`` is ``"attach"`` or
        ``"create_new"``. ``reference`` is required for ``create_new``.
        ``partial_xor_groups`` (BB.3) is the leg_rail_xor_groups
        update to apply to the reconciler TT in the same composite
        save — needed when multiple Variable rails accumulate so C1
        stays satisfied; ``None`` skips the update."""
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
        """Recreate every entity in dependency order so each editor
        save's full ``validate()`` pass succeeds.

        For non-aggregating single-leg rails (the BI bilateral cases
        S3 / C3), the driver tracks which reconcilers have already
        been materialized in the editor cache. The FIRST rail that
        attaches to a given reconciler uses **create-new** mode
        (POST creates rail + new reconciler atomically per BB.3);
        subsequent rails attaching to the same reconciler use
        **attach-existing** (BB.1). This dogfoods the operator
        workflow: validator stays strict, no in-flight invalid state.

        Order:
          1. Accounts (parent-first topological pass).
          2. AccountTemplates.
          3a. Non-aggregating rails (with reconciler create-new /
              attach-existing as needed). max_unbundled_age deferred.
          3b. Aggregating rails (any not already created via wave 3a's
              create-new path).
          3c. Edit-in deferred max_unbundled_age.
          4. TransferTemplates (any not already created via wave 3a).
          5. Chains.
          6. LimitSchedules.
          7. Top-level instance settings.
        """
        for account in _topo_accounts_by_parent(reference.accounts):
            self.create_account(account)
        for template in reference.account_templates:
            self.create_account_template(template)

        non_aggregating = [r for r in reference.rails if not r.aggregating]
        aggregating = [r for r in reference.rails if r.aggregating]

        # BB.3 — track which reconcilers (TTs + aggregating Rails)
        # have already been created in the cache + which rails are
        # currently attached to each. The first rail attaching to a
        # reconciler uses create-new; subsequent rails for the same
        # reconciler attach-existing. For TTs with leg_rail_xor_groups
        # in reference, each attach step also pushes a partial
        # xor_groups update (filtered to rails currently in the
        # cached TT + the rail being attached) so C1 stays satisfied
        # as Variable rails accumulate.
        created_reconcilers: set[tuple[str, str]] = set()
        attached_rails: dict[tuple[str, str], list[str]] = {}
        deferred_max_unbundled_age: list[Rail] = []
        for rail in non_aggregating:
            reconciler = _reconciler_choice_for_rail(rail, reference)
            mode_payload: "tuple[str, tuple[str, str]] | None" = None
            partial_xor_groups: "tuple[tuple[str, ...], ...] | None" = None
            if reconciler is not None:
                rail_name_str = str(rail.name)
                attached_rails.setdefault(reconciler, []).append(rail_name_str)
                if reconciler in created_reconcilers:
                    mode_payload = ("attach", reconciler)
                    # For TT reconcilers with reference xor_groups,
                    # compute the partial groups that include just the
                    # currently-cached rails. Each attach pushes the
                    # incrementally-grown groups.
                    if reconciler[0] == "transfer_template":
                        partial_xor_groups = _partial_xor_groups_for_attach(
                            reference, reconciler[1],
                            attached_so_far=attached_rails[reconciler],
                        )
                else:
                    mode_payload = ("create_new", reconciler)
                    created_reconcilers.add(reconciler)
            if getattr(rail, "max_unbundled_age", None) is not None:
                stripped = dataclasses.replace(rail, max_unbundled_age=None)
                self.create_rail(
                    stripped, reconciler=mode_payload, reference=reference,
                    partial_xor_groups=partial_xor_groups,
                )
                deferred_max_unbundled_age.append(rail)
            else:
                self.create_rail(
                    rail, reconciler=mode_payload, reference=reference,
                    partial_xor_groups=partial_xor_groups,
                )
        for rail in aggregating:
            # Aggregating rails may have ALREADY been created in wave
            # 3a if some non-agg rail referenced one as its reconciler
            # via create-new mode. Skip those.
            if ("aggregating_rail", str(rail.name)) in created_reconcilers:
                continue
            self.create_rail(rail, reconciler=None, reference=reference)
        for rail in deferred_max_unbundled_age:
            self._edit(
                "rail", str(rail.name),
                _max_unbundled_age_edit_form_data(rail),
            )

        for template in reference.transfer_templates:
            # TTs may have ALREADY been created in wave 3a if some
            # non-agg rail referenced one as its reconciler via
            # create-new mode. Skip those.
            if ("transfer_template", str(template.name)) in created_reconcilers:
                continue
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


def _partial_xor_groups_for_attach(
    reference: L2Instance,
    tt_name: str,
    *,
    attached_so_far: list[str],
) -> "tuple[tuple[str, ...], ...] | None":
    """BB.3 — compute the leg_rail_xor_groups update for an attach
    step. Returns the reference TT's groups filtered to rails that
    are currently in the cached TT (per ``attached_so_far``).

    Groups with fewer than 2 currently-attached members are dropped
    (a single-member group isn't a useful XOR; C1 is only constraining
    when >1 Variable rails are present).

    Returns ``None`` when the reference TT has no xor_groups, so the
    transport skips the update payload entirely.
    """
    for t in reference.transfer_templates:
        if str(t.name) != tt_name:
            continue
        groups = getattr(t, "leg_rail_xor_groups", ()) or ()
        if not groups:
            return None
        attached_set = set(attached_so_far)
        partial: list[tuple[str, ...]] = []
        for g in groups:
            present = tuple(str(r) for r in g if str(r) in attached_set)
            if len(present) >= 2:
                partial.append(present)
        if not partial:
            return None
        return tuple(partial)
    return None


def _find_reconciler_in_reference(
    reference: L2Instance,
    reconciler_kind: str,
    reconciler_name: str,
) -> object:
    """BB.3 — fetch the reconciler entity (TT or aggregating Rail)
    from ``reference`` so the driver can serialize its fields into
    the create_new payload."""
    if reconciler_kind == "transfer_template":
        for t in reference.transfer_templates:
            if str(t.name) == reconciler_name:
                return t
        raise KeyError(
            f"reference has no TransferTemplate named {reconciler_name!r}"
        )
    if reconciler_kind == "aggregating_rail":
        for r in reference.rails:
            if r.aggregating and str(r.name) == reconciler_name:
                return r
        raise KeyError(
            f"reference has no aggregating Rail named {reconciler_name!r}"
        )
    raise ValueError(
        f"reconciler_kind={reconciler_kind!r} not recognized "
        f"(expected 'transfer_template' or 'aggregating_rail')"
    )


def _strip_rail_lists(reconciler: object, reconciler_kind: str) -> object:
    """BB.3 — strip the rail-list field (leg_rails for TTs;
    bundles_activity for aggregating Rails) on the reconciler entity
    being passed to the server's create-new path.

    The server appends the new rail's name to this field after
    coercion; the driver passes an empty list so subsequent rails
    (which use attach-existing) are added by their own POSTs without
    double-counting in the create-new step.
    """
    if reconciler_kind == "transfer_template":
        # TT requires at least one leg_rail at create time; pass a
        # one-element placeholder that the server replaces. The
        # server's _create_new_reconciler_with_rail discards the
        # placeholder by overwriting leg_rails with the rail being
        # created. Use a dummy that's a structurally-valid Identifier
        # (any non-empty string).
        return dataclasses.replace(
            reconciler, leg_rails=(),
        )
    if reconciler_kind == "aggregating_rail":
        return dataclasses.replace(
            reconciler, bundles_activity=(),
        )
    raise ValueError(
        f"reconciler_kind={reconciler_kind!r} not recognized"
    )


def _reconciler_choice_for_rail(
    rail: Rail,
    reference: L2Instance,
) -> "tuple[str, str] | None":
    """BB.3 — find the reconciler (TT or aggregating Rail) in
    ``reference`` that contains this rail's name.

    Returns ``(reconciler_kind, reconciler_name)`` when the rail
    needs a forward-reference reconciler at create time:

    - **Non-aggregating SingleLegRail** (S3 / C3 bilateral): must be
      in some TT.leg_rails or aggregator.bundles_activity.
    - **TwoLegRail without expected_net** (S5 bilateral): must be
      in some TT.leg_rails (the TT's expected_net closure replaces
      the standalone rail's). With expected_net set, the rail is
      self-standing and needs no reconciler.

    Returns ``None`` for:
    - Aggregating rails (self-reconciling per SPEC exemption)
    - TwoLegRails with expected_net set (self-standing)

    Search order (matches the BB.2 picker):

    1. **TransferTemplate** — ``leg_rails`` contains the rail's name.
    2. **Aggregating Rail** — ``bundles_activity`` contains the rail's
       name (single-leg only — TTs only host two-leg via leg_rails).
    """
    from recon_gen.common.l2.primitives import SingleLegRail, TwoLegRail

    if rail.aggregating:
        return None
    # Two-leg with expected_net set is self-standing — no reconciler
    # forward-reference needed at create time.
    if isinstance(rail, TwoLegRail) and rail.expected_net is not None:
        return None
    rail_name_str = str(rail.name)
    for t in reference.transfer_templates:
        if any(str(r) == rail_name_str for r in t.leg_rails):
            return ("transfer_template", str(t.name))
    # Aggregating-Rail reconciliation only applies to single-leg
    # (S3 covers single-leg; TwoLegRail's only out is a TT).
    if isinstance(rail, SingleLegRail):
        for r in reference.rails:
            if not r.aggregating:
                continue
            if any(str(b) == rail_name_str for b in r.bundles_activity):
                return ("aggregating_rail", str(r.name))
    return None


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

    def _create(
        self,
        kind: EntityKind,
        entity: object,
        *,
        extra: FormData | None = None,
    ) -> None:
        data = create_form_data(kind, entity)
        if extra:
            data = {**data, **extra}
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

    def create_rail(
        self,
        rail: Rail,
        *,
        reconciler: "tuple[str, tuple[str, str]] | None" = None,
        reference: "L2Instance | None" = None,
        partial_xor_groups: "tuple[tuple[str, ...], ...] | None" = None,
    ) -> None:
        """BB.3 — thread the reconciler payload + optional
        xor_groups update into the POST."""
        extra: FormData | None = None
        if reconciler is not None:
            mode, (rec_kind, rec_name) = reconciler
            extra = {
                "reconciler_kind": [rec_kind],
                "reconciler_name": [rec_name],
                "reconciler_mode": [mode],
            }
            if mode == "create_new":
                if reference is None:
                    raise ValueError(
                        "reconciler create_new requires `reference` to "
                        "look up the reconciler entity's fields"
                    )
                reconciler_entity = _find_reconciler_in_reference(
                    reference, rec_kind, rec_name,
                )
                stripped_reconciler = _strip_rail_lists(
                    reconciler_entity, rec_kind,
                )
                reconciler_form_kind = (
                    "transfer_template"
                    if rec_kind == "transfer_template" else "rail"
                )
                reconciler_form = create_form_data(
                    reconciler_form_kind, stripped_reconciler,
                )
                for k, v_list in reconciler_form.items():
                    extra[f"reconciler_new_{k}"] = list(v_list)
        if partial_xor_groups is not None:
            if extra is None:
                extra = {}
            extra["leg_rail_xor_groups__present"] = ["1"]
            extra["leg_rail_xor_groups__num_groups"] = [
                str(len(partial_xor_groups)),
            ]
            for i, group in enumerate(partial_xor_groups):
                extra[f"leg_rail_xor_groups_{i}"] = list(group)
        self._create("rail", rail, extra=extra)

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

