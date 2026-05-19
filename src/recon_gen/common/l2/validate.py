"""Cross-entity validation for a loaded ``L2Instance`` (M.1.3).

The loader (M.1.2) catches malformed YAML + per-entity shape errors. This
module catches everything else the SPEC requires at load time — rules
that need to look across multiple entities to decide.

Public entry point: ``validate(instance)``. Raises ``L2ValidationError``
on the first failure with a message identifying the offending field +
the rule that failed.

**Locked rule (per L.1.18 + M.1.7):** every cross-entity validator that
``validate(instance)`` runs has a dedicated rejection test in
``tests/test_l2_validate.py``. The rule numbering in this docstring
matches the test names (e.g. rule U1 → ``test_u1_duplicate_account_id_rejected``).
Adding a new validator MUST land its rejection test in the same commit
that introduces it; the audit table below extends to cover the new rule.

Rules enforced (numbered for cross-reference with the test file):
  U1. Account.id values are unique within ``accounts``.
  U2. AccountTemplate.role values are unique within ``account_templates``.
  U3. Rail.name values are unique within ``rails``.
  U4. TransferTemplate.name values are unique within ``transfer_templates``.
  U5. LimitSchedule (parent_role, rail, direction) triples are unique
      (M.1a — duplicate combinations are a configuration error).
      Z.B (2026-05-15): renamed from (parent_role, transfer_type) when
      the symmetric transfer_type collapse landed. AB.1 (2026-05-19):
      added ``direction`` to the key — same (parent_role, rail) may
      now appear twice with different directions (one Outbound +
      one Inbound).
  U7. AccountTemplate-generated account_ids MUST NOT collide with any
      declared Account.id (AA.A.6.bug 2026-05-17). The seed plants both
      the singleton AND the template-rendered instance under the same
      id, producing two ``account_name`` values for one account_id;
      downstream the L1 dashboard renders inconsistent dropdown labels
      vs WHERE-clause matches and silently breaks per-account narrowing.
      Author resolution: rename the singleton, drop the redundant
      template, OR set ``instance_id_template`` to a non-colliding
      pattern. See :func:`recon_gen.common.l2.auto_scenario.
      template_instance_ids` — the validator walks the same rendering
      path the seed uses so the collision set is computed identically.

  Removed under Z.B grammar collapse (PLAN.md §Z.B — locked 2026-05-15):
  - U6 (Rail per-leg ``(transfer_type, role)`` discriminators unique) —
    transfer_type is gone; rail-to-transaction binding is now `rail_name`
    directly, which U3 already enforces unique.

  R1. Every Role referenced by a Rail (source_role / destination_role /
      leg_role) resolves to some Account.role OR AccountTemplate.role.
  R2. Every Account.parent_role resolves to some Account.role OR
      AccountTemplate.role.
  R3. Every AccountTemplate.parent_role MUST resolve to a singleton
      Account.role (NOT an AccountTemplate.role) — per the SPEC's
      "Singleton parent only" rule on AccountTemplate.
  R4. Every RailName in a TransferTemplate.leg_rails exists in ``rails``.
  R5. Every Chain.parent and every Chain.children entry resolves to a
      Rail name OR TransferTemplate name.
  R6. Every LimitSchedule.parent_role resolves to some declared Role.
  R7. Every TransferTemplate.leg_rails entry references a NON-aggregating
      Rail (M.1a — aggregating rails sweep on a cadence and don't carry
      the per-instance identity a TransferKey-grouped template needs).
  R8. Every Rail with ``max_unbundled_age`` set MUST appear in some
      AggregatingRail's ``bundles_activity`` (M.1a — otherwise the watch
      can never fire).
  R9. Every dotted-form BundleSelector (``Template.LegRail``) references
      a rail that is actually in that template's ``leg_rails`` (M.1a —
      catches typos + leg-rail cross-references at load).
  R10. Every ``LimitSchedule.rail`` matches some declared ``Rail.name``
      (M.2d.1 — a cap declared against a rail no L2 declares is a
      no-op; catches typos). Z.B (2026-05-15): formerly checked
      transfer_type alignment; under collapse the cap binds directly
      to a rail name.
  R11. Every bare-form (``<name>``, not ``Template.LegRail``) entry in
      an AggregatingRail's ``bundles_activity`` resolves to a declared
      ``Rail.name`` (Z.B 2026-05-15: formerly also matched
      Rail.transfer_type, dropped under the symmetric collapse).
      Companion to R8 (which checks the inverse: any rail with
      ``max_unbundled_age`` set must appear in *some* bundles_activity).
  R12. Every ``TransferKey`` field name MUST appear in
      ``metadata_keys`` of every Rail in the template's ``leg_rails``
      (M.3.13 — a TransferKey field is auto-derived as a
      ``PostedRequirement`` for every leg_rail; if the field isn't
      declared in the rail's ``metadata_keys``, the integrator's ETL
      has no legitimate place to populate it, and the leg can never
      reach Status=Posted).
  R13. Every key in a Rail's ``metadata_value_examples`` MUST appear
      in the same Rail's ``metadata_keys`` (M.4.2b — a typo'd example
      key would otherwise be silently ignored by the seed picker;
      catch it at load).

  C1. Every TransferTemplate contains at most one *non-grouped*
      Variable-direction leg (AB.3 rewrite — Variables that are
      members of a ``leg_rail_xor_groups`` group are exempt from this
      count; the runtime "exactly one fires per Transfer" check moves
      to the ``_xor_group_violation`` matview).
  C1a. Every member of every ``TransferTemplate.leg_rail_xor_groups``
      group is also in that template's ``leg_rails``.
  C1b. Every XOR-group member resolves to a Variable-direction
      SingleLegRail (Debit / Credit / non-SingleLeg rails are excluded).
  C1c. No rail appears in two XOR groups within the same template
      (overlap groups can't resolve to one firing deterministically).
  C1d. Every XOR group has ≥2 members (a 1-member group is degenerate).
  C3. Every Variable-direction SingleLegRail MUST appear in some
      ``TransferTemplate.leg_rails`` (M.3.13 — Variable closure
      semantics require a containing template's ``ExpectedNet`` to
      compute the leg's amount + direction; a Variable rail
      reconciled only by an AggregatingRail has no closure target).
  C5. Every Chain row's ``children`` list is non-empty (Z.A grammar
      collapse — singleton ⇒ required, multi ⇒ XOR; an empty list is
      a degenerate row that encodes no firing rule. Defense-in-depth
      against in-memory L2 instances built outside the loader; loader
      rejects empty lists earlier with a more actionable error.)
  C6. For any given Chain parent, no child appears in two Chain rows
      (Z.A grammar collapse — the new failure mode the collapsed shape
      introduces. E.g. one row says "Foo is required" plus another
      says "Foo is one of [Foo, Bar]" — the two rows contradict so
      reject at load.)
  C8a. ``fan_in=True`` requires every chain child to resolve to a
      TransferTemplate. Rail-as-child fan-in isn't well-defined per
      AB.4 gap doc §2 footnote — a rail's per-Transfer parent is the
      canonical 1:1 shape.
  C8b. ``expected_parent_count`` MUST be None when ``fan_in=False``
      (the field only carries meaning under fan-in; setting it on a
      non-fan-in chain is operator confusion).
  C8c. ``expected_parent_count`` MUST be ≥2 when set under
      ``fan_in=True`` (a 1-parent fan-in chain is degenerate — it's
      just a 1:1 chain).

  Removed under Z.A grammar collapse (PLAN.md §Z.A — locked 2026-05-13):
  - C2 (xor_group members share parent) — every Chain row IS one
    parent, so the cross-parent failure mode is unrepresentable.
  - C4 (xor_group ≥ 2 members) — singleton means "required", not
    "degenerate XOR". The cardinality-1 case is now a meaningful row
    shape, not an error.
  - C4.1 (required + xor_group contradiction) — the two flags are
    gone; the contradiction is unrepresentable.

  S1. A two-leg Rail that is NOT a TransferTemplate leg MUST have
      ``expected_net`` set.
  S2. A two-leg Rail that IS a TransferTemplate leg MUST NOT have
      ``expected_net`` set (the template owns the bundle's ExpectedNet).
  S3. Every NON-aggregating single-leg Rail MUST be reconciled — appears
      in some TransferTemplate.leg_rails OR some aggregating Rail's
      bundles_activity (matched by Rail.name; Z.B 2026-05-15 dropped
      the legacy Rail.transfer_type alternative). Aggregating single-leg
      rails are exempt — they ARE the reconciliation mechanism (per
      SPEC's "single-leg sweep that lands in an external counterparty"
      example).
  S4. Aggregating Rails MUST NOT appear in any Chain.children.
  S5. Aggregating Rails MUST declare both ``cadence`` and
      ``bundles_activity``.
  S6. Non-aggregating Rails MUST NOT declare ``cadence`` or
      ``bundles_activity``.

  V1. Every TransferTemplate.completion matches a v1
      CompletionExpression vocabulary literal.
  V2. Every aggregating Rail's cadence matches a v1 CadenceExpression
      vocabulary literal.

  O1. Every leg of every Rail resolves to an Origin per the SPEC's
      per-leg Origin resolution table (M.1a). 1-leg rails MUST set
      ``origin``; 2-leg rails MUST cover both legs via either rail-level
      ``origin`` alone OR both per-leg overrides OR one override + the
      rail-level fallback.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from .primitives import (
    Identifier,
    L2Instance,
    Rail,
    SingleLegRail,
    TwoLegRail,
)


# -- Errors -------------------------------------------------------------------


class L2ValidationError(ValueError):
    """Raised when a loaded ``L2Instance`` fails cross-entity validation."""


# -- Vocabulary literals (per SPEC v1) ----------------------------------------


_COMPLETION_PATTERNS = (
    re.compile(r"^business_day_end$"),
    re.compile(r"^business_day_end\+(\d+)d$"),
    re.compile(r"^month_end$"),
    re.compile(r"^metadata\.[A-Za-z_][A-Za-z0-9_]*$"),
)

_WEEKDAY_NAMES = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}

_CADENCE_PATTERNS = (
    re.compile(r"^intraday-(\d+)h$"),
    re.compile(r"^daily-eod$"),
    re.compile(r"^daily-bod$"),
    re.compile(r"^weekly-(mon|tue|wed|thu|fri|sat|sun)$"),
    re.compile(r"^monthly-eom$"),
    re.compile(r"^monthly-bom$"),
    re.compile(r"^monthly-(\d+)$"),
)


def _completion_is_valid(expr: str) -> bool:
    return any(p.match(expr) for p in _COMPLETION_PATTERNS)


def _cadence_is_valid(expr: str) -> bool:
    for p in _CADENCE_PATTERNS:
        m = p.match(expr)
        if not m:
            continue
        # Bounds checks: monthly-N is day-of-month 1..31.
        if expr.startswith("monthly-") and expr not in ("monthly-eom", "monthly-bom"):
            day = int(m.group(1))
            if not 1 <= day <= 31:
                return False
        return True
    return False


# -- Public API --------------------------------------------------------------


def validate(instance: L2Instance) -> None:
    """Run every cross-entity validation rule on ``instance``.

    Fail-fast: raises ``L2ValidationError`` on the first rule violation
    with a message naming the offending field + the rule.
    """
    _check_unique_account_ids(instance)
    _check_unique_account_template_roles(instance)
    _check_unique_rail_names(instance)
    _check_unique_transfer_template_names(instance)
    _check_unique_limit_schedule_combinations(instance)
    _check_no_template_id_collides_with_singleton(instance)

    account_roles = {a.role for a in instance.accounts if a.role is not None}
    template_roles = {t.role for t in instance.account_templates}
    all_roles = account_roles | template_roles
    rail_names = {r.name for r in instance.rails}
    template_names = {t.name for t in instance.transfer_templates}
    rails_by_name: dict[Identifier, Rail] = {r.name: r for r in instance.rails}

    _check_role_references(instance, all_roles)
    _check_account_parent_role_resolves(instance, all_roles)
    _check_account_template_parent_role_is_singleton(
        instance, account_roles, template_roles,
    )
    _check_template_leg_rails_exist(instance, rail_names)
    _check_template_has_at_least_one_leg_rail(instance)
    _check_chain_endpoints_exist(instance, rail_names, template_names)
    _check_limit_schedule_parent_role_resolves(instance, all_roles)
    _check_template_leg_rails_are_non_aggregating(instance, rails_by_name)
    _check_max_unbundled_age_only_on_bundled_rails(instance)
    _check_dotted_bundle_selectors_resolve(instance)
    _check_limit_schedule_rail_resolves(instance, rail_names)
    _check_bare_bundles_activity_selectors_resolve(instance)
    _check_transfer_key_in_leg_rail_metadata_keys(instance, rails_by_name)
    _check_metadata_value_example_keys_resolve(instance)

    _check_variable_leg_count_per_template(instance)
    _check_leg_rail_xor_group_shape(instance)
    _check_variable_single_leg_in_some_template(instance, rails_by_name)
    _check_chain_parent_has_non_empty_children(instance)
    _check_chain_no_duplicate_child_per_parent(instance)
    _check_fan_in_shape(instance, template_names)

    _check_two_leg_expected_net_consistency(instance)
    _check_single_leg_reconciliation(instance)
    _check_chain_aggregating_not_child(instance)
    _check_aggregating_rail_required_fields(instance)
    _check_amount_typical_range_shape(instance)
    _check_non_aggregating_rail_no_cadence_or_bundles(instance)
    _check_role_business_day_offsets_resolve(instance, all_roles)

    _check_completion_vocabulary(instance)
    _check_cadence_vocabulary(instance)

    _check_per_leg_origin_resolution(instance)


# -- Uniqueness (U1-U4) ------------------------------------------------------


def _check_unique_account_ids(inst: L2Instance) -> None:
    """U1."""
    _reject_duplicates(
        (a.id for a in inst.accounts), label="Account.id",
    )


def _check_unique_account_template_roles(inst: L2Instance) -> None:
    """U2."""
    _reject_duplicates(
        (t.role for t in inst.account_templates), label="AccountTemplate.role",
    )


def _check_unique_rail_names(inst: L2Instance) -> None:
    """U3."""
    _reject_duplicates(
        (r.name for r in inst.rails), label="Rail.name",
    )


def _check_unique_transfer_template_names(inst: L2Instance) -> None:
    """U4."""
    _reject_duplicates(
        (t.name for t in inst.transfer_templates),
        label="TransferTemplate.name",
    )


def _check_unique_limit_schedule_combinations(inst: L2Instance) -> None:
    """U5: each (parent_role, rail, direction) triple appears at most once.

    Per SPEC: duplicate combinations are a load-time configuration error
    (the projection into ``StoredBalance.Limits`` would be ambiguous —
    which cap wins?). Z.B (2026-05-15): renamed from
    (parent_role, transfer_type) under the symmetric collapse. AB.1
    (2026-05-19) added ``direction``: the same ``(parent_role, rail)``
    may now carry both an Outbound AND an Inbound cap (per-direction
    flow caps split AML inbound thresholds from per-rail send caps).
    """
    seen: dict[tuple[Identifier, str, str], int] = {}
    for i, ls in enumerate(inst.limit_schedules):
        key = (ls.parent_role, ls.rail, ls.direction)
        if key in seen:
            raise L2ValidationError(
                f"limit_schedules[{i}]: duplicate "
                f"(parent_role={ls.parent_role!r}, "
                f"rail={ls.rail!r}, "
                f"direction={ls.direction!r}) — also declared at "
                f"limit_schedules[{seen[key]}]"
            )
        seen[key] = i


def _check_no_template_id_collides_with_singleton(inst: L2Instance) -> None:
    """U7: AccountTemplate-generated account_ids MUST NOT collide with
    any declared Account.id.

    The seed plants both the singleton AND the template-rendered
    instance under the same id, producing two ``account_name`` values
    for one ``account_id``. The L1 dashboard's dropdown source
    (``current_daily_balances`` DISTINCT) then advertises both display
    strings; the WHERE clause picks rows by *one* of them, so picking
    the dropdown option silently narrows to half the account's rows.

    Walks :func:`recon_gen.common.l2.auto_scenario.template_instance_ids`
    so the validator's ID set is the same set the seed will plant —
    impossible to drift apart.
    """
    # Import locally to avoid auto_scenario ↔ validate import cycle
    # at module load (auto_scenario imports nothing from validate but
    # the surrounding common.l2 package wires them together).
    from recon_gen.common.l2.auto_scenario import template_instance_ids
    singleton_ids = {str(a.id): i for i, a in enumerate(inst.accounts)}
    for ti, template in enumerate(inst.account_templates):
        for generated in template_instance_ids(template):
            if generated in singleton_ids:
                raise L2ValidationError(
                    f"account_templates[{ti}] (role={template.role!r}) "
                    f"materializes account_id {generated!r} which is "
                    f"already declared as a singleton at "
                    f"accounts[{singleton_ids[generated]}] — rename the "
                    f"singleton, drop the redundant template, OR set "
                    f"the template's ``instance_id_template`` to a "
                    f"non-colliding pattern (e.g. "
                    f"``tmpl-cust-{{n:03d}}``). Per U7 — collision "
                    f"breaks L1 dashboard per-account narrowing."
                )


def _reject_duplicates(values: Iterable[Identifier], *, label: str) -> None:
    counts = Counter(values)
    dupes = sorted(v for v, c in counts.items() if c > 1)
    if dupes:
        raise L2ValidationError(
            f"duplicate {label} values: {dupes!r}"
        )


# -- Reference resolution (R1-R6) --------------------------------------------


def _check_role_references(inst: L2Instance, all_roles: set[Identifier]) -> None:
    """R1: Every Role referenced by a Rail's role fields resolves to a declared Role."""
    for r in inst.rails:
        match r:
            case TwoLegRail(name=n, source_role=src, destination_role=dst):
                _check_role_set(src, all_roles, where=f"Rail {n!r}.source_role")
                _check_role_set(dst, all_roles, where=f"Rail {n!r}.destination_role")
            case SingleLegRail(name=n, leg_role=leg):
                _check_role_set(leg, all_roles, where=f"Rail {n!r}.leg_role")


