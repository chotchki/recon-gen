"""BTa.8 — ETL-feed gap plants for the bundled-demo path.

When the operator runs Studio without an ``etl_hook`` configured,
the synthetic-data generator produces clean, L2-conformant rows.
That's correct for production deployments — but it leaves the
``/etl/triage`` page empty, so the BTa.4 accordion / volume
badges / per-kind color stripes never get exercised in the demo
experience.

This module exposes one plant function per demo-surfaced ETL
trust-killer, mirroring how ``add_broken_rail_plants`` /
``boost_inv_fanout_plants`` in ``auto_scenario.py`` cover the
L1-invariant violation kinds. Each function below produces SQL
for ONE plant kind; ``emit_demo_etl_gap_sql`` composes them.

Two surfaces, two failure shapes:

    Plant kind                    Surface          Function
    ----------------------------- ---------------- ----------------------------------
    phantom rail (INSERT)         Triage           add_phantom_rail_gap_rows
    phantom template (INSERT)     Triage           add_phantom_template_gap_rows
    missing metadata (INSERT)     Triage           add_missing_metadata_gap_rows
    uncovered rail (DELETE)       Coverage         add_uncovered_rail_gap_rows
    uncovered template (DELETE)   Coverage         add_uncovered_template_gap_rows
    missing_limit_schedule        (NOT planted)    — see note below

Cold-read v3 finding: planting only Triage gaps (the INSERT kind)
left Coverage all-green while Triage went red. The operator's
mental model says "if Triage has gaps, Coverage should reflect
the brokenness too" — otherwise a glance at Coverage misleads.
DELETE-based plants surgically empty one L2 entity's transaction
set so its Coverage card flips to ✗ ("declared but no rows").

The ``missing_limit_schedule`` gap kind isn't planted here because
it's an L2-shape gap (rail/role pair without a schedule), not a
transaction-stream gap; you can't synthesize it via inserts.

Architectural note: L1-invariant plants ship as ``ScenarioPlant``
dataclass entries that flow through ``emit_full_seed``'s emitter
pipeline. ETL-feed gaps live in the transaction stream's *contract*
layer (not the invariant layer), so they ship as raw INSERT overlays
invoked AFTER the generator finishes — same conceptual "plant"
pattern (deterministic, demo-only, lights up a specific dashboard
surface), different integration point.

Gated by ``cfg.etl_hook is None`` at the studio call site — a real
operator's ETL feed is the source of truth; demo overlay would
corrupt it. Locked seeds are unaffected because they're built from
the generator pipeline directly, never via this overlay.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from recon_gen.common.l2 import L2Instance
from recon_gen.common.sql.literals import sql_timestamp_literal as _sql_timestamp_literal
from recon_gen.common.sql.dialect import Dialect


# Visible-on-the-page string prefix so an operator browsing
# transactions can spot demo gaps at a glance + grep them out.
DEMO_GAP_ID_PREFIX = "__demo_gap_"

# Phantom values chosen to be obviously fake AND to mimic the kinds
# of strings that would land in a real ETL feed from a legacy /
# misconfigured source — `legacy_card_swipe` reads like a name an
# operator might actually see, not a random string.
PHANTOM_RAIL_NAME = "legacy_card_swipe"
PHANTOM_TEMPLATE_NAME = "orphan_settlement_batch"

# Sentinel account_id reused across every gap row. No FK on
# transactions.account_id, so any string works; using one stable
# value makes the gaps clearly co-located when the operator
# inspects raw transactions.
_DEMO_ACCOUNT_ID = "__demo_gap_account__"


def emit_demo_etl_gap_sql(
    instance: L2Instance,
    *,
    prefix: str,
    dialect: Dialect,
    anchor: datetime | None = None,
) -> str:
    """Compose all demo ETL-feed gap plants into one SQL overlay.

    Calls the three per-kind plant functions in order, joins the
    output, returns a single string of semicolon-terminated INSERTs
    safe to ``execute_script`` against any supported dialect.

    ``anchor`` controls the wall-clock posting timestamp; defaults
    to ``datetime.now()`` so freshly-planted gaps surface in the
    default Triage / Probe windows. Pass an explicit datetime in
    tests for byte-stability.

    Composition matches ``build_default_scenario`` in
    ``cli/_helpers.py`` (which chains ``default_scenario_for`` →
    ``densify_scenario`` → ``add_broken_rail_plants`` →
    ``boost_inv_fanout_plants``): each plant kind is its own
    function, called in a fixed sequence.
    """
    if anchor is None:
        anchor = datetime.now()  # typing-smell: ignore[no-datetime-now]: demo overlay uses the wall clock so freshly-planted rows fall in the operator's default date window; tests pass an explicit anchor.

    parts: list[str] = []
    parts.append(add_phantom_rail_gap_rows(
        prefix=prefix, dialect=dialect, anchor=anchor, count=3,
    ))
    parts.append(add_phantom_template_gap_rows(
        prefix=prefix, dialect=dialect, anchor=anchor, count=2,
    ))
    md_sql = add_missing_metadata_gap_rows(
        instance, prefix=prefix, dialect=dialect, anchor=anchor,
    )
    if md_sql:
        parts.append(md_sql)
    # Coverage-side plants (DELETE) — flip one rail + one template
    # to ✗ on the Coverage card so the operator's glance reflects
    # the same brokenness Triage shows.
    rail_del = add_uncovered_rail_gap_rows(
        instance, prefix=prefix, dialect=dialect,
    )
    if rail_del:
        parts.append(rail_del)
    tmpl_del = add_uncovered_template_gap_rows(
        instance, prefix=prefix, dialect=dialect,
    )
    if tmpl_del:
        parts.append(tmpl_del)
    return "\n".join(parts) + "\n"


# -- per-gap-kind plant functions --------------------------------------------


def add_phantom_rail_gap_rows(
    *,
    prefix: str,
    dialect: Dialect,
    anchor: datetime,
    count: int = 3,
    rail_name: str = PHANTOM_RAIL_NAME,
) -> str:
    """Plant ``count`` transactions whose ``rail_name`` doesn't
    resolve in the L2.

    Triage's ``_detect_unmatched_rails`` picks these up + renders an
    ``unmatched_rail`` accordion section with the volume badge
    showing ``<count> rows``. The default phantom name
    (``legacy_card_swipe``) is chosen to read like a plausible
    legacy rail an integrator might forget to declare — not a
    random sentinel.

    BU.1 — ``rail_name`` is now operator-overridable so the Trainer
    plant page can pre-populate it from the registry and let the
    operator type a custom name for the demo / cold-read scenario.
    Defaults to the bundled-demo sentinel for backward compatibility
    with the BTa.8 overlay caller.

    Parallels ``add_broken_rail_plants`` in ``auto_scenario.py``
    (single plant kind, parameterized count, deterministic output).
    """
    return "\n".join(
        _insert_phantom_rail_row(
            row_idx=i, prefix=prefix, dialect=dialect, anchor=anchor,
            rail_name=rail_name,
        )
        for i in range(count)
    )


def add_phantom_template_gap_rows(
    *,
    prefix: str,
    dialect: Dialect,
    anchor: datetime,
    count: int = 2,
) -> str:
    """Plant ``count`` transactions whose ``template_name`` doesn't
    resolve in the L2.

    Triage's ``_detect_unmatched_templates`` picks these up + renders
    an ``unmatched_template`` accordion section. ``rail_name`` on
    these rows is a separate phantom so the row contributes to
    template detection but doesn't double-count with the rail plant.
    """
    return "\n".join(
        _insert_phantom_template_row(
            row_idx=i, prefix=prefix, dialect=dialect, anchor=anchor,
        )
        for i in range(count)
    )


def add_uncovered_rail_gap_rows(
    instance: L2Instance,
    *,
    prefix: str,
    dialect: Dialect,
) -> str:
    """Empty all transactions for one L2-declared rail (DELETE).

    The Coverage card for Rails then renders that rail as ✗
    (`declared but no rows`). Picks the alphabetically-last rail
    deterministically — a stable, demo-clear choice that doesn't
    depend on row counts. Returns empty string when the L2 declares
    no rails (vacuous deployment).

    Parallels ``add_phantom_rail_gap_rows`` on the Triage side:
    same demo-shaping intent, opposite DML (INSERT vs DELETE).
    """
    rail_names = sorted(str(r.name) for r in instance.rails)
    if not rail_names:
        return ""
    target = rail_names[-1]
    return (
        f"DELETE FROM {prefix}_transactions "
        f"WHERE rail_name = '{_sql_escape(target)}';"
    )


def add_uncovered_template_gap_rows(
    instance: L2Instance,
    *,
    prefix: str,
    dialect: Dialect,
) -> str:
    """Empty all transactions for one L2-declared template (DELETE).

    The Coverage card for Templates then renders that template as ✗.
    Picks the alphabetically-last template deterministically.
    Returns empty string when the L2 declares no templates.
    """
    tmpl_names = sorted(str(t.name) for t in instance.transfer_templates)
    if not tmpl_names:
        return ""
    target = tmpl_names[-1]
    return (
        f"DELETE FROM {prefix}_transactions "
        f"WHERE template_name = '{_sql_escape(target)}';"
    )


def _sql_escape(value: str) -> str:
    """Single-quote escape for SQL string literals. The names come
    from L2 yaml (operator-controlled) so a defensive escape keeps
    SQL injection out — even though the L2 loader already validates
    Identifier shape."""
    return value.replace("'", "''")


def add_missing_metadata_gap_rows(
    instance: L2Instance,
    *,
    prefix: str,
    dialect: Dialect,
    anchor: datetime,
) -> str:
    """Plant one transaction whose ``template_name`` resolves but
    whose metadata omits the template's required ``transfer_key``.

    Triage's ``_detect_missing_metadata_keys`` picks this up +
    renders a ``missing_metadata_key`` accordion section. Returns
    empty string when the L2 has no template with a
    ``transfer_key`` — the gap can't be synthesized without a
    target template that declares one.
    """
    sql = _insert_missing_metadata_row(
        instance=instance, prefix=prefix,
        dialect=dialect, anchor=anchor,
    )
    return sql if sql is not None else ""


# -- BU.3 L2FT Hygiene plants (Trainer needs-build) --------------------------
#
# Four plants flipping the L2FT L2 Hygiene Exceptions sheet's per-kind row
# count above zero. Surface SQL via the same INSERT / DELETE shape the
# Triage gaps use; the L2FT dataset SQL re-runs at every dashboard query
# (no matview to refresh).


def add_chain_orphan_gap_rows(
    instance: L2Instance,
    *,
    prefix: str,
    dialect: Dialect,
    anchor: datetime,
    count: int = 3,
) -> str:
    """Plant ``count`` parent firings for a Required (singleton-children,
    non-fan_in) chain WITHOUT matching child firings.

    Picks the alphabetically-first Required chain (singleton children,
    fan_in=False on the only child). Inserts ``count`` rows whose
    ``rail_name`` is the chain's parent identifier; no row references
    these as a ``transfer_parent_id``, so the L2FT
    ``build_exc_chain_orphans_dataset`` check fires
    (``parent_firing_count > child_firing_count``).

    Returns empty string when the L2 declares no Required chain —
    the BU.3 picker's "satisfiability" guard.
    """
    target = _pick_required_chain_parent(instance)
    if target is None:
        return ""
    return "\n".join(
        _insert_chain_orphan_parent_row(
            row_idx=i, prefix=prefix, dialect=dialect, anchor=anchor,
            parent_name=target,
        )
        for i in range(count)
    )


def add_dead_bundles_activity_gap_rows(
    instance: L2Instance,
    *,
    prefix: str,
    dialect: Dialect,
) -> str:
    """Empty all transactions for one L2-declared bundle_target (DELETE).

    Picks the alphabetically-first (aggregating_rail, bundle_target)
    pair declared on an L2 rail's ``bundles_activity``; deletes every
    row whose ``rail_name = bundle_target`` so the
    ``build_exc_dead_bundles_activity_dataset`` check's NOT EXISTS
    matches. Returns empty string when no rail declares
    ``bundles_activity``.
    """
    target = _pick_dead_bundle_target(instance)
    if target is None:
        return ""
    return (
        f"DELETE FROM {prefix}_transactions "
        f"WHERE rail_name = '{_sql_escape(target)}';"
    )


def add_dead_metadata_gap_rows(
    instance: L2Instance,
    *,
    prefix: str,
    dialect: Dialect,
) -> str:
    """Empty all transactions for one L2-declared (rail, metadata_key)
    pair (DELETE).

    The L2FT ``dead_metadata`` check fires per (rail, metadata_key)
    declared on an L2 Rail when no posting on that rail carries a
    non-null value for the key. Simplest plant: DELETE every row on the
    chosen rail — the NOT EXISTS guard then matches for every key the
    rail declared (rail-level), surfacing one or more rows on the L2FT
    Hygiene Exceptions sheet's Dead Metadata Declarations branch.

    Picks the alphabetically-first rail with non-empty
    ``metadata_keys``. Returns empty string when no rail declares any.
    """
    target = _pick_rail_with_metadata_keys(instance)
    if target is None:
        return ""
    return (
        f"DELETE FROM {prefix}_transactions "
        f"WHERE rail_name = '{_sql_escape(target)}';"
    )


def add_dead_limit_schedule_gap_rows(
    instance: L2Instance,
    *,
    prefix: str,
    dialect: Dialect,
) -> str:
    """Empty all Debit transactions for one L2-declared LimitSchedule
    cell (DELETE).

    The L2FT ``dead_limit_schedules`` check fires when no Debit posting
    matches (parent_role, rail_name). Picks the alphabetically-first
    (parent_role, rail_name) cell declared in
    ``instance.limit_schedules`` + deletes every matching Debit row.
    Returns empty string when no LimitSchedule is declared.
    """
    target = _pick_dead_limit_schedule_cell(instance)
    if target is None:
        return ""
    parent_role, rail_name = target
    return (
        f"DELETE FROM {prefix}_transactions "
        f"WHERE account_parent_role = '{_sql_escape(parent_role)}' "
        f"AND rail_name = '{_sql_escape(rail_name)}' "
        f"AND amount_direction = 'Debit';"
    )


# -- BU.3 pickers (deterministic, alphabetical-first by convention) ----------


def _pick_required_chain_parent(instance: L2Instance) -> str | None:
    """First chain that's singleton-children + non-fan_in (== Required
    semantics per the L2 grammar Z.A: ``len(children) == 1`` encodes
    "child SHOULD fire when parent does"). Returns the parent identifier
    (a rail name OR a template name; the L2FT chain_orphans dataset
    resolves both via ``COALESCE(template_name, rail_name)``).
    """
    candidates: list[str] = []
    for chain in instance.chains:
        if len(chain.children) != 1:
            continue
        if chain.children[0].fan_in:
            continue
        candidates.append(str(chain.parent))
    if not candidates:
        return None
    return sorted(candidates)[0]


def _pick_dead_bundle_target(instance: L2Instance) -> str | None:
    """First (rail, bundle_target) pair where the rail declares
    ``bundles_activity``. Returns the bundle_target identifier (the
    rail_name to empty), not the aggregating rail itself. Sorted by
    (rail_name, bundle_target) for determinism.
    """
    candidates: list[tuple[str, str]] = []
    for r in instance.rails:
        for ref in r.bundles_activity:
            candidates.append((str(r.name), str(ref)))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def _pick_rail_with_metadata_keys(instance: L2Instance) -> str | None:
    """First rail (alphabetical) whose ``metadata_keys`` is non-empty.
    Returns the rail name. None when no rail declares any keys.
    """
    candidates: list[str] = []
    for r in instance.rails:
        if r.metadata_keys:
            candidates.append(str(r.name))
    if not candidates:
        return None
    return sorted(candidates)[0]


def _pick_dead_limit_schedule_cell(
    instance: L2Instance,
) -> tuple[str, str] | None:
    """First (parent_role, rail_name) LimitSchedule cell (alphabetical).
    Direction is ignored — the dead-schedule check filters on
    ``amount_direction = 'Debit'`` regardless, matching the
    out-of-the-box Outbound default.
    """
    candidates: list[tuple[str, str]] = [
        (str(s.parent_role), str(s.rail))
        for s in instance.limit_schedules
    ]
    if not candidates:
        return None
    candidates.sort()
    return candidates[0]


def _insert_chain_orphan_parent_row(
    *,
    row_idx: int,
    prefix: str,
    dialect: Dialect,
    anchor: datetime,
    parent_name: str,
) -> str:
    """One synthetic parent firing for the chain-orphan plant. The row
    carries ``rail_name = parent_name`` so the L2FT chain_orphans CTE's
    ``COALESCE(template_name, rail_name) = d.parent_name`` join matches.
    ``transfer_parent_id`` is left null — no parent edge on this row.
    No CHILD row is planted: that's the whole point — parent fires,
    child doesn't, orphan_count > 0.
    """
    posting = (anchor - timedelta(hours=row_idx + 1)).isoformat(timespec="seconds")
    ts_lit = _sql_timestamp_literal(posting, dialect)
    tx_id = f"{DEMO_GAP_ID_PREFIX}chain_orphan_{row_idx:03d}"
    xfer_id = f"{DEMO_GAP_ID_PREFIX}chain_orphan_xfer_{row_idx:03d}"
    return (
        f"INSERT INTO {prefix}_transactions ("
        "id, account_id, account_role, account_scope, "
        "amount_money, amount_direction, status, posting, "
        "transfer_id, rail_name, origin, metadata"
        ") VALUES ("
        f"'{tx_id}', '{_DEMO_ACCOUNT_ID}', 'DemoGap', 'external', "
        f"100, 'Credit', 'Posted', {ts_lit}, "
        f"'{xfer_id}', '{_sql_escape(parent_name)}', 'DemoOverlay', "
        "'{}');"
    )


# -- per-row builders (internal) ---------------------------------------------


def _insert_phantom_rail_row(
    *,
    row_idx: int,
    prefix: str,
    dialect: Dialect,
    anchor: datetime,
    rail_name: str = PHANTOM_RAIL_NAME,
) -> str:
    posting = (anchor - timedelta(hours=row_idx)).isoformat(timespec="seconds")
    ts_lit = _sql_timestamp_literal(posting, dialect)
    tx_id = f"{DEMO_GAP_ID_PREFIX}phantom_rail_{row_idx:03d}"
    xfer_id = f"{DEMO_GAP_ID_PREFIX}phantom_rail_xfer_{row_idx:03d}"
    return (
        f"INSERT INTO {prefix}_transactions ("
        "id, account_id, account_role, account_scope, "
        "amount_money, amount_direction, status, posting, "
        "transfer_id, rail_name, origin, metadata"
        ") VALUES ("
        f"'{tx_id}', '{_DEMO_ACCOUNT_ID}', 'DemoGap', 'external', "
        f"100, 'Credit', 'Posted', {ts_lit}, "
        f"'{xfer_id}', '{_sql_escape(rail_name)}', 'DemoOverlay', "
        "'{}');"
    )


def _insert_phantom_template_row(
    *, row_idx: int, prefix: str, dialect: Dialect, anchor: datetime,
) -> str:
    posting = (anchor - timedelta(hours=row_idx + 5)).isoformat(timespec="seconds")
    ts_lit = _sql_timestamp_literal(posting, dialect)
    tx_id = f"{DEMO_GAP_ID_PREFIX}phantom_tmpl_{row_idx:03d}"
    xfer_id = f"{DEMO_GAP_ID_PREFIX}phantom_tmpl_xfer_{row_idx:03d}"
    # Use a real rail name (first one in the L2) so the row's
    # rail_name resolves cleanly — the gap is on template_name only.
    return (
        f"INSERT INTO {prefix}_transactions ("
        "id, account_id, account_role, account_scope, "
        "amount_money, amount_direction, status, posting, "
        "transfer_id, rail_name, template_name, origin, metadata"
        ") VALUES ("
        f"'{tx_id}', '{_DEMO_ACCOUNT_ID}', 'DemoGap', 'external', "
        f"50, 'Credit', 'Posted', {ts_lit}, "
        f"'{xfer_id}', '__demo_rail_for_tmpl_gap', "
        f"'{PHANTOM_TEMPLATE_NAME}', 'DemoOverlay', "
        "'{}');"
    )


def _insert_missing_metadata_row(
    *,
    instance: L2Instance,
    prefix: str,
    dialect: Dialect,
    anchor: datetime,
) -> str | None:
    """Plant a row with template_name set to a real template that
    declares a required metadata key, but with metadata that omits
    the key. Triage's `_detect_missing_metadata_keys` will catch it.

    Returns None when no template in the L2 declares a required
    metadata key — the gap can't be synthesized in that case.
    """
    target_template = _pick_template_with_required_metadata(instance)
    if target_template is None:
        return None

    posting = (anchor - timedelta(hours=10)).isoformat(timespec="seconds")
    ts_lit = _sql_timestamp_literal(posting, dialect)
    tx_id = f"{DEMO_GAP_ID_PREFIX}missing_md_000"
    xfer_id = f"{DEMO_GAP_ID_PREFIX}missing_md_xfer_000"
    return (
        f"INSERT INTO {prefix}_transactions ("
        "id, account_id, account_role, account_scope, "
        "amount_money, amount_direction, status, posting, "
        "transfer_id, rail_name, template_name, origin, metadata"
        ") VALUES ("
        f"'{tx_id}', '{_DEMO_ACCOUNT_ID}', 'DemoGap', 'external', "
        f"75, 'Credit', 'Posted', {ts_lit}, "
        f"'{xfer_id}', '__demo_rail_for_md_gap', "
        f"'{target_template}', 'DemoOverlay', "
        # Empty JSON object — every required metadata key is absent.
        "'{}');"
    )


def _pick_template_with_required_metadata(
    instance: L2Instance,
) -> str | None:
    """First transfer template (sorted by name) that has a
    ``transfer_key`` declared, since transfer_key is the canonical
    "required metadata key" the contract derivation flags as
    not_null. None when no template carries one.
    """
    candidates: list[str] = []
    for tmpl in instance.transfer_templates:
        if getattr(tmpl, "transfer_key", None):
            candidates.append(str(tmpl.name))
    if not candidates:
        return None
    return sorted(candidates)[0]
