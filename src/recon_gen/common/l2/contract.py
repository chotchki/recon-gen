"""Per-L2-primitive column contracts (BT.5 — derivation for BT.4 triage).

Pure-function derivation over a loaded ``L2Instance``. The output is a
typed map that names, per L2 entity, the rows on ``<prefix>_transactions``
that entity owns plus the per-column predicates those rows must satisfy.
BT.4's exception-triage surface walks the result against an observed
runtime snapshot and surfaces each gap as a card with a deep link to
the relevant editor page.

BT.0 lock 4 anchored the design call: the typed L2 primitives carry
enough to derive the contract — no new domain model, no DB-side work,
no new validator. This module exists so BT.4 doesn't have to re-walk
the primitives itself.

Severability: imports ``primitives`` only. The editor-path strings
mirror ``common/html/_studio_editor_routes.py::_entity_id`` (the route
shape's authoritative source) but the link target is just a string —
this module doesn't import the html layer.

The selectors + predicates are renderer-agnostic on purpose. BT.4's
SQL composer reads them; a unit test reads them; the same shape would
serve a CLI ``recon-gen probe`` if that ever lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from .primitives import (
    Identifier,
    L2Instance,
    LimitDirection,
    LimitSchedule,
    Money,
    Rail,
    RailName,
    SingleLegRail,
    TransferTemplate,
    TwoLegRail,
)


PredicateKind: TypeAlias = Literal["equals", "one_of", "not_null"]


@dataclass(frozen=True, slots=True)
class RowSelector:
    """Narrows ``<prefix>_transactions`` to the rows one contract owns.

    Always a column-equals-literal — the L2 primitives that own
    transactions rows (Rail / TransferTemplate) each project to exactly
    one selector column (``rail_name`` / ``template_name``).
    """

    column: str
    equals: str


@dataclass(frozen=True, slots=True)
class ColumnPredicate:
    """One per-column expectation on the rows selected by a contract.

    ``column`` is a plain transactions column name (``account_role``,
    ``amount_direction``, ``transfer_parent_id``) OR the dotted form
    ``metadata.<key>`` for a JSON metadata field. BT.4's SQL composer
    knows to translate the dotted form to a ``JSON_VALUE`` extraction.

    ``kind`` discriminates the predicate shape:

    - ``equals``      — ``column = expected`` (``expected`` is ``str``)
    - ``one_of``      — ``column IN expected`` (``expected`` is
      ``tuple[str, ...]``)
    - ``not_null``    — ``column IS NOT NULL`` (``expected`` is ``None``)
    """

    column: str
    kind: PredicateKind
    expected: object


@dataclass(frozen=True, slots=True)
class RailContract:
    """Per-Rail expectations on rows tagged ``rail_name = <name>``.

    ``leg_kind`` discriminates the source primitive so BT.4 can label
    the gap card ("Two-leg rail expects ...", "Single-leg rail
    expects ...") without re-walking the L2.
    """

    rail_name: Identifier
    leg_kind: Literal["two_leg", "single_leg"]
    selector: RowSelector
    predicates: tuple[ColumnPredicate, ...]
    editor_path: str


@dataclass(frozen=True, slots=True)
class TemplateContract:
    """Per-TransferTemplate expectations on rows tagged
    ``template_name = <name>``.

    ``leg_rail_names`` is retained on the side (rather than only as a
    ``one_of`` predicate over ``rail_name``) so BT.4 can render the
    template's per-leg fan-out — the predicate is the gate, the list
    is the explanation.
    """

    template_name: Identifier
    selector: RowSelector
    predicates: tuple[ColumnPredicate, ...]
    leg_rail_names: tuple[Identifier, ...]
    editor_path: str


@dataclass(frozen=True, slots=True)
class ChainEdgeContract:
    """Per-(parent, child) chain expectation.

    Z.A grammar: a chain with one child encodes *required* semantics
    (the child always fires under the parent); a chain with multiple
    children encodes *XOR* (exactly one fires per parent invocation).
    ``is_singleton`` carries that distinction so BT.4 can phrase the
    diagnosis appropriately ("missing required child" vs "missing XOR
    sibling fire").

    Singleton chains add ``transfer_parent_id NOT NULL`` as a row-level
    predicate — under the SPEC, every firing of a required child IS a
    chain firing, so ``transfer_parent_id`` is always populated.
    XOR chains do NOT add the predicate: the matview-level
    "exactly-one-fired" check lives in ``_v_config_chain_children``
    (BS.5), not at the row level.

    The chain has no single selector column on the transactions table
    (the chain identity is parent-name + child-name + transfer_parent_id
    linkage), so the contract omits ``selector`` — BT.4 composes the
    SQL against the rows tagged with the child name.
    """

    parent: Identifier
    child: Identifier
    is_singleton: bool
    fan_in: bool
    expected_parent_count: int | None
    predicates: tuple[ColumnPredicate, ...]
    editor_path: str


@dataclass(frozen=True, slots=True)
class LimitContract:
    """Per-(parent_role, rail, direction) cap.

    LimitSchedule is balance-side (aggregated over
    ``<prefix>_daily_balances``) rather than per-row; carrying it as a
    distinct contract kind keeps BT.4 from having to translate
    ``cap`` into a row-level predicate. The triage page renders limits
    in their own panel.
    """

    parent_role: Identifier
    rail: RailName
    direction: LimitDirection
    cap: Money
    editor_path: str


@dataclass(frozen=True, slots=True)
class ColumnContracts:
    """The full derivation result.

    All four collections are tuples (frozen + iteration-stable). Order
    mirrors the L2Instance declaration order so a test asserting on a
    specific contract's index stays deterministic across re-derivations
    of the same instance.
    """

    rails: tuple[RailContract, ...]
    templates: tuple[TemplateContract, ...]
    chain_edges: tuple[ChainEdgeContract, ...]
    limits: tuple[LimitContract, ...]


def derive_column_contracts(instance: L2Instance) -> ColumnContracts:
    """Derive per-L2-primitive column contracts from a loaded ``L2Instance``.

    Pure function. No I/O, no caching, no mutation of the input. Output
    is BT.4-consumable directly: for each entity, BT.4 SELECTs the
    rows matching the entity's selector then evaluates the predicates
    against the result set.

    Args:
      instance: The L2 model to derive against.

    Returns:
      A ``ColumnContracts`` with one entry per L2-declared Rail, one
      per TransferTemplate, one per (parent, child) chain edge, and
      one per LimitSchedule row.
    """
    chain_edges: list[ChainEdgeContract] = []
    for chain in instance.chains:
        is_singleton = len(chain.children) == 1
        # Match `_studio_editor_routes._entity_id`'s composite shape so
        # the deep link resolves against the same row the editor lookup
        # walks. Sorted children CSV — Z.A's address scheme.
        children_csv = ",".join(sorted(str(c.name) for c in chain.children))
        composite = f"{chain.parent}::{children_csv}"
        for child_spec in chain.children:
            predicates: list[ColumnPredicate] = []
            if is_singleton:
                predicates.append(
                    ColumnPredicate(
                        column="transfer_parent_id",
                        kind="not_null",
                        expected=None,
                    )
                )
            chain_edges.append(
                ChainEdgeContract(
                    parent=chain.parent,
                    child=child_spec.name,
                    is_singleton=is_singleton,
                    fan_in=child_spec.fan_in,
                    expected_parent_count=child_spec.expected_parent_count,
                    predicates=tuple(predicates),
                    editor_path=f"/l2_shape/chain/{composite}/edit",
                )
            )

    return ColumnContracts(
        rails=tuple(_rail_contract(r) for r in instance.rails),
        templates=tuple(
            _template_contract(t) for t in instance.transfer_templates
        ),
        chain_edges=tuple(chain_edges),
        limits=tuple(_limit_contract(limit) for limit in instance.limit_schedules),
    )


def _rail_contract(rail: Rail) -> RailContract:
    selector = RowSelector(column="rail_name", equals=str(rail.name))
    predicates: list[ColumnPredicate] = []

    if isinstance(rail, TwoLegRail):
        leg_kind: Literal["two_leg", "single_leg"] = "two_leg"
        # Source + destination roles cover both legs. Union + sort for
        # stable ordering across re-derivations.
        roles = sorted({str(r) for r in (*rail.source_role, *rail.destination_role)})
        predicates.append(
            ColumnPredicate(
                column="account_role", kind="one_of", expected=tuple(roles),
            )
        )
    else:
        assert isinstance(rail, SingleLegRail)  # narrow for pyright
        leg_kind = "single_leg"
        roles = sorted({str(r) for r in rail.leg_role})
        predicates.append(
            ColumnPredicate(
                column="account_role", kind="one_of", expected=tuple(roles),
            )
        )
        # Variable-direction = closing leg of a TransferTemplate; the
        # direction (Debit/Credit) is determined at posting time by the
        # template's ExpectedNet closure. No static predicate then.
        if rail.leg_direction != "Variable":
            predicates.append(
                ColumnPredicate(
                    column="amount_direction",
                    kind="equals",
                    expected=rail.leg_direction,
                )
            )

    for key in rail.metadata_keys:
        predicates.append(
            ColumnPredicate(
                column=f"metadata.{key}", kind="not_null", expected=None,
            )
        )

    return RailContract(
        rail_name=rail.name,
        leg_kind=leg_kind,
        selector=selector,
        predicates=tuple(predicates),
        editor_path=f"/l2_shape/rail/{rail.name}/edit",
    )


def _template_contract(template: TransferTemplate) -> TemplateContract:
    selector = RowSelector(column="template_name", equals=str(template.name))
    predicates: list[ColumnPredicate] = [
        ColumnPredicate(
            column="rail_name",
            kind="one_of",
            expected=tuple(sorted(str(r) for r in template.leg_rails)),
        ),
    ]
    # TransferKey fields MUST be present on every leg of the template's
    # shared Transfer — they're the grouping key. Auto-derived per the
    # SPEC; mirrored in derived.py::posted_requirements_for.
    for key in template.transfer_key:
        predicates.append(
            ColumnPredicate(
                column=f"metadata.{key}", kind="not_null", expected=None,
            )
        )
    return TemplateContract(
        template_name=template.name,
        selector=selector,
        predicates=tuple(predicates),
        leg_rail_names=tuple(template.leg_rails),
        editor_path=f"/l2_shape/transfer_template/{template.name}/edit",
    )


def _limit_contract(limit: LimitSchedule) -> LimitContract:
    composite = f"{limit.parent_role}::{limit.rail}::{limit.direction}"
    return LimitContract(
        parent_role=limit.parent_role,
        rail=limit.rail,
        direction=limit.direction,
        cap=limit.cap,
        editor_path=f"/l2_shape/limit_schedule/{composite}/edit",
    )