def _check_role_set(
    roles: tuple[Identifier, ...], declared: set[Identifier], *, where: str,
) -> None:
    missing = [r for r in roles if r not in declared]
    if missing:
        raise L2ValidationError(
            f"{where}: roles {missing!r} are not declared on any "
            f"Account or AccountTemplate"
        )


def _check_account_parent_role_resolves(
    inst: L2Instance, all_roles: set[Identifier],
) -> None:
    """R2: every Account.parent_role resolves to some declared Role."""
    for a in inst.accounts:
        if a.parent_role is not None and a.parent_role not in all_roles:
            raise L2ValidationError(
                f"Account {a.id!r}.parent_role={a.parent_role!r}: "
                f"role is not declared on any Account or AccountTemplate"
            )


def _check_account_template_parent_role_is_singleton(
    inst: L2Instance,
    account_roles: set[Identifier],
    template_roles: set[Identifier],
) -> None:
    """R3: AccountTemplate.parent_role MUST resolve to a singleton Account.

    Per SPEC: template-under-template nesting is forbidden because the
    per-instance parent assignment becomes ambiguous (which of N
    parent-template instances does a given child-template instance roll
    up to?).
    """
    for t in inst.account_templates:
        if t.parent_role is None:
            continue
        if t.parent_role in template_roles and t.parent_role not in account_roles:
            raise L2ValidationError(
                f"AccountTemplate {t.role!r}.parent_role={t.parent_role!r}: "
                f"resolves to another AccountTemplate, but parent_role MUST "
                f"resolve to a singleton Account (template-under-template "
                f"nesting is forbidden)"
            )
        if t.parent_role not in account_roles:
            raise L2ValidationError(
                f"AccountTemplate {t.role!r}.parent_role={t.parent_role!r}: "
                f"role is not declared on any Account"
            )


