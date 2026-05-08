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
  U5. LimitSchedule (parent_role, transfer_type) combinations are unique
      (M.1a — duplicate combinations are a configuration error).
  U6. Rail per-leg ``(transfer_type, role)`` discriminators are unique
      across rails (P.9b — the Rail-to-Transaction binding is implicit
      via this tuple; two rails contributing the same discriminator make
      the binding ambiguous). Direction is intentionally NOT in the key.

  R1. Every Role referenced by a Rail (source_role / destination_role /
      leg_role) resolves to some Account.role OR AccountTemplate.role.
  R2. Every Account.parent_role resolves to some Account.role OR
      AccountTemplate.role.
  R3. Every AccountTemplate.parent_role MUST resolve to a singleton
      Account.role (NOT an AccountTemplate.role) — per the SPEC's
      "Singleton parent only" rule on AccountTemplate.
  R4. Every RailName in a TransferTemplate.leg_rails exists in ``rails``.
  R5. Every Chain.parent and Chain.child resolves to a Rail name OR
      TransferTemplate name.
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
  R10. Every ``LimitSchedule.transfer_type`` matches some declared
      ``Rail.transfer_type`` (M.2d.1 — a cap declared against a
      transfer_type no Rail emits is a no-op; catches typos).
  R11. Every bare-form (``<name>``, not ``Template.LegRail``) entry in
      an AggregatingRail's ``bundles_activity`` resolves to either a
      declared ``Rail.name`` OR some declared ``Rail.transfer_type``
      (M.2d.1 — catches typos that would silently make the bundler
      match nothing). Companion to R8 (which checks the inverse: any
      rail with ``max_unbundled_age`` set must appear in *some*
      bundles_activity).
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

  C1. Every TransferTemplate contains at most one Variable-direction leg.
  C2. Every Chain.xor_group's members share the same Chain.parent.
  C3. Every Variable-direction SingleLegRail MUST appear in some
      ``TransferTemplate.leg_rails`` (M.3.13 — Variable closure
      semantics require a containing template's ``ExpectedNet`` to
      compute the leg's amount + direction; a Variable rail
      reconciled only by an AggregatingRail has no closure target).
  C4. Every Chain.xor_group MUST have at least 2 members (M.3.13 — a
      single-member XOR group is degenerate: "exactly one of one
      option happens" trivially holds, so the declaration is a typo
      or leftover from a deletion).
  C5. Every Chain parent MUST have at least one ``required=True`` child
      OR at least one ``xor_group``-tagged child (X.1.j — an
      all-optional chain encodes no enforceable obligation; the chain
      mechanism's whole point is "if X fires, Y must follow", and an
      all-optional declaration makes Y's firing unobservable as a
      constraint. Surfaces as a "No Required Children" branch in the
      L2FT Chains dashboard's completion_status — caught at load so the
      dashboard never has to advertise a meaningless filter value).

  S1. A two-leg Rail that is NOT a TransferTemplate leg MUST have
      ``expected_net`` set.
  S2. A two-leg Rail that IS a TransferTemplate leg MUST NOT have
      ``expected_net`` set (the template owns the bundle's ExpectedNet).
  S3. Every NON-aggregating single-leg Rail MUST be reconciled — appears
      in some TransferTemplate.leg_rails OR some aggregating Rail's
      bundles_activity (matched by Rail.name OR Rail.transfer_type).
      Aggregating single-leg rails are exempt — they ARE the
      reconciliation mechanism (per SPEC's "single-leg sweep that lands
      in an external counterparty" example).
  S4. Aggregating Rails MUST NOT appear as Chain.child.
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
    ChainEntry,
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
    _check_unique_rail_discriminators(instance)
    _check_unique_transfer_template_names(instance)
    _check_unique_limit_schedule_combinations(instance)

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
    _check_chain_endpoints_exist(instance, rail_names, template_names)
    _check_limit_schedule_parent_role_resolves(instance, all_roles)
    _check_template_leg_rails_are_non_aggregating(instance, rails_by_name)
    _check_max_unbundled_age_only_on_bundled_rails(instance)
    _check_dotted_bundle_selectors_resolve(instance)
    _check_limit_schedule_transfer_type_has_rail(instance)
    _check_bare_bundles_activity_selectors_resolve(instance)
    _check_transfer_key_in_leg_rail_metadata_keys(instance, rails_by_name)
    _check_metadata_value_example_keys_resolve(instance)

    _check_variable_leg_count_per_template(instance)
    _check_chain_xor_group_consistency(instance)
    _check_variable_single_leg_in_some_template(instance, rails_by_name)
    _check_xor_group_min_members(instance)
    _check_chain_parent_has_required_or_xor(instance)

    _check_two_leg_expected_net_consistency(instance)
    _check_single_leg_reconciliation(instance)
    _check_chain_aggregating_not_child(instance)
    _check_aggregating_rail_required_fields(instance)
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


def _check_unique_rail_discriminators(inst: L2Instance) -> None:
    """P.9b — Rail uniqueness on the per-leg ``(transfer_type, role)``
    discriminator.

    The Rail-to-Transaction binding is implicit: a posted Transaction's
    ``(transfer_type, account_role)`` tuple identifies which Rail
    produced it. Two rails sharing the same discriminator for any leg
    are silently ambiguous — a candidate Transaction matches both with
    no defined tiebreak.

    Per-leg discriminator:
    - ``TwoLegRail``: contributes two — ``(transfer_type, source_role)``
      and ``(transfer_type, destination_role)``.
    - ``SingleLegRail``: contributes one — ``(transfer_type, leg_role)``.

    Role expressions that are tuples (rail can fan out to multiple
    accounts of the same role) contribute one discriminator per role.

    Direction is intentionally NOT in the discriminator: forcing
    uniqueness on ``(transfer_type, role)`` alone surfaces two-rail-
    per-direction patterns (e.g. CustomerInboundACH + CustomerOutboundACH
    sharing ``ach`` + ``CustomerDDA`` with swapped source/destination),
    which the SPEC says should be modeled as either one bidirectional
    rail or two distinct transfer_types.
    """
    seen: dict[tuple[str, str], str] = {}
    for rail in inst.rails:
        rail_name = str(rail.name)
        transfer_type = str(rail.transfer_type)
        match rail:
            case TwoLegRail(source_role=src, destination_role=dst):
                roles: tuple[str, ...] = (
                    *_expand_role_expr(src),
                    *_expand_role_expr(dst),
                )
            case SingleLegRail(leg_role=leg):
                roles = _expand_role_expr(leg)
        # Dedupe within-rail repetitions: a rail with the same role on
        # both legs (or a union with duplicates) is fine — both legs of
        # one Transfer share a transfer_id, no cross-rail ambiguity.
        for role in set(roles):
            key = (transfer_type, role)
            if key in seen and seen[key] != rail_name:
                raise L2ValidationError(
                    f"Rail uniqueness violation: rail {rail_name!r} and "
                    f"rail {seen[key]!r} both contribute discriminator "
                    f"(transfer_type={transfer_type!r}, role={role!r}). "
                    f"A posted Transaction matching this discriminator "
                    f"would be ambiguous between the two rails.\n"
                    f"Resolve by either (a) using distinct transfer_type "
                    f"values per direction (e.g. ach_inbound / ach_outbound), "
                    f"(b) merging into a single bidirectional rail, or "
                    f"(c) chaining the two via a TransferTemplate."
                )
            seen[key] = rail_name


def _expand_role_expr(expr: object) -> tuple[str, ...]:
    """Normalize a RoleExpression into a tuple of role-name strings.

    RoleExpression is either an Identifier (single role) or a tuple
    of Identifiers (union of roles, e.g. a rail that fans out to
    multiple destination accounts of the same role family).
    """
    if isinstance(expr, tuple):
        return tuple(
            str(e)  # type: ignore[reportUnknownArgumentType]: expr is recursive Identifier-or-tuple; e is Identifier here
            for e in expr  # type: ignore[reportUnknownVariableType]: expr is recursive Identifier-or-tuple; e is Identifier here
        )
    return (str(expr),)


def _check_unique_limit_schedule_combinations(inst: L2Instance) -> None:
    """U5: each (parent_role, transfer_type) pair appears at most once.

    Per SPEC: duplicate combinations are a load-time configuration error
    (the projection into ``StoredBalance.Limits`` would be ambiguous —
    which cap wins?).
    """
    seen: dict[tuple[Identifier, str], int] = {}
    for i, ls in enumerate(inst.limit_schedules):
        key = (ls.parent_role, ls.transfer_type)
        if key in seen:
            raise L2ValidationError(
                f"limit_schedules[{i}]: duplicate "
                f"(parent_role={ls.parent_role!r}, "
                f"transfer_type={ls.transfer_type!r}) — also declared at "
                f"limit_schedules[{seen[key]}]"
            )
        seen[key] = i


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


def _check_chain_endpoints_exist(
    inst: L2Instance,
    rail_names: set[Identifier],
    template_names: set[Identifier],
) -> None:
    """R5: every Chain.parent and Chain.child resolves to a Rail or Template."""
    valid = rail_names | template_names
    for i, c in enumerate(inst.chains):
        if c.parent not in valid:
            raise L2ValidationError(
                f"chains[{i}].parent={c.parent!r}: not a declared Rail "
                f"or TransferTemplate name"
            )
        if c.child not in valid:
            raise L2ValidationError(
                f"chains[{i}].child={c.child!r}: not a declared Rail "
                f"or TransferTemplate name"
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
    """
    bundled: set[Identifier] = set()
    bundled_transfer_types: set[str] = set()
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
                # Bare identifier — could be a TransferType, RailName, or
                # TransferTemplateName at runtime. Match all 3 here so the
                # watch is "satisfied" by any plausible resolution.
                bundled.add(Identifier(sel_str))
                bundled_transfer_types.add(sel_str)
    for r in inst.rails:
        if r.max_unbundled_age is None:
            continue
        if r.name in bundled or r.transfer_type in bundled_transfer_types:
            continue
        raise L2ValidationError(
            f"Rail {r.name!r}: max_unbundled_age is set but no aggregating "
            f"Rail bundles this rail (neither the rail name {r.name!r} nor "
            f"its transfer_type {r.transfer_type!r} appears in any "
            f"bundles_activity); the watch can never fire"
        )


def _check_limit_schedule_transfer_type_has_rail(inst: L2Instance) -> None:
    """R10: every LimitSchedule.transfer_type matches some Rail.transfer_type.

    Per M.2d.1: a cap declared against a transfer_type that no Rail
    emits is a no-op — the limit-breach matview's CASE branches key
    off the rail's transfer_type, so a typo'd cap never fires. Caught
    at YAML load.
    """
    rail_transfer_types = {r.transfer_type for r in inst.rails}
    for i, ls in enumerate(inst.limit_schedules):
        if ls.transfer_type not in rail_transfer_types:
            raise L2ValidationError(
                f"limit_schedules[{i}].transfer_type={ls.transfer_type!r}: "
                f"no declared Rail emits this transfer_type "
                f"(declared: {sorted(rail_transfer_types)!r}). The cap "
                f"would silently never fire."
            )


def _check_bare_bundles_activity_selectors_resolve(inst: L2Instance) -> None:
    """R11: every bare-form bundles_activity selector resolves.

    Per M.2d.1: a bare-form selector (``<name>``, not ``Template.LegRail``)
    must match either a declared Rail.name OR some declared
    Rail.transfer_type. Otherwise the bundler matches nothing and the
    aggregating rail silently never sweeps. R8 (max_unbundled_age set
    ⇒ rail must be bundled) and R9 (dotted form ⇒ template + leg
    actually exist) cover the inverse and the dotted form respectively;
    this rule catches typos in the bare form.
    """
    rail_names = {r.name for r in inst.rails}
    rail_transfer_types = {r.transfer_type for r in inst.rails}
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
            if sel_str in rail_transfer_types:
                continue
            raise L2ValidationError(
                f"Rail {r.name!r}.bundles_activity: bare selector "
                f"{sel_str!r} resolves to neither a declared Rail.name "
                f"nor any declared Rail.transfer_type "
                f"(rail names: {sorted(rail_names)!r}; transfer_types: "
                f"{sorted(rail_transfer_types)!r}). The bundler would "
                f"silently match nothing."
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
    """C1: at most one LegDirection=Variable leg per TransferTemplate."""
    rails_by_name: dict[str, Rail] = {r.name: r for r in inst.rails}
    for t in inst.transfer_templates:
        variable_legs = [
            n for n in t.leg_rails
            if isinstance(rails_by_name.get(n), SingleLegRail)
            and isinstance(rails_by_name[n], SingleLegRail)
            and rails_by_name[n].leg_direction == "Variable"  # type: ignore[union-attr]: narrowed by the prior isinstance(..., SingleLegRail) check
        ]
        if len(variable_legs) > 1:
            raise L2ValidationError(
                f"TransferTemplate {t.name!r}: contains {len(variable_legs)} "
                f"Variable-direction legs ({variable_legs!r}); SPEC requires "
                f"at most one (otherwise closure is under-determined)"
            )


def _check_chain_xor_group_consistency(inst: L2Instance) -> None:
    """C2: every XorGroup's members share the same Chain.parent."""
    parents_by_xor: dict[str, set[str]] = {}
    for c in inst.chains:
        if c.xor_group is None:
            continue
        parents_by_xor.setdefault(c.xor_group, set()).add(c.parent)
    for xor_group, parents in parents_by_xor.items():
        if len(parents) > 1:
            raise L2ValidationError(
                f"xor_group {xor_group!r}: members reference different "
                f"parents {sorted(parents)!r}; all members of an XOR "
                f"group MUST share the same parent"
            )


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


def _check_xor_group_min_members(inst: L2Instance) -> None:
    """C4: every Chain.xor_group MUST have at least 2 members.

    A single-member XOR group is degenerate — "exactly one of one
    option happens" trivially holds whenever the parent fires, so the
    declaration adds no constraint. In practice this is a typo (the
    second member was deleted, or its xor_group string disagrees) or
    a leftover from an editing pass. Caught at load so the misconfig
    can't silently weaken the dashboard's XOR-violation detection.
    """
    member_count_by_group: dict[str, int] = {}
    for c in inst.chains:
        if c.xor_group is None:
            continue
        key = str(c.xor_group)
        member_count_by_group[key] = member_count_by_group.get(key, 0) + 1
    for group, count in member_count_by_group.items():
        if count < 2:
            raise L2ValidationError(
                f"xor_group {group!r}: has only {count} member; XOR "
                f"groups MUST have at least 2 members (a single-member "
                f"group is degenerate — 'exactly one of one option' "
                f"trivially holds)"
            )


def _check_chain_parent_has_required_or_xor(inst: L2Instance) -> None:
    """C5: every chain parent MUST have at least one Required child OR
    at least one xor_group-tagged child.

    The chain mechanism encodes "if X fires, Y must follow" — an
    all-optional chain (no required children, no XOR groups) makes Y's
    firing unobservable as a constraint, so the declaration adds
    nothing the dashboard can surface as a violation. In practice this
    is a typo or a leftover from an editing pass that flipped every
    child to ``required = False`` without re-reading the implied
    contract. Caught at load so the L2FT Chains dashboard never has to
    advertise a meaningless 'No Required Children' filter value.
    """
    children_by_parent: dict[str, list[ChainEntry]] = {}
    for c in inst.chains:
        children_by_parent.setdefault(str(c.parent), []).append(c)
    for parent, children in children_by_parent.items():
        any_required = any(c.required for c in children)
        any_xor = any(c.xor_group is not None for c in children)
        if not any_required and not any_xor:
            raise L2ValidationError(
                f"Chain parent {parent!r}: declares {len(children)} "
                f"children, none required and none in an xor_group. The "
                f"chain encodes no enforceable obligation — flag at "
                f"least one child ``required = True`` or group two or "
                f"more children with an ``xor_group`` to make the "
                f"chain mean something."
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
        in_aggregating = (
            r.name in aggregating_bundles
            or r.transfer_type in aggregating_bundles
        )
        if not (in_template or in_aggregating):
            raise L2ValidationError(
                f"Rail {r.name!r}: single-leg rail is not reconciled "
                f"(not listed in any TransferTemplate.leg_rails AND "
                f"its name + transfer_type {r.transfer_type!r} not "
                f"matched by any aggregating Rail's bundles_activity); "
                f"the drift it introduces would persist forever"
            )


def _check_chain_aggregating_not_child(inst: L2Instance) -> None:
    """S4: aggregating Rails MUST NOT appear as Chain.child."""
    aggregating_names = {r.name for r in inst.rails if r.aggregating}
    for i, c in enumerate(inst.chains):
        if c.child in aggregating_names:
            raise L2ValidationError(
                f"chains[{i}].child={c.child!r}: aggregating Rails MUST NOT "
                f"appear as Chain.child (they sweep on cadence, not on a "
                f"per-Transfer parent trigger)"
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
