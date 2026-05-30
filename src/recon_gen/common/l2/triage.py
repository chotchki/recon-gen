"""BT.4 — Exception triage gap detector.

``detect_gaps`` diffs ``derive_column_contracts(L2Instance)`` against
the observed runtime in ``<prefix>_transactions`` and surfaces typed
``Gap`` records the triage page renders as decision cards. Each gap
carries:

- ``kind`` — discriminator (unmatched rail / template / missing
  LimitSchedule / missing metadata key).
- ``diagnosis`` — operator-readable English (the card's headline).
- ``evidence`` — observed row count + (sometimes) a sample
  transaction id + extras like "L2 declares these rails: …".
- ``link_target`` — deep link to the relevant L2 editor list page
  (per BT.0 lock 5 — link-only v1; pre-fill of the create form is
  deferred until cold-read flags friction).

Severability: imports the L2 model + ColumnContracts + the async
pool protocol. No html/render dependency — the triage page consumes
typed ``Gap`` tuples and renders them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias, cast

from recon_gen.common.db import AsyncConnectionPool
from recon_gen.common.l2.contract import ColumnContracts
from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.sql.dialect import Dialect, column_name


GapKind: TypeAlias = Literal[
    "unmatched_rail",
    "unmatched_template",
    "missing_limit_schedule",
    "missing_metadata_key",
]


@dataclass(frozen=True, slots=True)
class GapEvidence:
    """Row-side context for a Gap card.

    ``row_count`` is the absolute number of rows exhibiting the gap.
    ``sample_transaction_id`` (when set) is one offending row's id so
    the operator can grep the source ETL log. ``extras`` carries
    free-form key-value pairs the card renders ("existing_rails":
    "ach_credit, ach_debit, …") — separate from the diagnosis prose
    so the render layer can format them as a sub-list rather than
    inline.
    """

    row_count: int
    sample_transaction_id: str | None = None
    extras: Mapping[str, str] = field(default_factory=lambda: cast(Mapping[str, str], {}))


@dataclass(frozen=True, slots=True)
class Gap:
    """One gap card.

    ``observed_value`` is the offending string for unmatched-name
    kinds (the rail_name / template_name that doesn't resolve); None
    for kinds where the gap is structural (e.g. missing_limit_schedule
    is a missing TUPLE, not a single bad value).
    """

    kind: GapKind
    diagnosis: str
    observed_value: str | None
    evidence: GapEvidence
    link_target: str


async def detect_gaps(
    pool: AsyncConnectionPool,
    prefix: str,
    instance: L2Instance,
    contracts: ColumnContracts,
    *,
    dialect: Dialect,
) -> tuple[Gap, ...]:
    """Run all four gap checks against the demo DB. Returns gaps in a
    stable order (kind, then offending value)."""
    gaps: list[Gap] = []
    gaps.extend(await _detect_unmatched_rails(pool, prefix, instance, dialect))
    gaps.extend(await _detect_unmatched_templates(pool, prefix, instance, dialect))
    gaps.extend(
        await _detect_missing_limit_schedules(pool, prefix, instance, dialect)
    )
    gaps.extend(
        await _detect_missing_metadata_keys(
            pool, prefix, contracts, dialect,
        )
    )
    return tuple(gaps)


# -- Gap kind 1: unmatched rail_name -----------------------------------------


async def _detect_unmatched_rails(
    pool: AsyncConnectionPool, prefix: str,
    instance: L2Instance, dialect: Dialect,
) -> list[Gap]:
    """Rows whose ``rail_name`` doesn't resolve to any L2-declared Rail."""
    declared = {str(r.name) for r in instance.rails}
    txns = f"{prefix}_transactions"
    rail_col = column_name("rail_name", dialect)
    rows = await _fetch_count_per_value(
        pool,
        f"SELECT {rail_col}, COUNT(*) FROM {txns} "
        f"WHERE {rail_col} IS NOT NULL "
        f"GROUP BY {rail_col} "
        f"ORDER BY {rail_col}",
        dialect,
    )
    sample_ids = await _fetch_sample_ids(
        pool, prefix, dialect,
        column=rail_col,
        values=[v for v, _ in rows if v not in declared],
    )
    declared_sorted = sorted(declared)
    declared_list = (
        ", ".join(declared_sorted) if declared_sorted else "(none)"
    )

    gaps: list[Gap] = []
    for value, count in rows:
        if value in declared:
            continue
        gaps.append(Gap(
            kind="unmatched_rail",
            diagnosis=(
                f"{count} rows arrived with rail_name=\"{value}\" but the "
                f"L2 declares no Rail of that name."
            ),
            observed_value=value,
            evidence=GapEvidence(
                row_count=count,
                sample_transaction_id=sample_ids.get(value),
                extras={"declared_rails": declared_list},
            ),
            link_target="/l2_shape/rail/",
        ))
    return gaps


# -- Gap kind 2: unmatched template_name -------------------------------------


async def _detect_unmatched_templates(
    pool: AsyncConnectionPool, prefix: str,
    instance: L2Instance, dialect: Dialect,
) -> list[Gap]:
    """Rows whose ``template_name`` doesn't resolve to any L2-declared
    TransferTemplate."""
    declared = {str(t.name) for t in instance.transfer_templates}
    txns = f"{prefix}_transactions"
    tmpl_col = column_name("template_name", dialect)
    rows = await _fetch_count_per_value(
        pool,
        f"SELECT {tmpl_col}, COUNT(*) FROM {txns} "
        f"WHERE {tmpl_col} IS NOT NULL "
        f"GROUP BY {tmpl_col} "
        f"ORDER BY {tmpl_col}",
        dialect,
    )
    sample_ids = await _fetch_sample_ids(
        pool, prefix, dialect,
        column=tmpl_col,
        values=[v for v, _ in rows if v not in declared],
    )
    declared_sorted = sorted(declared)
    declared_list = (
        ", ".join(declared_sorted) if declared_sorted else "(none)"
    )

    gaps: list[Gap] = []
    for value, count in rows:
        if value in declared:
            continue
        gaps.append(Gap(
            kind="unmatched_template",
            diagnosis=(
                f"{count} rows tagged with template_name=\"{value}\" — no "
                f"such template in the L2."
            ),
            observed_value=value,
            evidence=GapEvidence(
                row_count=count,
                sample_transaction_id=sample_ids.get(value),
                extras={"declared_templates": declared_list},
            ),
            link_target="/l2_shape/transfer_template/",
        ))
    return gaps


# -- Gap kind 3: missing LimitSchedule ---------------------------------------


async def _detect_missing_limit_schedules(
    pool: AsyncConnectionPool, prefix: str,
    instance: L2Instance, dialect: Dialect,
) -> list[Gap]:
    """``(account_parent_role, rail_name)`` combos firing in transactions
    that no LimitSchedule covers (in either direction).

    Per SPEC, LimitSchedule keys on (parent_role, rail, direction). The
    gap surfaces when rows fire for a (parent_role, rail) tuple
    without ANY LimitSchedule row for it — dashboards render the
    Limit Breach matview with "no cap" for these. Operator decides:
    add a schedule OR confirm "no cap" is the intended posture."""
    declared = {
        (str(ls.parent_role), str(ls.rail))
        for ls in instance.limit_schedules
    }
    txns = f"{prefix}_transactions"
    role_col = column_name("account_parent_role", dialect)
    rail_col = column_name("rail_name", dialect)
    rows_raw = await _fetch_two_value_count(
        pool,
        f"SELECT {role_col}, {rail_col}, COUNT(*) FROM {txns} "
        f"WHERE {role_col} IS NOT NULL AND {rail_col} IS NOT NULL "
        f"GROUP BY {role_col}, {rail_col} "
        f"ORDER BY {role_col}, {rail_col}",
        dialect,
    )
    gaps: list[Gap] = []
    for parent_role, rail, count in rows_raw:
        if (parent_role, rail) in declared:
            continue
        sibling_caps = sorted(
            f"({other_role}, {other_rail}, {ls.direction}) cap={ls.cap}"
            for ls in instance.limit_schedules
            for other_role, other_rail in [(str(ls.parent_role), str(ls.rail))]
            if other_role == parent_role
        )
        siblings_label = (
            "; ".join(sibling_caps) if sibling_caps
            else "(none for this parent_role)"
        )
        gaps.append(Gap(
            kind="missing_limit_schedule",
            diagnosis=(
                f"{count} {rail} rows landed against {parent_role} but no "
                f"LimitSchedule covers this (parent_role, rail) tuple. "
                f"L1 Limit Breach renders these as \"no cap\" in dashboards."
            ),
            observed_value=f"{parent_role}::{rail}",
            evidence=GapEvidence(
                row_count=count,
                extras={f"existing_schedules_for_{parent_role}": siblings_label},
            ),
            link_target="/l2_shape/limit_schedule/",
        ))
    return gaps


# -- Gap kind 4: missing required metadata key -------------------------------


async def _detect_missing_metadata_keys(
    pool: AsyncConnectionPool, prefix: str,
    contracts: ColumnContracts, dialect: Dialect,
) -> list[Gap]:
    """For each TransferTemplate contract, check that rows tagged with
    the template carry every required metadata key.

    Per BT.5's derivation, a TemplateContract's predicates include one
    ``metadata.<key> not_null`` per ``transfer_key`` field. Rows
    missing the key contribute to the gap count; sample id is the
    most-recent offender.
    """
    txns = f"{prefix}_transactions"
    tmpl_col = column_name("template_name", dialect)
    md_col = column_name("metadata", dialect)
    id_col = column_name("id", dialect)
    posting_col = column_name("posting", dialect)

    gaps: list[Gap] = []
    for template in contracts.templates:
        tmpl_name = str(template.template_name)
        for predicate in template.predicates:
            if not predicate.column.startswith("metadata."):
                continue
            key = predicate.column[len("metadata."):]
            # Count rows tagged with the template where the key isn't
            # in the metadata JSON (LIKE-pattern same as
            # metadata_coverage_per_template).
            like_pattern = f'%"{key}"%'
            count_sql = (
                f"SELECT COUNT(*) FROM {txns} "
                f"WHERE {tmpl_col} = {_quote(tmpl_name)} "
                f"AND ({md_col} IS NULL OR {md_col} NOT LIKE {_quote(like_pattern)})"
            )
            total_sql = (
                f"SELECT COUNT(*) FROM {txns} "
                f"WHERE {tmpl_col} = {_quote(tmpl_name)}"
            )
            sample_sql = (
                f"SELECT {id_col} FROM {txns} "
                f"WHERE {tmpl_col} = {_quote(tmpl_name)} "
                f"AND ({md_col} IS NULL OR {md_col} NOT LIKE {_quote(like_pattern)}) "
                f"ORDER BY {posting_col} DESC "
                f"{_limit_clause(dialect, 1)}"
            )
            missing_rows = await _fetch_rows(pool, count_sql, dialect)
            missing = int(missing_rows[0][0]) if missing_rows else 0
            if missing == 0:
                continue
            total_rows = await _fetch_rows(pool, total_sql, dialect)
            total = int(total_rows[0][0]) if total_rows else 0
            sample_rows = await _fetch_rows(pool, sample_sql, dialect)
            sample = (
                str(sample_rows[0][0]) if sample_rows and sample_rows[0][0]
                else None
            )
            gaps.append(Gap(
                kind="missing_metadata_key",
                diagnosis=(
                    f"Template {tmpl_name} declares \"{key}\" as required. "
                    f"{missing} of {total} {tmpl_name} rows landed without "
                    f"it — L1 Conservation can't bucket them. Operator "
                    f"decides: fix the ETL to emit \"{key}\", or drop the "
                    f"key from the template if upstream genuinely doesn't "
                    f"carry it."
                ),
                observed_value=f"{tmpl_name}::{key}",
                evidence=GapEvidence(
                    row_count=missing,
                    sample_transaction_id=sample,
                    extras={
                        "template_total_rows": str(total),
                        "missing_key": key,
                    },
                ),
                link_target=template.editor_path,
            ))
    return gaps


# -- SQL helpers -------------------------------------------------------------


def _quote(value: str) -> str:
    """SQL-escape a string literal (single-quote escaping)."""
    return "'" + value.replace("'", "''") + "'"


def _limit_clause(dialect: Dialect, limit: int) -> str:
    """Per-dialect LIMIT clause."""
    if dialect is Dialect.ORACLE:
        return f"FETCH FIRST {int(limit)} ROWS ONLY"
    return f"LIMIT {int(limit)}"


async def _fetch_count_per_value(
    pool: AsyncConnectionPool, sql: str, dialect: Dialect,
) -> list[tuple[str, int]]:
    """Run a ``SELECT col, COUNT(*) ...`` query, return [(value, count)]."""
    rows = await _fetch_rows(pool, sql, dialect)
    return [(str(r[0]), int(r[1])) for r in rows if r[0] is not None]


async def _fetch_two_value_count(
    pool: AsyncConnectionPool, sql: str, dialect: Dialect,
) -> list[tuple[str, str, int]]:
    """Same as ``_fetch_count_per_value`` for the 2-column-key shape."""
    rows = await _fetch_rows(pool, sql, dialect)
    out: list[tuple[str, str, int]] = []
    for r in rows:
        if r[0] is None or r[1] is None:
            continue
        out.append((str(r[0]), str(r[1]), int(r[2])))
    return out


async def _fetch_sample_ids(
    pool: AsyncConnectionPool,
    prefix: str, dialect: Dialect,
    *, column: str, values: list[str],
) -> dict[str, str]:
    """Per offending value, fetch one transaction id (most recent)."""
    if not values:
        return {}
    txns = f"{prefix}_transactions"
    id_col = column_name("id", dialect)
    posting_col = column_name("posting", dialect)
    out: dict[str, str] = {}
    for value in values:
        sql = (
            f"SELECT {id_col} FROM {txns} "
            f"WHERE {column} = {_quote(value)} "
            f"ORDER BY {posting_col} DESC "
            f"{_limit_clause(dialect, 1)}"
        )
        rows = await _fetch_rows(pool, sql, dialect)
        if rows and rows[0][0] is not None:
            out[value] = str(rows[0][0])
    return out


async def _fetch_rows(
    pool: AsyncConnectionPool, sql: str, dialect: Dialect,
) -> list[tuple[Any, ...]]:  # typing-smell: ignore[explicit-any]: row tuples are heterogeneous; per-call shape lives in the SELECT contract
    """Driver-uniform execute + fetchall (mirror of probe._execute_fetchall
    + coverage._fetch_rows; per-module duplicate keeps the import graph
    small + each consumer's SQL self-contained)."""
    async with pool.acquire() as conn:
        if dialect is Dialect.ORACLE:
            cur: Any = cast(Any, conn).cursor()  # typing-smell: ignore[explicit-any]: per-driver cursor union has no shared Protocol
            await cur.execute(sql)
        else:
            cur = await cast(Any, conn).execute(sql)  # typing-smell: ignore[explicit-any]: psycopg / aiosqlite cursor types not unified by a single Protocol
        try:
            rows: list[Any] = await cur.fetchall()  # typing-smell: ignore[explicit-any]: driver-typed row union widens to Any after Any cursor
        finally:
            close = getattr(cur, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
    return [tuple(r) for r in rows]