def _check_template_leg_rails_exist(
    inst: L2Instance, rail_names: set[Identifier],
) -> None:
    """R4: every RailName in TransferTemplate.leg_rails exists."""
    for t in inst.transfer_templates:
        missing = [n for n in t.leg_rails if n not in rail_names]
        if missing:
            raise L2ValidationError(
                f"TransferTemplate {t.name!r}.leg_rails: rails {missing!r} "
                f"are not declared in rails"
            )


def _check_template_has_at_least_one_leg_rail(inst: L2Instance) -> None:
    """R4.1 (X.4.f.10): every TransferTemplate must declare at least one
    leg_rail. A template with zero leg_rails has no rail firings to
    bundle into a transfer event — there's nothing for the L1 layer to
    measure ``expected_net`` or ``completion`` against. The Studio
    editor surfaces this as the inline error when the operator
    de-selects the last rail in the multi_select; the L1 layer would
    silently ignore the template otherwise.
    """
    for t in inst.transfer_templates:
        if len(t.leg_rails) == 0:
            raise L2ValidationError(
                f"TransferTemplate {t.name!r}.leg_rails is empty — "
                f"a template must declare at least one leg_rail. Either "
                f"add a replacement rail or delete the whole template."
            )


def _check_chain_endpoints_exist(
    inst: L2Instance,
    rail_names: set[Identifier],
    template_names: set[Identifier],
) -> None:
    """R5: every Chain.parent and every Chain.children entry resolves to a Rail or Template."""
    valid = rail_names | template_names
    for i, c in enumerate(inst.chains):
        if c.parent not in valid:
            raise L2ValidationError(
                f"chains[{i}].parent={c.parent!r}: not a declared Rail "
                f"or TransferTemplate name"
            )
        for j, child in enumerate(c.children):
            if child not in valid:
                raise L2ValidationError(
                    f"chains[{i}].children[{j}]={child!r}: not a declared "
                    f"Rail or TransferTemplate name"
                )


