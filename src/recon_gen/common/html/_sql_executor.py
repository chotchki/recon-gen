"""X.2.f — generic SQL executor with dialect-aware filter substitution.

Today's ``_db_fetcher.py`` hand-writes WHERE clauses + bind params
inline per visual. X.2.g will need the same pattern across dozens
of visuals — at that point the boilerplate becomes the bug surface.
This module is the abstraction: a Visual + its dataset SQL + the
URL-keyed filter dict → executed query → ``(rows, columns)``.

Filter param convention (from X.2.d's URL contract):

    date_from    → WHERE <date_col> >= :date_from
    date_to      → WHERE <date_col> <= :date_to
    param_<name> → bound to ``:<name>`` in dataset SQL
    filter_<col> → WHERE <col> IN (...) (comma-split server side)
    min_<col>    → WHERE <col> >= :min_<col>
    max_<col>    → WHERE <col> <= :max_<col>

The dataset SQL author opts in to filters by referencing them as
``:date_from`` / ``:param_view`` / etc. Filters not referenced are
silently ignored (zero-impact when a sheet doesn't carry them).

Placeholder dispatch:

    Postgres → ``%(name)s``  (psycopg / psycopg_pool named bind)
    Oracle   → ``:name``     (oracledb named bind)
    SQLite   → ``:name``     (aiosqlite named bind)

So Oracle and SQLite share the source form (``:name``); Postgres
gets a single rewrite pass before execution.

Two execute fns:

  - ``execute_visual_sql_async`` (X.2.n.3): async; takes an
    ``AsyncConnectionPool`` and uses ``async with pool.acquire()``
    + ``await cur.execute()``. The hot path used by App2's
    ``visual_data`` route.
  - ``execute_visual_sql`` (legacy sync): kept for backward compat
    with tests + scripts that pass a sync ``connection_factory``.
    Will be removed once all callers move to the async pool.

Pure module — no network / no DB at import. The pool / factory is
the seam. ``execute_visual_sql_*`` is renderer-agnostic;
``shape_for_kind`` in ``_data_shape.py`` is the per-renderer step
that follows.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from recon_gen.common.models import DatasetParameter
from recon_gen.common.sql.dialect import Dialect

if TYPE_CHECKING:
    from recon_gen.common.db import AsyncConnectionPool


# Matches ``:name`` placeholders. Excludes ``::`` (Postgres cast
# operator) by requiring the colon to NOT be preceded by another
# colon — uses a negative lookbehind. Identifier characters per
# Python identifier rules + digits.
_NAMED_PLACEHOLDER_RE = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


# Y.1.e — QuickSight dataset-parameter placeholders.
#
# QS dataset Custom SQL uses ``<<$paramName>>`` for parameter
# substitution (literal value spliced in by QS at query time).
# App2 reads the same SQL but binds via ``:param_paramName`` —
# these patterns translate the QS shape to the bind shape so one
# SQL string serves both dialects.
#
# Two patterns, applied in order:
# 1. Quoted form ``'<<$pName>>'`` (QS string-param convention —
#    author wraps in single quotes so substitution produces a
#    valid SQL string literal). Bind variables don't need quoting,
#    so we strip the surrounding quotes when translating.
# 2. Unquoted form ``<<$pName>>`` (QS numeric-param convention —
#    author leaves bare so substitution produces a valid SQL
#    number literal). Bind variables work directly without quoting.
#
# Translated form: ``:param_paramName`` — App2's URL bridge maps
# this to the URL-supplied value. The ``param_`` prefix matches the
# X.2.b URL-as-state contract (``?param_pName=value`` → bind under
# the ``param_pName`` name in the bind dict).
_QS_QUOTED_DSP_RE = re.compile(r"'<<\$([A-Za-z_][A-Za-z0-9_]*)>>'")
_QS_UNQUOTED_DSP_RE = re.compile(r"<<\$([A-Za-z_][A-Za-z0-9_]*)>>")


def translate_qs_dataset_params(sql: str) -> str:
    """Translate QS-style ``<<$paramName>>`` placeholders to the
    App2 bind-variable form ``:param_paramName``.

    Quoted form first (``'<<$pName>>'`` strips the outer quotes —
    binds quote for us), then unquoted (``<<$pName>>`` becomes
    ``:param_pName`` directly). Idempotent for SQL that contains
    no QS placeholders — passes through unchanged.

    The translation is purely syntactic and dialect-agnostic; the
    later ``rewrite_placeholders_for_dialect`` step converts
    ``:param_pName`` to PG's ``%(param_pName)s`` if needed.
    """
    sql = _QS_QUOTED_DSP_RE.sub(r":param_\1", sql)
    sql = _QS_UNQUOTED_DSP_RE.sub(r":param_\1", sql)
    return sql


def _dataset_param_fields(
    dp: DatasetParameter,
) -> tuple[str, str, str, list[object]] | None:
    """Extract ``(name, kind, value_type, static_default_values)`` from a
    ``DatasetParameter`` wrapper. ``kind`` ∈ ``{"string", "integer",
    "decimal", "datetime"}``. Returns ``None`` for a DateTime param whose
    default is a ``RollingDate`` expression (App2 can't evaluate QS
    rolling-date expressions) or for an unrecognised/empty wrapper.
    """
    sp = dp.StringDatasetParameter
    if sp is not None:
        sv = sp.DefaultValues.StaticValues if sp.DefaultValues else []
        return (str(sp.Name), "string", str(sp.ValueType), list(sv or []))
    ip = dp.IntegerDatasetParameter
    if ip is not None:
        sv = ip.DefaultValues.StaticValues if ip.DefaultValues else []
        return (str(ip.Name), "integer", str(ip.ValueType), list(sv or []))
    dec = dp.DecimalDatasetParameter
    if dec is not None:
        sv = dec.DefaultValues.StaticValues if dec.DefaultValues else []
        return (str(dec.Name), "decimal", str(dec.ValueType), list(sv or []))
    dt = dp.DateTimeDatasetParameter
    if dt is not None:
        sv = dt.DefaultValues.StaticValues if dt.DefaultValues else None
        if not sv:
            # RollingDate default (or none) — App2 can't resolve it.
            return None
        return (str(dt.Name), "datetime", str(dt.ValueType), list(sv))
    return None


def _format_default_for_sql(kind: str, values: Sequence[object]) -> str:
    """Format a dataset parameter's static default value(s) as a SQL
    literal fragment matching how QuickSight substitutes them:

    - ``integer`` / ``decimal`` → ``42`` (single) / ``1,2,3`` (multi)
    - ``string`` / ``datetime`` → ``'a'`` (single) / ``'a','b','c'`` (multi)

    These are TRUSTED values — declared in the codebase, never user
    input — so direct string-splicing is safe (mirrors QS's substitution
    and the dataset-SQL smoke verifier's ``_substitute_qs_params``).
    """
    if kind in ("integer", "decimal"):
        return ",".join(str(v) for v in values)
    return ",".join("'" + str(v).replace("'", "''") + "'" for v in values)


def _url_has_real_value(url_params: Mapping[str, list[str]], key: str) -> bool:
    """True iff the URL supplies ``key`` with at least one non-empty
    value. An emptied multi-select (``?key=``) or absent key counts as
    "no value" — callers fall back to the dataset-param default, mirroring
    how QuickSight reverts to ``DefaultValues`` on an emptied dropdown
    (Y.2.c.0 spike)."""
    vals = url_params.get(key)
    return bool(vals) and any(v != "" for v in vals)


def apply_dataset_param_defaults(
    sql: str,
    dataset_parameters: Sequence[DatasetParameter],
    url_params: Mapping[str, list[str]],
) -> str:
    """Y.2.app2.cde — replace ``<<$paramName>>`` placeholders with the
    dataset parameter's STATIC DEFAULT (string-substituted) when the URL
    doesn't supply that parameter, so a freshly-loaded App2 page matches
    how QuickSight renders the dashboard on initial load (where each
    dataset parameter's ``DefaultValues`` apply — analysis-param
    ``MappedDataSetParameters`` bridges don't fire until the analyst
    interacts; see Y.1.k).

    Placeholders the URL *does* supply with a real value (``?param_pName=v``)
    are left untouched — ``translate_qs_dataset_params`` (single value) /
    ``expand_multivalued_dataset_params`` (2+ values) then turn those into
    ``:param_pName`` bind variables (safe for untrusted URL input). An
    *emptied* multi-select (``?param_pName=``) counts as "not supplied" →
    falls back to the default (QS reverts there too — Y.2.c.0). Placeholders
    for params not declared on this dataset are also left untouched. Both
    the quoted (``'<<$pName>>'``) and bare (``<<$pName>>``) forms are
    handled — the quoted-form regex consumes the author's surrounding
    ``'...'`` so a formatted-with-quotes string default drops in cleanly.
    """
    by_name: dict[str, tuple[str, str, str, list[object]]] = {}
    for dp in dataset_parameters:
        fields = _dataset_param_fields(dp)
        if fields is not None:
            by_name[fields[0]] = fields

    def _sub(match: re.Match[str]) -> str:
        pname = match.group(1)
        if _url_has_real_value(url_params, f"param_{pname}"):
            return match.group(0)  # URL supplies it — leave for the bind/expand path.
        fields = by_name.get(pname)
        if fields is None:
            return match.group(0)  # Not declared here — leave it.
        _name, kind, _value_type, values = fields
        if not values:
            return match.group(0)  # Empty static default — nothing to splice.
        return _format_default_for_sql(kind, values)

    # Quoted form first (consumes the author's surrounding quotes), then
    # the bare form on what remains — same order as translate_*.
    sql = _QS_QUOTED_DSP_RE.sub(_sub, sql)
    sql = _QS_UNQUOTED_DSP_RE.sub(_sub, sql)
    return sql


def expand_multivalued_dataset_params(
    sql: str,
    dataset_parameters: Sequence[DatasetParameter],
    url_params: Mapping[str, list[str]],
) -> tuple[str, dict[str, Any]]:  # typing-smell: ignore[explicit-any]: int|float|str bind values per the param kind (AO.R.4 coercion)
    """Y.2.app2.cde.multivalued — for a ``MULTI_VALUED`` dataset
    parameter whose URL supplies 2+ non-empty values, expand the
    placeholder ``<<$pName>>`` into ``:param_pName_0, :param_pName_1, …``
    so an ``IN (<<$pName>>)`` becomes ``IN (:param_pName_0, …)`` with one
    bind per value — **never string-spliced** (URL values are untrusted;
    App2 doesn't enforce the param's ``StaticValues``).

    Call this AFTER ``apply_dataset_param_defaults`` (which already
    resolved the absent / emptied case to the static default) and BEFORE
    ``translate_qs_dataset_params`` (so the expanded ``:param_pName_i``
    binds pass straight through translate untouched). A placeholder with
    0 or 1 URL value is left alone — 0 was already resolved to the
    default; 1 binds fine through the normal ``:param_pName`` path.

    Returns ``(rewritten_sql, extra_binds)`` — merge ``extra_binds`` into
    whatever ``collect_bind_params`` returns (it overwrites the empty
    placeholders that walk would otherwise emit for the ``_i`` names).
    """
    kind_by_name: dict[str, str] = {}
    multi_names: set[str] = set()
    for dp in dataset_parameters:
        fields = _dataset_param_fields(dp)
        if fields is not None:
            kind_by_name[fields[0]] = fields[1]
            if fields[2] == "MULTI_VALUED":
                multi_names.add(fields[0])
    extra_binds: dict[str, Any] = {}  # typing-smell: ignore[explicit-any]: int|float|str per the param kind (AO.R.4 coercion)

    def _sub(match: re.Match[str]) -> str:
        pname = match.group(1)
        if pname not in multi_names:
            return match.group(0)
        vals = [v for v in (url_params.get(f"param_{pname}") or []) if v != ""]
        if len(vals) < 2:
            return match.group(0)  # 0 → default already applied; 1 → normal bind.
        names = [f"param_{pname}_{i}" for i in range(len(vals))]
        kind = kind_by_name.get(pname)
        for n, v in zip(names, vals, strict=True):
            extra_binds[n] = _coerce_bind(v, kind)  # AO.R.4 — numeric IN-list values
        return ", ".join(f":{n}" for n in names)

    sql = _QS_QUOTED_DSP_RE.sub(_sub, sql)
    sql = _QS_UNQUOTED_DSP_RE.sub(_sub, sql)
    return sql, extra_binds


def rewrite_placeholders_for_dialect(sql: str, dialect: Dialect) -> str:
    """Convert ``:name`` placeholders to dialect-native form.

    SQLite + Oracle accept ``:name`` natively (DB-API 2.0 named
    paramstyle); Postgres uses ``%(name)s``. The rewrite is purely
    string-level — caller still passes the same dict of bind values
    regardless of dialect. ``::`` (PG cast) is preserved.
    """
    if dialect is Dialect.POSTGRES:
        return _NAMED_PLACEHOLDER_RE.sub(r"%(\1)s", sql)
    # Oracle + SQLite already accept ``:name``.
    return sql


def _coerce_bind(value: str, kind: str | None) -> Any:  # typing-smell: ignore[explicit-any]: returns int|float|str depending on the dataset-param kind
    """Coerce a URL-supplied bind value to the dataset parameter's type.

    AO.R.4 — an ``IntegerDatasetParameter`` / ``DecimalDatasetParameter``
    bound from the URL arrives as a string (``"2"``); binding it as text
    makes a numeric comparison (``z_score >= :p``) fall to a string
    comparison, which on SQLite's type affinity matches 0 rows (the moved
    σ-slider → "0 flagged" bug — the default-substitution path splices the
    integer literal and works, so the symptom only appears once the
    control is touched). String / datetime params stay as-is (date columns
    compare correctly against ISO text). A non-numeric value falls back to
    the raw string so the SQL author's empty-guard still fires.
    """
    if kind == "integer":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if kind == "decimal":
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value


def collect_bind_params(
    sql: str,
    url_params: Mapping[str, list[str]],
    dataset_parameters: Sequence[DatasetParameter] = (),
) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: bind dict accepted by every driver coerces values per-driver — caller passes whatever the SQL placeholder needs
    """Build the bind-param dict for the SQL string.

    Walks ``sql`` for ``:name`` placeholders, looks up each name in
    ``url_params`` (a multi-dict; takes the LAST value when a key
    repeats — mirrors the old ``query_params.items()`` last-wins
    behavior), and returns the dict the DB driver wants. Names not
    present in ``url_params`` (or present with no values) get an
    empty string — the dataset SQL author guards against empty
    filters (typically ``WHERE col >= :date_from OR :date_from = ''``).
    Names referenced in ``url_params`` but NOT in the SQL are dropped
    (no-op — the driver would reject "too many parameters" otherwise).
    Multi-valued ``IN``-list placeholders are handled separately by
    ``expand_multivalued_dataset_params`` (one ``:name_i`` bind per
    value), whose ``extra_binds`` the caller merges over this dict.
    """
    referenced = set(_NAMED_PLACEHOLDER_RE.findall(sql))
    # ``param_<name>`` bind → the declared kind of dataset param ``<name>``,
    # so an integer / decimal value coerces off its URL string (AO.R.4).
    kind_by_bind: dict[str, str] = {}
    for dp in dataset_parameters:
        fields = _dataset_param_fields(dp)
        if fields is not None:
            kind_by_bind[f"param_{fields[0]}"] = fields[1]
    return {
        name: _coerce_bind((url_params.get(name) or [""])[-1], kind_by_bind.get(name))
        for name in referenced
    }


def _prepare_sql_and_binds(
    sql: str,
    url_params: Mapping[str, list[str]],
    dataset_parameters: Sequence[DatasetParameter],
    dialect: Dialect,
) -> tuple[str, dict[str, Any]]:  # typing-smell: ignore[explicit-any]: bind dict is driver-coerced — same justification as collect_bind_params
    """Shared pre-execute pipeline: resolve dataset-param defaults →
    expand multi-valued ``IN``-lists → translate QS placeholders →
    dialect-rewrite → collect binds (with the multi-valued ``_i``
    binds merged on top). Returns ``(rewritten_sql, binds)``."""
    sql = apply_dataset_param_defaults(sql, dataset_parameters, url_params)
    sql, extra_binds = expand_multivalued_dataset_params(
        sql, dataset_parameters, url_params,
    )
    sql = translate_qs_dataset_params(sql)
    rewritten = rewrite_placeholders_for_dialect(sql, dialect)
    binds = collect_bind_params(sql, url_params, dataset_parameters)
    binds.update(extra_binds)
    return rewritten, binds


def execute_visual_sql(
    connection_factory: Callable[[], Any],  # typing-smell: ignore[explicit-any]: sync DB-API 2.0 connection has no shared Protocol across psycopg/oracledb/sqlite3
    sql: str,
    url_params: Mapping[str, list[str]],
    *,
    dialect: Dialect,
    dataset_parameters: Sequence[DatasetParameter] | None = None,
) -> tuple[list[tuple[Any, ...]], list[str]]:  # typing-smell: ignore[explicit-any]: row tuples are heterogeneous; per-call shape lives in the dataset SQL contract
    """Execute a Visual's dataset SQL via a sync DB-API 2.0 driver.

    DEPRECATED — kept for backward compatibility with sync test
    fixtures + ad-hoc scripts. The App2 server uses the async path
    (``execute_visual_sql_async``) which doesn't block the event
    loop. New code should pass an ``AsyncConnectionPool`` and
    ``await execute_visual_sql_async(...)``.

    Args:
        connection_factory: returns a fresh DB-API 2.0 connection.
            Caller is responsible for pooling / sharing if relevant
            — this fn opens + closes per call.
        sql: dataset SQL with ``:name`` placeholders (any dialect).
        url_params: the URL-keyed filter multi-dict the App2 server
            extracted from the request query string (``param_pName →
            [v, …]``; repeated keys preserved). Keys not referenced
            in ``sql`` are ignored.
        dialect: SQL dialect of the connection. Drives placeholder
            rewriting (PG → ``%(name)s``; Oracle / SQLite stay).
        dataset_parameters: optional QS ``DatasetParameter`` list —
            drives ``<<$paramName>>`` default substitution (Y.2.app2.cde)
            + multi-valued ``IN``-list bind expansion (Y.2.app2.cde.multivalued).

    Returns:
        ``(rows, columns)``: rows is a list of tuples, columns is
        the list of column names from ``cursor.description``. The
        per-renderer shape adapter in ``_data_shape.py`` consumes
        this tuple.
    """
    rewritten, binds = _prepare_sql_and_binds(
        sql, url_params, dataset_parameters or [], dialect,
    )
    conn = connection_factory()
    try:
        cur = conn.cursor()
        try:
            cur.execute(rewritten, binds)
            rows: list[Any] = list(cur.fetchall())  # typing-smell: ignore[explicit-any]: heterogeneous row tuples
            description: Sequence[Sequence[Any]] = cur.description or []  # typing-smell: ignore[explicit-any]: DB-API 2.0 description tuples mix types per column slot
            columns = [str(c[0]) for c in description]
        finally:
            cur.close()
    finally:
        conn.close()
    return [tuple(r) for r in rows], columns


async def execute_visual_sql_async(
    pool: AsyncConnectionPool,
    sql: str,
    url_params: Mapping[str, list[str]],
    *,
    dialect: Dialect,
    dataset_parameters: Sequence[DatasetParameter] | None = None,
) -> tuple[list[tuple[Any, ...]], list[str]]:  # typing-smell: ignore[explicit-any]: row tuples are heterogeneous; per-call shape lives in the dataset SQL contract
    """Async sibling of ``execute_visual_sql`` — the hot path for the
    App2 ``visual_data`` route.

    Acquires a connection from the pool (returns to pool on context
    exit), opens a cursor, awaits ``execute`` + ``fetchall``, and
    returns ``(rows, columns)`` in the same shape the sync version
    produces. The pure-CPU bits (placeholder rewrite + bind
    collection) stay sync; only the I/O bits await.

    Cursor lifecycle is dialect-aware (Y.3.f.alt.4b):
      - psycopg: ``await conn.execute(sql, params)`` returns the
        AsyncCursor — the documented one-shot pattern.
      - aiosqlite: same — ``await conn.execute(...)`` returns a
        cursor.
      - oracledb async: ``conn.execute()`` does NOT return a cursor
        (returns ``None``; executes against an internal cursor we
        can't access). Must use the explicit ``cur = conn.cursor();
        await cur.execute(sql, params)`` pattern.

    Returns:
        Same ``(rows, columns)`` shape as the sync version. Rows
        are coerced to tuples (oracledb returns lists; psycopg
        returns tuples; aiosqlite returns Row objects that pickle
        as tuples).

    Raises:
        Whatever the underlying driver raises on bad SQL / pool
        exhaustion / connection failure. The App2 server's themed
        500 handler (X.2.m) catches it.

        dataset_parameters: optional QS ``DatasetParameter`` list —
            drives ``<<$paramName>>`` default substitution (Y.2.app2.cde)
            + multi-valued ``IN``-list bind expansion (Y.2.app2.cde.multivalued).
    """
    rewritten, binds = _prepare_sql_and_binds(
        sql, url_params, dataset_parameters or [], dialect,
    )
    async with pool.acquire() as conn:
        if dialect is Dialect.ORACLE:
            # oracledb async: must open a cursor explicitly. The
            # ``conn.execute(sql, params)`` shorthand returns None
            # (executes via an internal cursor we can't read back).
            # cast: per-driver cursor union (psycopg AsyncCursor /
            # aiosqlite Cursor / oracledb AsyncCursor) lacks a
            # shared Protocol for fetchall/description/close, AND
            # `conn` is typed as psycopg AsyncConnection (the pool
            # alias) — Oracle conn doesn't share that interface.
            cur: Any = cast(Any, conn).cursor()  # typing-smell: ignore[explicit-any]: per-driver cursor union (psycopg/aiosqlite/oracledb) has no shared Protocol; conn typed as AsyncConnection so cursor() not exposed there either
            await cur.execute(rewritten, binds)
        else:
            # psycopg AsyncConnection + aiosqlite both return a
            # cursor from ``await conn.execute(sql, params)``.
            cur = cast(Any, await conn.execute(rewritten, binds))  # typing-smell: ignore[explicit-any]: per-driver cursor union (psycopg/aiosqlite) has no shared Protocol for fetchall/description/close
        try:
            rows: list[Any] = await cur.fetchall()  # typing-smell: ignore[explicit-any]: driver-typed row union widens to Any after Any cursor
            description = cast(list[Any], cur.description or [])  # typing-smell: ignore[explicit-any]: per-driver column-meta tuple union — same justification as `cur` above
            columns = [str(c[0]) for c in description]
        finally:
            # aiosqlite cursors close async; psycopg / oracledb
            # cursors are also async. Await regardless to keep the
            # path uniform.
            close = getattr(cur, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
    return [tuple(r) for r in rows], columns