def _check_limit_schedule_parent_role_resolves(
    inst: L2Instance, all_roles: set[Identifier],
) -> None:
    """R6: every LimitSchedule.parent_role resolves to some declared Role."""
    for i, ls in enumerate(inst.limit_schedules):
        if ls.parent_role not in all_roles:
            raise L2ValidationError(
                f"limit_schedules[{i}].parent_role={ls.parent_role!r}: "
                f"role is not declared on any Account or AccountTemplate"
            )


def _check_template_leg_rails_are_non_aggregating(
    inst: L2Instance, rails_by_name: dict[Identifier, Rail],
) -> None:
    """R7: every TransferTemplate.leg_rails entry references a non-Aggregating Rail.

    Per SPEC: aggregating rails sweep on a cadence and don't carry the
    per-instance identity a TransferKey-grouped template needs. Listing
    one in ``leg_rails`` is a configuration mistake — the template's
    ExpectedNet closure can't be evaluated against a sweeping rail.
    """
    for t in inst.transfer_templates:
        for n in t.leg_rails:
            r = rails_by_name.get(n)
            # R4 already guarantees `n` exists; this rule only triggers
            # when the referenced rail IS aggregating.
            if r is not None and r.aggregating:
                raise L2ValidationError(
                    f"TransferTemplate {t.name!r}.leg_rails: rail {n!r} is "
                    f"aggregating; aggregating rails sweep on a cadence and "
                    f"cannot serve as a template leg (the template's "
                    f"ExpectedNet closure can't bind to sweep activity)"
                )


def _check_max_unbundled_age_only_on_bundled_rails(inst: L2Instance) -> None:
    """R8: every Rail with ``max_unbundled_age`` set MUST appear in some
    AggregatingRail's ``bundles_activity``.

    Per SPEC: the watch fires when a Posted-and-eligible-for-bundling row
    sits unassigned past the threshold. If nothing bundles this rail, the
    watch can never fire — declaring it is a configuration error.

    Z.B (2026-05-15): bundles_activity matches Rail.name (or template-leg
    name in the dotted form) only — the legacy transfer_type fallback is
    gone with the symmetric collapse.
    """
    bundled: set[Identifier] = set()
    for r in inst.rails:
        if not r.aggregating:
            continue
        for sel in r.bundles_activity:
            sel_str = str(sel)
            # Dotted form (Template.LegRail) — the leg-rail name is the
            # part after the dot; that IS what gets bundled.
            if "." in sel_str:
                _, _, leg = sel_str.partition(".")
                bundled.add(Identifier(leg))
            else:
                # Bare identifier — Rail.name or TransferTemplate.name.
                bundled.add(Identifier(sel_str))
    for r in inst.rails:
        if r.max_unbundled_age is None:
            continue
        if r.name in bundled:
            continue
        raise L2ValidationError(
            f"Rail {r.name!r}: max_unbundled_age is set but no aggregating "
            f"Rail bundles this rail (rail name {r.name!r} does not appear "
            f"in any bundles_activity); the watch can never fire"
        )


def _check_limit_schedule_rail_resolves(
    inst: L2Instance, rail_names: set[Identifier],
) -> None:
    """R10: every LimitSchedule.rail matches some declared Rail.name.

    Per M.2d.1: a cap declared against a rail that no L2 emits is a
    no-op — the limit-breach matview keys off the rail name, so a
    typo'd cap never fires. Caught at YAML load.

    Z.B (2026-05-15): formerly checked transfer_type alignment; under
    the symmetric collapse the cap binds directly to a rail name.
    """
    for i, ls in enumerate(inst.limit_schedules):
        if ls.rail not in rail_names:
            raise L2ValidationError(
                f"limit_schedules[{i}].rail={ls.rail!r}: "
                f"no declared Rail with this name "
                f"(declared: {sorted(rail_names)!r}). The cap "
                f"would silently never fire."
            )


def _check_bare_bundles_activity_selectors_resolve(inst: L2Instance) -> None:
    """R11: every bare-form bundles_activity selector resolves.

    Per M.2d.1: a bare-form selector (``<name>``, not ``Template.LegRail``)
    must match a declared Rail.name. Otherwise the bundler matches
    nothing and the aggregating rail silently never sweeps. R8
    (max_unbundled_age set ⇒ rail must be bundled) and R9 (dotted form
    ⇒ template + leg actually exist) cover the inverse and the dotted
    form respectively; this rule catches typos in the bare form.

    Z.B (2026-05-15): formerly accepted Rail.transfer_type as a fallback
    match; transfer_type is gone with the symmetric collapse.
    """
    rail_names = {r.name for r in inst.rails}
    for r in inst.rails:
        if not r.aggregating:
            continue
        for sel in r.bundles_activity:
            sel_str = str(sel)
            if "." in sel_str:
                # Dotted form — R9's job, not R11's.
                continue
            if sel_str in rail_names:
                continue
            raise L2ValidationError(
                f"Rail {r.name!r}.bundles_activity: bare selector "
                f"{sel_str!r} resolves to no declared Rail.name "
                f"(rail names: {sorted(rail_names)!r}). The bundler "
                f"would silently match nothing."
            )


def _check_dotted_bundle_selectors_resolve(inst: L2Instance) -> None:
    """R9: every dotted-form BundleSelector references a real template-leg pair.

    Per SPEC: ``Template.LegRail`` is one of the 4 BundleSelector forms;
    it scopes the bundler's eligibility to one specific leg-pattern of
    a multi-leg template. This rule catches typos in either side AND
    cross-references where the leg-rail isn't actually a leg of that
    template (a common mistake when copy-pasting selectors).
    """
    template_leg_rails: dict[Identifier, set[Identifier]] = {
        t.name: set(t.leg_rails) for t in inst.transfer_templates
    }
    for r in inst.rails:
        if not r.aggregating:
            continue
        for sel in r.bundles_activity:
            sel_str = str(sel)
            if "." not in sel_str:
                continue
            template_name, _, leg_name = sel_str.partition(".")
            tn = Identifier(template_name)
            if tn not in template_leg_rails:
                raise L2ValidationError(
                    f"Rail {r.name!r}.bundles_activity: dotted selector "
                    f"{sel_str!r} references TransferTemplate "
                    f"{template_name!r} which is not declared in "
                    f"transfer_templates"
                )
            ln = Identifier(leg_name)
            if ln not in template_leg_rails[tn]:
                raise L2ValidationError(
                    f"Rail {r.name!r}.bundles_activity: dotted selector "
                    f"{sel_str!r} references rail {leg_name!r} which is "
                    f"not in TransferTemplate {template_name!r}.leg_rails"
                )


def _check_transfer_key_in_leg_rail_metadata_keys(
    inst: L2Instance, rails_by_name: dict[Identifier, Rail],
) -> None:
    """R12: every TransferKey field name MUST appear in metadata_keys of
    every Rail in the template's leg_rails.

    Per SPEC §"PostedRequirements": TransferKey fields are auto-derived
    as PostedRequirements for every leg_rail (``derived.posted_requirements_for``).
    A leg can't be Posted without those fields populated. If the field
    isn't declared in the rail's ``metadata_keys``, the integrator's
    ETL has no legitimate place to populate it — the column simply
    doesn't exist on the rail's posting shape — and the rail can never
    reach Status=Posted. That's a configuration error, caught at load
    instead of at first posting attempt.
    """
    for t in inst.transfer_templates:
        if not t.transfer_key:
            continue
        for n in t.leg_rails:
            r = rails_by_name.get(n)
            # R4 already guarantees the rail exists; defensive skip.
            if r is None:
                continue
            missing = [
                k for k in t.transfer_key if k not in r.metadata_keys
            ]
            if missing:
                raise L2ValidationError(
                    f"TransferTemplate {t.name!r}.transfer_key={list(t.transfer_key)!r}: "
                    f"leg_rail {n!r}.metadata_keys={list(r.metadata_keys)!r} "
                    f"is missing TransferKey field(s) {missing!r}; the "
                    f"library auto-derives these as PostedRequirements, "
                    f"so a leg whose rail can't carry the field can never "
                    f"reach Status=Posted"
                )


def _check_metadata_value_example_keys_resolve(inst: L2Instance) -> None:
    """R13: every key in a Rail's ``metadata_value_examples`` MUST also
    appear in that Rail's ``metadata_keys``.

    Catches typos. The seed picker only consults examples by-key for
    keys it's already iterating from ``metadata_keys``, so a typo'd
    example-list key would silently never be used — the integrator
    would never see a feedback signal that their example data is
    wrong. Caught at load instead.
    """
    for r in inst.rails:
        if not r.metadata_value_examples:
            continue
        declared = set(r.metadata_keys)
        for key, _values in r.metadata_value_examples:
            if key not in declared:
                raise L2ValidationError(
                    f"Rail {r.name!r}.metadata_value_examples: key "
                    f"{key!r} is not in metadata_keys "
                    f"{list(r.metadata_keys)!r}; example values would "
                    f"be silently ignored. Add the key to metadata_keys "
                    f"or remove the example list."
                )


# -- Cardinality (C1-C4) -----------------------------------------------------


def _check_variable_leg_count_per_template(inst: L2Instance) -> None:
    """C1 (AB.3 rewrite): at most one *non-grouped* Variable-direction
    leg per TransferTemplate.

    Pre-AB.3 C1 was "≤1 Variable per template, period". AB.3 relaxes
    that for XOR-grouped Variables: a template MAY declare any number
    of Variable-direction legs as long as every additional one beyond
    the first non-grouped Variable is a member of some
    ``leg_rail_xor_groups`` group (where the AB.3.3 matview enforces
    exactly-one-firing-per-Transfer at runtime). The structural
    invariants on the groups themselves live in C1a-d below.
    """
    rails_by_name: dict[str, Rail] = {r.name: r for r in inst.rails}
    for t in inst.transfer_templates:
        grouped: set[Identifier] = {
            member for group in t.leg_rail_xor_groups for member in group
        }
        variable_legs = [
            n for n in t.leg_rails
            if isinstance(rails_by_name.get(n), SingleLegRail)
            and isinstance(rails_by_name[n], SingleLegRail)
            and rails_by_name[n].leg_direction == "Variable"  # type: ignore[union-attr]: narrowed by the prior isinstance(..., SingleLegRail) check
        ]
        non_grouped_variables = [n for n in variable_legs if n not in grouped]
        if len(non_grouped_variables) > 1:
            raise L2ValidationError(
                f"TransferTemplate {t.name!r}: contains "
                f"{len(non_grouped_variables)} non-grouped Variable-"
                f"direction legs ({non_grouped_variables!r}); SPEC C1 "
                f"requires at most one (otherwise closure is "
                f"under-determined). Variables in `leg_rail_xor_groups` "
                f"don't count — see AB.3 lock."
            )


def _check_leg_rail_xor_group_shape(inst: L2Instance) -> None:
    """C1a-d (AB.3): structural rules on TransferTemplate.leg_rail_xor_groups.

    - **C1a**: every member of every group MUST appear in the same
      template's ``leg_rails``.
    - **C1b**: every member MUST resolve to a Variable-direction
      SingleLegRail. (A non-Variable rail in an XOR group is a category
      error — the "exactly one fires per Transfer" matview enforcement
      only makes sense for Variable closure legs.)
    - **C1c**: no rail appears in two XOR groups within the same
      template. (Overlap groups can't be resolved to one firing
      deterministically.)
    - **C1d**: every group MUST have ≥2 members. (A 1-member group is
      degenerate — the rail always fires; the XOR adds no information.
      Same defense-in-depth shape as Z.A's C5 empty-children check.)

    Runtime "exactly one fires per Transfer" check lives in the AB.3.3
    ``_xor_group_violation`` matview, not here.
    """
    rails_by_name: dict[Identifier, Rail] = {r.name: r for r in inst.rails}
    for t in inst.transfer_templates:
        leg_rails_set: set[Identifier] = set(t.leg_rails)
        seen_members: dict[Identifier, int] = {}  # member -> group index
        for gi, group in enumerate(t.leg_rail_xor_groups):
            if len(group) < 2:
                raise L2ValidationError(
                    f"TransferTemplate {t.name!r}.leg_rail_xor_groups[{gi}]: "
                    f"has {len(group)} member(s); SPEC C1d requires "
                    f"at least 2 (a 1-member XOR group is degenerate)."
                )
            for member in group:
                if member not in leg_rails_set:
                    raise L2ValidationError(
                        f"TransferTemplate {t.name!r}."
                        f"leg_rail_xor_groups[{gi}]: member {member!r} "
                        f"is not in this template's `leg_rails`; SPEC "
                        f"C1a requires every XOR-group member to also "
                        f"be declared as a leg_rail."
                    )
                rail = rails_by_name.get(member)
                if not (
                    isinstance(rail, SingleLegRail)
                    and rail.leg_direction == "Variable"
                ):
                    raise L2ValidationError(
                        f"TransferTemplate {t.name!r}."
                        f"leg_rail_xor_groups[{gi}]: member {member!r} "
                        f"must resolve to a Variable-direction "
                        f"SingleLegRail; SPEC C1b excludes Debit/Credit/"
                        f"non-SingleLeg rails from XOR groups (the "
                        f"exactly-one-fires runtime check only applies "
                        f"to Variable closure legs)."
                    )
                if member in seen_members:
                    prior_gi = seen_members[member]
                    raise L2ValidationError(
                        f"TransferTemplate {t.name!r}: rail {member!r} "
                        f"appears in two XOR groups (groups {prior_gi} "
                        f"and {gi}); SPEC C1c forbids overlap because "
                        f"the exactly-one-fires-per-group rule can't "
                        f"resolve deterministically when groups share "
                        f"a member."
                    )
                seen_members[member] = gi


def _check_variable_single_leg_in_some_template(
    inst: L2Instance, rails_by_name: dict[Identifier, Rail],
) -> None:
    """C3: every Variable-direction SingleLegRail MUST appear in some
    TransferTemplate.leg_rails.

    Per SPEC §"LegDirection = Variable": "Both the leg's amount AND
    direction are determined at posting time by ... the requirement that
    a containing TransferTemplate's ExpectedNet hold given the other
    legs already posted." A Variable rail not in any template has no
    closure target — the bundler-only reconciliation path (S3's other
    branch) doesn't compute closure amounts, only sweeps eligible rows.
    Catches the failure mode where an integrator declares a Variable
    rail and reconciles it via an aggregating bundler, expecting the
    closure to "just work".
    """
    template_leg_names: set[Identifier] = set()
    for t in inst.transfer_templates:
        template_leg_names.update(t.leg_rails)
    for r in inst.rails:
        if not isinstance(r, SingleLegRail):
            continue
        if r.leg_direction != "Variable":
            continue
        if r.name in template_leg_names:
            continue
        raise L2ValidationError(
            f"Rail {r.name!r}: Variable-direction single-leg rail is "
            f"not in any TransferTemplate.leg_rails; Variable closure "
            f"semantics require a containing template's ExpectedNet to "
            f"compute the leg's amount + direction at posting time"
        )


def _check_chain_no_duplicate_child_per_parent(inst: L2Instance) -> None:
    """C6 (Z.A grammar collapse): for any given parent, no child appears
    in two Chain rows.

    Pre-collapse, this failure mode was unrepresentable in code (the
    `required` + `xor_group` combination silently overlapped). Post-
    collapse, the collapsed shape lets the operator accidentally list
    the same child in two rows for the same parent — e.g. one row
    saying "Foo is required" plus another saying "Foo is one of [Foo,
    Bar]". The two rows contradict (Foo is required ⇒ Bar can't fire
    in the XOR; XOR ⇒ Foo doesn't have to fire), so reject at load.
    """
    for parent in {c.parent for c in inst.chains}:
        seen: dict[Identifier, int] = {}
        for c in inst.chains:
            if c.parent != parent:
                continue
            for child in c.children:
                seen[child] = seen.get(child, 0) + 1
        dupes = [name for name, count in seen.items() if count > 1]
        if dupes:
            raise L2ValidationError(
                f"Chain parent {str(parent)!r}: child(ren) {sorted(str(d) for d in dupes)!r} "
                f"appear in more than one chain row. Each child must "
                f"appear in exactly one row per parent — singleton row "
                f"= required, multi-item row = XOR among the listed "
                f"children. (PLAN.md §Z.A C6.)"
            )


def _check_fan_in_shape(
    inst: L2Instance,
    template_names: set[Identifier],
) -> None:
    """C8 (AB.4): structural rules on ``Chain.fan_in`` + ``Chain.expected_parent_count``.

    - **C8a**: ``fan_in=True`` requires every child to resolve to a
      TransferTemplate. Rail-as-child fan-in isn't well-defined —
      a rail's per-Transfer parent is the canonical 1:1 shape; the
      AB.4 gap doc §2 footnote closes this door explicitly.
    - **C8b**: ``expected_parent_count`` MUST be None when
      ``fan_in=False`` (the field only carries meaning under fan-in;
      setting it on a non-fan-in chain is operator confusion that
      would mislead the matview wiring).
    - **C8c**: when ``expected_parent_count`` is set under
      ``fan_in=True``, it MUST be ≥2 (a 1-parent fan-in chain is
      degenerate — it's just a 1:1 chain; the AB.4 contract is "≥2
      parent firings share one child Transfer").

    Runtime "actual parent count matches expected" check lives in
    the AB.4.7 ``_fan_in_disagreement`` matview, not here.
    """
    for c in inst.chains:
        if c.fan_in:
            non_template_children = [
                child for child in c.children
                if child not in template_names
            ]
            if non_template_children:
                raise L2ValidationError(
                    f"Chain parent={c.parent!r}: fan_in=True requires "
                    f"every child to resolve to a TransferTemplate; "
                    f"got non-template children "
                    f"{sorted(str(x) for x in non_template_children)!r}. "
                    f"SPEC C8a: rail-as-child fan-in is undefined — "
                    f"a rail's per-Transfer parent is the canonical "
                    f"1:1 shape (AB.4 gap doc §2 footnote)."
                )
            if (
                c.expected_parent_count is not None
                and c.expected_parent_count < 2
            ):
                raise L2ValidationError(
                    f"Chain parent={c.parent!r}: fan_in=True with "
                    f"expected_parent_count={c.expected_parent_count} "
                    f"is degenerate — SPEC C8c requires ≥2 "
                    f"(a 1-parent fan-in is just a 1:1 chain; if you "
                    f"only have 1 expected parent, drop fan_in)."
                )
        else:
            if c.expected_parent_count is not None:
                raise L2ValidationError(
                    f"Chain parent={c.parent!r}: expected_parent_count "
                    f"is set ({c.expected_parent_count}) but fan_in is "
                    f"False. SPEC C8b: the field only carries meaning "
                    f"under fan_in=True; remove it or set fan_in=True."
                )


def _check_chain_parent_has_non_empty_children(inst: L2Instance) -> None:
    """C5 (rewritten under Z.A): every chain row's ``children`` list is
    non-empty.

    Pre-collapse, C5 caught the "all-optional chain" mode (no required
    child, no XOR group). Post-collapse there's no all-optional mode —
    every row IS a firing rule (singleton ⇒ required, multi ⇒ XOR), so
    the only remaining failure mode is an empty children list. Loader
    rejects it earlier (Z.A.3), but keep this as a defense-in-depth
    check for in-memory L2 instances built outside the loader (tests,
    fuzz fixtures, editor mutations).
    """
    for c in inst.chains:
        if not c.children:
            raise L2ValidationError(
                f"Chain parent {str(c.parent)!r}: children list is empty. "
                f"Each chain row must list at least one child (singleton "
                f"= required, multi = XOR). Drop the row entirely if no "
                f"children apply. (PLAN.md §Z.A C5.)"
            )


# -- State-dependent (S1-S6) -------------------------------------------------


def _check_two_leg_expected_net_consistency(inst: L2Instance) -> None:
    """S1 + S2: standalone two-leg requires expected_net; template-leg forbids it."""
    template_leg_names: set[str] = set()
    for t in inst.transfer_templates:
        template_leg_names.update(t.leg_rails)

    for r in inst.rails:
        if not isinstance(r, TwoLegRail):
            continue
        is_template_leg = r.name in template_leg_names
        if is_template_leg and r.expected_net is not None:
            raise L2ValidationError(
                f"Rail {r.name!r}: appears in a TransferTemplate's "
                f"leg_rails AND declares expected_net; the template owns "
                f"the bundle's ExpectedNet so the rail MUST NOT carry one"
            )
        if not is_template_leg and r.expected_net is None:
            raise L2ValidationError(
                f"Rail {r.name!r}: standalone two-leg rail (not in any "
                f"TransferTemplate.leg_rails) MUST declare expected_net "
                f"(typically 0)"
            )


def _check_single_leg_reconciliation(inst: L2Instance) -> None:
    """S3: every non-aggregating single-leg Rail is reconciled.

    Aggregating single-leg rails ARE the reconciliation mechanism (per
    SPEC's Aggregating Rails section: "single-leg aggregating rails are
    permitted, e.g. a single-leg sweep that lands in an external
    counterparty"). Their drift exits the system into the External
    counterparty by design — they do not themselves need to appear in
    any other rail's bundles_activity. So the S3 reconciliation check
    only applies to non-aggregating single-leg rails.

    This exemption was surfaced by the M.1.8 kitchen-sink fixture (a
    single-leg aggregating rail tripped a literal reading of the SPEC
    rule). SPEC v1's wording amended in M.1.8 to make the exemption
    explicit.
    """
    template_leg_names: set[str] = set()
    for t in inst.transfer_templates:
        template_leg_names.update(t.leg_rails)

    aggregating_bundles: set[str] = set()
    for r in inst.rails:
        if r.aggregating:
            aggregating_bundles.update(r.bundles_activity)

    for r in inst.rails:
        if not isinstance(r, SingleLegRail):
            continue
        if r.aggregating:
            # Self-reconciling per the exemption above.
            continue
        in_template = r.name in template_leg_names
        in_aggregating = r.name in aggregating_bundles
        if not (in_template or in_aggregating):
            raise L2ValidationError(
                f"Rail {r.name!r}: single-leg rail is not reconciled "
                f"(not listed in any TransferTemplate.leg_rails AND "
                f"its name does not appear in any aggregating Rail's "
                f"bundles_activity); the drift it introduces would "
                f"persist forever"
            )


def _check_chain_aggregating_not_child(inst: L2Instance) -> None:
    """S4: aggregating Rails MUST NOT appear in any Chain.children."""
    aggregating_names = {r.name for r in inst.rails if r.aggregating}
    for i, c in enumerate(inst.chains):
        for j, child in enumerate(c.children):
            if child in aggregating_names:
                raise L2ValidationError(
                    f"chains[{i}].children[{j}]={child!r}: aggregating Rails "
                    f"MUST NOT appear in Chain.children (they sweep on "
                    f"cadence, not on a per-Transfer parent trigger)"
                )


def _check_aggregating_rail_required_fields(inst: L2Instance) -> None:
    """S5: aggregating Rails MUST declare cadence + bundles_activity."""
    for r in inst.rails:
        if not r.aggregating:
            continue
        if r.cadence is None:
            raise L2ValidationError(
                f"Rail {r.name!r}: aggregating=true requires cadence to be set"
            )
        if not r.bundles_activity:
            raise L2ValidationError(
                f"Rail {r.name!r}: aggregating=true requires "
                f"bundles_activity to be a non-empty list"
            )


def _check_amount_typical_range_shape(inst: L2Instance) -> None:
    """V1a-c (AB.5): structural rules on Rail.amount_typical_range.

    - **V1a**: when set, ``min < max`` (a degenerate single-point range
      would mean every firing samples the same amount — pointless soft
      bound; integrator probably meant either a single value [not the
      feature] or an actual range).
    - **V1b**: both ``min`` and ``max`` MUST be > 0. The bound is on
      ``abs(amount)``; signed direction is determined elsewhere
      (leg_direction for fixed rails; closure for Variable rails).
      Negative or zero magnitudes are operator confusion — reject
      loud rather than silently coerce.
    - **V1c**: ``amount_typical_range`` is forbidden on aggregating
      rails (per AB.5.0 lock — aggregator amounts derive from bundled
      children, so the per-firing bound's meaning is fuzzy; deferred
      to a future iteration if integrators want a sanity-check field
      on aggregators too).
    """
    for r in inst.rails:
        if r.amount_typical_range is None:
            continue
        lo, hi = r.amount_typical_range
        if lo >= hi:
            raise L2ValidationError(
                f"Rail {r.name!r}: amount_typical_range min ({lo}) "
                f"must be strictly less than max ({hi}); SPEC V1a "
                f"rejects degenerate single-point ranges."
            )
        if lo <= 0 or hi <= 0:
            raise L2ValidationError(
                f"Rail {r.name!r}: amount_typical_range values must "
                f"both be > 0 (got min={lo}, max={hi}); SPEC V1b — "
                f"the bound is on abs(amount), so signed/zero values "
                f"have no meaning here."
            )
        if r.aggregating:
            raise L2ValidationError(
                f"Rail {r.name!r}: amount_typical_range is forbidden "
                f"on aggregating rails (aggregator amounts derive from "
                f"bundled children; per-firing bound's meaning is "
                f"fuzzy); SPEC V1c per AB.5.0 lock."
            )


def _check_non_aggregating_rail_no_cadence_or_bundles(inst: L2Instance) -> None:
    """S6: non-aggregating Rails MUST NOT declare cadence or bundles_activity."""
    for r in inst.rails:
        if r.aggregating:
            continue
        if r.cadence is not None:
            raise L2ValidationError(
                f"Rail {r.name!r}: cadence is only meaningful when "
                f"aggregating=true; remove cadence or set aggregating=true"
            )
        if r.bundles_activity:
            raise L2ValidationError(
                f"Rail {r.name!r}: bundles_activity is only meaningful when "
                f"aggregating=true; remove bundles_activity or set "
                f"aggregating=true"
            )


# -- Vocabulary (V1-V2) ------------------------------------------------------


def _check_completion_vocabulary(inst: L2Instance) -> None:
    """V1: every TransferTemplate.completion matches a v1 vocabulary literal."""
    for t in inst.transfer_templates:
        if not _completion_is_valid(t.completion):
            raise L2ValidationError(
                f"TransferTemplate {t.name!r}.completion={t.completion!r}: "
                f"not a v1 CompletionExpression literal. Allowed: "
                f"business_day_end, business_day_end+Nd, month_end, "
                f"metadata.<key>"
            )


def _check_cadence_vocabulary(inst: L2Instance) -> None:
    """V2: every aggregating Rail's cadence matches a v1 vocabulary literal."""
    for r in inst.rails:
        if not r.aggregating or r.cadence is None:
            continue
        if not _cadence_is_valid(r.cadence):
            raise L2ValidationError(
                f"Rail {r.name!r}.cadence={r.cadence!r}: not a v1 "
                f"CadenceExpression literal. Allowed: intraday-Nh, "
                f"daily-eod, daily-bod, weekly-<mon..sun>, monthly-eom, "
                f"monthly-bom, monthly-<1..31>"
            )


# -- Per-leg Origin resolution (O1) ------------------------------------------


def _check_per_leg_origin_resolution(inst: L2Instance) -> None:
    """O1: every leg of every Rail resolves to an Origin per the SPEC's
    per-leg Origin resolution table.

    1-leg rails: ``origin`` MUST be set.
    2-leg rails: every leg MUST resolve under one of:
      - rail-level ``origin`` alone (covers both legs);
      - both ``source_origin`` AND ``destination_origin`` (per-leg);
      - one per-leg override + rail-level ``origin`` as fallback for
        the unspecified leg.

    The loader (M.1a.2) hard-rejects per-leg overrides on single-leg
    rails — that case never reaches this validator.
    """
    for r in inst.rails:
        if isinstance(r, SingleLegRail):
            if r.origin is None:
                raise L2ValidationError(
                    f"Rail {r.name!r}: single-leg rail MUST set origin "
                    f"(per-leg Origin overrides apply only to two-leg rails)"
                )
            continue
        # Two-leg
        source_resolved = r.source_origin is not None or r.origin is not None
        dest_resolved = r.destination_origin is not None or r.origin is not None
        if not source_resolved or not dest_resolved:
            raise L2ValidationError(
                f"Rail {r.name!r}: two-leg rail's "
                f"{'source' if not source_resolved else 'destination'} "
                f"leg has no resolved Origin. Either set rail-level "
                f"`origin` OR provide both `source_origin` + "
                f"`destination_origin` OR set the missing per-leg override "
                f"alongside rail-level `origin` as fallback."
            )


def _check_role_business_day_offsets_resolve(
    inst: L2Instance, all_roles: set[Identifier],
) -> None:
    """role_business_day_offsets keys must reference declared roles
    (M.4.4.14). Catches typos before they silently no-op at emit time.
    """
    if not inst.role_business_day_offsets:
        return
    declared = {str(r) for r in all_roles}
    for role in inst.role_business_day_offsets:
        if role not in declared:
            raise L2ValidationError(
                f"role_business_day_offsets key {role!r} doesn't match any "
                f"declared role (declared: {sorted(declared)})"
            )
