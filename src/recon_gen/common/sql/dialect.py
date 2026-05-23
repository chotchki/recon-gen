"""Dialect-specific SQL helpers — Phase P.2 catalog + P.3 Oracle fill +
P.3.e cleanup + X.3 SQLite.

Every helper takes an explicit ``Dialect`` (no defaults — see "P.3.e
cleanup" below) and returns a dialect-appropriate SQL string. The
helpers split into two output shapes:

**Statement helpers** — return a fully terminated, self-contained
statement. Postgres branch ends in ``;``; Oracle branch wraps in
a PL/SQL block ending in ``END;``. Callers concatenate without
appending a separate ``;``.

  - ``drop_table_if_exists``
  - ``drop_matview_if_exists``
  - ``drop_index_if_exists``
  - ``drop_view_if_exists``
  - ``refresh_matview``
  - ``analyze_table``

**Fragment helpers** — return an expression-level SQL string with no
trailing ``;``. Substituted into larger SQL templates by the caller,
which decides where the statement boundary lives.

  - Type names (``serial_type``, ``timestamp_type``, ``text_type``,
    ``varchar_type``, ``decimal_type``, ``boolean_type``, ``decimal_type``)
  - Casts (``cast``, ``typed_null``, ``to_date``, ``date_trunc_day``)
  - Date arithmetic (``epoch_seconds_between``, ``interval_days``,
    ``date_minus_days``)
  - Constraints (``json_check``)
  - Other clauses (``with_recursive``, ``create_matview``,
    ``matview_options``)

Phase notes:
- P.2 shipped every helper with a Postgres branch only.
- P.3 filled in the Oracle branches (19c Standard Edition target).
- P.3.e dropped the ``dialect: Dialect = Dialect.POSTGRES`` defaults
  so call sites must be explicit. Every in-tree caller already passed
  a dialect by then; defaults were a rollout convenience that's no
  longer load-bearing.
- ``_matview_options`` moved into this module from ``common.l2.schema``
  in P.3.e — it's a pure dialect helper and belongs alongside
  ``create_matview`` / ``refresh_matview``.
- X.3 added the SQLite branch (3.38+, ships with stdlib ``sqlite3``).
  SQLite has no ``CREATE MATERIALIZED VIEW``: matviews are emitted as
  ``CREATE TABLE … AS SELECT`` and refreshed by ``DELETE`` + ``INSERT``.
  JSON metadata uses SQLite's JSON1 functions (``json_valid``,
  ``json_extract``); the ``IS JSON`` constraint becomes a
  ``CHECK (json_valid(col))``. The dialect is the local-iteration
  loop's storage — single-file or in-memory, no server required.
"""

from __future__ import annotations

from enum import Enum


class Dialect(str, Enum):
    """Target SQL dialect.

    Postgres covers Postgres 17+ (the version floor required by the
    SQL/JSON path syntax we already use). Oracle covers Oracle 19c
    Standard Edition (the long-term-support version Phase P targets).
    SQLite covers SQLite 3.38+ (the version floor for the JSON1
    functions we depend on; ships with Python's stdlib ``sqlite3``).
    """

    POSTGRES = "postgres"
    ORACLE = "oracle"
    SQLITE = "sqlite"


# -- Identifiers -------------------------------------------------------------


def column_name(name: str, dialect: Dialect) -> str:
    """Per-dialect natural case for an unquoted column identifier.

    Postgres + SQLite case-fold unquoted identifiers to lowercase; Oracle
    case-folds to UPPERCASE. Storing column names in the dialect's natural
    case lets every reference (DDL column, dataset SQL projection, App2
    outer-SELECT alias, QuickSight ``Columns`` declaration) use the same
    string without quote-juggling — and lets unquoted refs in dataset SQL
    pick up the matching column on every dialect.

    Used at every site that emits a column name into either SQL output or
    the QuickSight Dataset.Columns metadata: callers pass the canonical
    lowercase column name from the contract, this helper folds to the
    dialect's natural case, and the result is the string that both the
    database and QuickSight will agree on.

    The bridge this replaces (``_oracle_lowercase_alias_wrapper`` in
    ``common/dataset_contract.py``) wrapped Oracle dataset SQL in an
    outer alias-rename to back-port UPPERCASE → quoted-lowercase for QS.
    The wrapper produced an asymmetry App2's ``wrap_for_visual`` couldn't
    follow (``ORA-00904: "ACCOUNT_ID": invalid identifier``), and every
    new dialect-port surfaced fresh case-confusion edge cases — see
    Y.3.f for the full bridge-removal plan.
    """
    if dialect is Dialect.ORACLE:
        return name.upper()
    return name.lower()


# -- Type names (DDL) --------------------------------------------------------


def serial_type(dialect: Dialect) -> str:
    """Auto-incrementing 64-bit append-only key.

    Postgres ``BIGSERIAL`` / Oracle
    ``NUMBER GENERATED ALWAYS AS IDENTITY``. SQLite has no
    auto-increment for non-INTEGER-PRIMARY-KEY columns (``entry``
    participates in a composite PK with ``id``, so the
    ``INTEGER PRIMARY KEY AUTOINCREMENT`` shortcut doesn't apply).
    Schema emit pairs the bare ``INTEGER`` column type with a
    ``BEFORE INSERT`` trigger that sets ``NEW.entry =
    COALESCE((SELECT MAX(entry) FROM <table> WHERE id = NEW.id), 0)
    + 1`` so the column behaves like a per-id supersession key —
    the same semantic Postgres' ``BIGSERIAL`` + Oracle's IDENTITY
    deliver via different mechanisms.
    """
    if dialect is Dialect.POSTGRES:
        return "BIGSERIAL"
    if dialect is Dialect.SQLITE:
        return "INTEGER"
    return "NUMBER GENERATED ALWAYS AS IDENTITY"


def boolean_type(dialect: Dialect) -> str:
    """Boolean column type.

    Postgres has a native ``BOOLEAN``; Oracle 19c does not, so the
    canonical encoding is ``NUMBER(1)`` with a ``CHECK (col IN (0, 1))``.
    SQLite has no native BOOLEAN — uses ``INTEGER`` (0/1) by
    convention. The helper returns just the type name — callers that
    need the CHECK constraint compose it themselves.
    """
    if dialect is Dialect.POSTGRES:
        return "BOOLEAN"
    if dialect is Dialect.SQLITE:
        return "INTEGER"
    return "NUMBER(1)"


def text_type(dialect: Dialect) -> str:
    """Unbounded character data.

    Postgres ``TEXT`` / Oracle ``CLOB`` / SQLite ``TEXT``.
    """
    if dialect is Dialect.POSTGRES:
        return "TEXT"
    if dialect is Dialect.SQLITE:
        return "TEXT"
    return "CLOB"


def json_text_type(dialect: Dialect) -> str:
    """Bounded text type for columns holding JSON metadata documents.

    Diverges from ``text_type`` (which returns Postgres ``TEXT`` /
    Oracle ``CLOB``) by emitting a bounded ``VARCHAR(4000)`` /
    ``VARCHAR2(4000)`` so the columns behave like ordinary strings on
    both dialects. Why bound:

    - Oracle ``CLOB`` can't be aggregated (``MIN`` / ``MAX`` /
      ``GROUP BY`` reject CLOB with ORA-00932), can't appear in
      ``DISTINCT`` / ``ORDER BY``, and fails ``IN`` comparisons
      against ``VARCHAR2`` literals. Queries that pick a
      representative ``metadata`` per transfer via ``MAX(metadata)``
      need it bounded.
    - Bounding Postgres to the same 4000-char limit keeps the two
      dialects symmetric so a "data too long" failure surfaces on
      either DB instead of leaking past PG and breaking only on
      Oracle.

    4000 chars covers every JSON metadata document the L2 schema emits
    (typically 5–20 keys with short values). Banks with longer
    documents either trim at the ETL boundary or enable Oracle's
    ``MAX_STRING_SIZE=EXTENDED`` (lifts VARCHAR2 to 32767) and bump
    this helper.

    SQLite uses ``TEXT`` here too — it's typeless under the hood
    (``VARCHAR(N)`` parses but the length is purely advisory), so
    matching the symmetric "string-shaped" treatment by emitting
    plain ``TEXT`` keeps the SQL readable.
    """
    if dialect is Dialect.POSTGRES:
        return "VARCHAR(4000)"
    if dialect is Dialect.SQLITE:
        return "TEXT"
    return "VARCHAR2(4000)"


def timestamp_type(dialect: Dialect) -> str:  # noqa: ARG001
    """TZ-naive timestamp, identical on both dialects.

    Returns ``TIMESTAMP`` regardless of dialect (P.9a). Timezone
    normalization is the integrator's contract — the L2 schema does
    not store timezone metadata and does not convert across zones.
    Banks reading from sources in multiple zones MUST normalize at
    the ETL boundary (typically to UTC or the institution's local
    business zone).

    Why standardized: the prior split helpers (``timestamp_tz_type``
    + ``pk_safe_timestamp_type``) bridged Postgres TIMESTAMPTZ /
    Oracle TIMESTAMP WITH TIME ZONE for non-PK columns and demoted
    to plain TIMESTAMP for PK columns (Oracle rejects TZ-aware
    TIMESTAMPs in PKs with ORA-02329). The split surfaced as a
    cross-dialect divergence with no compensating value — neither
    engine performs query-time TZ conversion in a way the dashboards
    rely on, and the demotion was already happening for half the
    columns. Unifying on plain TIMESTAMP makes the schema byte-
    identical between dialects.
    """
    return "TIMESTAMP"


def varchar_type(n: int, dialect: Dialect) -> str:
    """Bounded variable-length character.

    Postgres ``VARCHAR(n)`` / Oracle ``VARCHAR2(n)`` / SQLite
    ``TEXT`` (SQLite is typeless internally; the ``(n)`` would parse
    but enforce nothing, so emit plain ``TEXT`` for clarity).
    """
    if dialect is Dialect.POSTGRES:
        return f"VARCHAR({n})"
    if dialect is Dialect.SQLITE:
        return "TEXT"
    return f"VARCHAR2({n})"


def decimal_type(precision: int, scale: int, dialect: Dialect) -> str:
    """Fixed-precision decimal.

    Postgres ``DECIMAL(p,s)`` / Oracle ``NUMBER(p,s)`` / SQLite
    ``NUMERIC`` (SQLite is typeless and stores all numerics as one
    of INTEGER / REAL / TEXT per its dynamic typing rules; ``NUMERIC``
    is the affinity that prefers exact representation).
    """
    if dialect is Dialect.POSTGRES:
        return f"DECIMAL({precision},{scale})"
    if dialect is Dialect.SQLITE:
        return "NUMERIC"
    return f"NUMBER({precision},{scale})"


# -- Casts -------------------------------------------------------------------


def cast(expr: str, type_name: str, dialect: Dialect) -> str:
    """Cast ``expr`` to ``type_name``.

    Postgres ``expr::type`` / Oracle ``CAST(expr AS type)`` / SQLite
    ``CAST(expr AS type)`` (SQLite uses standard SQL CAST syntax;
    type-name aliasing remaps Postgres-shape names to SQLite affinity
    names where they differ).
    """
    if dialect is Dialect.POSTGRES:
        return f"{expr}::{type_name}"
    if dialect is Dialect.SQLITE:
        return f"CAST({expr} AS {_sqlite_type_alias(type_name)})"
    return f"CAST({expr} AS {_oracle_type_alias(type_name)})"


def typed_null(type_name: str, dialect: Dialect) -> str:
    """Typed NULL literal.

    Postgres ``NULL::type`` / Oracle ``CAST(NULL AS type)`` / SQLite
    ``CAST(NULL AS type)`` (same standard SQL CAST as Oracle; type
    aliasing maps to SQLite affinity names).
    """
    if dialect is Dialect.POSTGRES:
        return f"NULL::{type_name}"
    if dialect is Dialect.SQLITE:
        return f"CAST(NULL AS {_sqlite_type_alias(type_name)})"
    return f"CAST(NULL AS {_oracle_type_alias(type_name)})"


def to_date(timestamp_expr: str, dialect: Dialect) -> str:
    """Truncate a timestamp expression to its date component.

    Postgres ``expr::date`` / Oracle ``TRUNC(expr)`` / SQLite
    ``DATE(expr)`` (SQLite has no native ``DATE`` type — ``DATE()``
    returns the date portion as ``YYYY-MM-DD`` text, which is
    sortable + groupable the same way the typed counterparts are).
    """
    if dialect is Dialect.POSTGRES:
        return f"{timestamp_expr}::date"
    if dialect is Dialect.SQLITE:
        return f"DATE({timestamp_expr})"
    return f"TRUNC({timestamp_expr})"


def date_literal(iso_value: str, dialect: Dialect) -> str:
    """A SQL date literal that compares correctly on every dialect.

    ``iso_value`` is the ``YYYY-MM-DD`` string form (caller produces it
    via ``date.isoformat()``). The helper wraps it in the per-dialect
    syntax that produces a value comparable against the dialect's
    DATE / TIMESTAMP / TEXT-shaped date columns.

    Postgres + Oracle: ``DATE 'YYYY-MM-DD'`` — the SQL-standard date
    literal, accepted by both. Compares natively against DATE and
    coerces correctly against TIMESTAMP columns.

    SQLite: ``'YYYY-MM-DD'`` — a plain text literal. SQLite has no
    native DATE type and stores ISO dates as TEXT; lexicographic TEXT
    comparison happens to be correct for ISO-8601 (`'2030-01-01' <
    '2030-01-02'` lexically, same as date-wise). The SQL-standard
    ``DATE 'literal'`` form is rejected by SQLite (parses ``DATE`` as a
    column reference), and ``CAST('YYYY-MM-DD' AS DATE)`` coerces to
    INTEGER 2030 (NUMERIC affinity extracts the leading digits) — both
    are wrong for SQLite. Use this helper instead of inline string
    formatting at every audit / matview / dataset SQL site that needs
    a date literal in a WHERE / CASE WHEN comparison.
    """
    if dialect is Dialect.SQLITE:
        return f"'{iso_value}'"
    return f"DATE '{iso_value}'"


# Oracle type-name canonicalization. Postgres uses lowercase
# ``numeric`` / ``bigint`` / ``date`` / ``timestamp`` per its docs;
# Oracle wants ``NUMBER`` / ``DATE`` / ``TIMESTAMP``. The
# ``_oracle_type_alias`` table keeps the helpers' callers free to
# pass Postgres-shape type names while the Oracle branch substitutes
# the right name automatically.
_ORACLE_TYPE_ALIASES = {
    "numeric": "NUMBER",
    "bigint": "NUMBER(19)",
    "int": "NUMBER(10)",
    "integer": "NUMBER(10)",
    "smallint": "NUMBER(5)",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "text": "CLOB",
    "boolean": "NUMBER(1)",
}


def _oracle_type_alias(type_name: str) -> str:
    """Return the Oracle equivalent of a Postgres-shape type name.

    Direct hits in ``_ORACLE_TYPE_ALIASES`` win; otherwise the helper
    rewrites ``varchar(N)`` → ``VARCHAR2(N)`` so callers can pass the
    Postgres-shape parameterized type name and get the Oracle form.
    Unhandled types pass through unchanged.
    """
    name_lower = type_name.lower()
    if name_lower in _ORACLE_TYPE_ALIASES:
        return _ORACLE_TYPE_ALIASES[name_lower]
    # varchar(N) → VARCHAR2(N) for parameterized varchars
    import re
    m = re.match(r"^varchar\((\d+)\)$", name_lower)
    if m:
        return f"VARCHAR2({m.group(1)})"
    return type_name


# SQLite type-name canonicalization — maps Postgres-shape type names
# to SQLite affinity names. SQLite's storage classes are NULL /
# INTEGER / REAL / TEXT / BLOB; column "types" are advisory affinities
# (NUMERIC / INTEGER / REAL / TEXT / BLOB). We map exact-precision
# numerics to NUMERIC, integers to INTEGER, dates/timestamps to TEXT
# (SQLite has no native datetime type — convention is ISO-8601 text
# stored in TEXT-affinity columns + queried via ``date()`` / ``datetime()``
# / ``strftime()``), and text-y types to TEXT. Anything we forgot
# passes through unchanged.
_SQLITE_TYPE_ALIASES = {
    "numeric": "NUMERIC",
    "bigint": "INTEGER",
    "int": "INTEGER",
    "integer": "INTEGER",
    "smallint": "INTEGER",
    "date": "TEXT",
    "timestamp": "TEXT",
    "text": "TEXT",
    "boolean": "INTEGER",
    "clob": "TEXT",
}


def _sqlite_type_alias(type_name: str) -> str:
    """Return the SQLite equivalent of a Postgres-shape type name.

    Direct hits in ``_SQLITE_TYPE_ALIASES`` win; otherwise the helper
    rewrites ``varchar(N)`` → ``TEXT`` (SQLite ignores VARCHAR length).
    Unhandled types pass through unchanged.
    """
    name_lower = type_name.lower()
    if name_lower in _SQLITE_TYPE_ALIASES:
        return _SQLITE_TYPE_ALIASES[name_lower]
    # varchar(N) → TEXT — SQLite is typeless internally; the (N) is
    # advisory at best.
    import re
    m = re.match(r"^varchar\((\d+)\)$", name_lower)
    if m:
        return "TEXT"
    return type_name


# -- JSON --------------------------------------------------------------------


def greatest(*args: str, dialect: Dialect) -> str:
    """Row-wise greatest of two or more expressions.

    PG / Oracle: ``GREATEST(a, b, ...)`` — the SQL-standard scalar
    function (not an aggregate). SQLite: ``MAX(a, b, ...)`` — SQLite
    overloads ``MAX`` to be a row-wise scalar when called with 2+
    arguments and the column-aggregate when called with 1. Both
    forms have identical semantics for our use case (clamping
    expressions like ``GREATEST(x - y, 0)``).
    """
    joined = ", ".join(args)
    if dialect is Dialect.SQLITE:
        return f"MAX({joined})"
    return f"GREATEST({joined})"


def json_value(col: str, path_expr: str, dialect: Dialect) -> str:
    """Extract a scalar from a JSON-shaped column via SQL/JSON path.

    PG / Oracle: ``JSON_VALUE(col, path_expr)`` — the SQL/JSON-standard
    function (Postgres 12+, Oracle 12.2+). SQLite: ``json_extract(col,
    path_expr)`` — the JSON1 extension's equivalent (built into
    stdlib ``sqlite3`` 3.38+). Both return scalar TEXT for the path's
    leaf; missing paths return NULL.

    ``path_expr`` is the SQL expression (already wrapped in quotes /
    constructed at the call site, e.g. ``"'$.customer_id'"`` or
    ``"'$.' || pKey"``) — same shape on every dialect, so the helper
    only swaps the function name.
    """
    if dialect is Dialect.SQLITE:
        return f"json_extract({col}, {path_expr})"
    return f"JSON_VALUE({col}, {path_expr})"


def json_array_iterate(
    json_expr: str, array_path: str, *, alias: str, dialect: Dialect,
) -> str:
    """``LEFT JOIN`` clause that iterates a JSON array within
    ``json_expr`` at ``array_path``, exposing each element under
    ``alias.value`` (and ``alias.key`` on SQLite — unused but harmless).

    Per-dialect renderings (AW.0.b spike confirmed the SQLite shape;
    PG + Oracle use the SQL/JSON-standard ``JSON_TABLE`` form per the
    project's portability constraint — no JSONB, no ``->>``, only
    SQL/JSON path syntax; PG 17+ required for native ``JSON_TABLE``):

    - **SQLite**: ``json_each(<expr>, '<path>')`` — table-valued
      function over JSON1; each row has ``key`` + ``value`` columns.
    - **PG 17+**: ``JSON_TABLE(<expr>::json, '<path>[*]' COLUMNS
      (value JSON PATH '$'))`` — SQL/JSON-standard, no JSONB needed.
    - **Oracle 12c+**: same JSON_TABLE shape; ``VARCHAR2(4000) FORMAT
      JSON PATH '$'`` for the per-row value column (Oracle doesn't
      have a JSON type pre-21c; uses VARCHAR2 + FORMAT JSON hint).

    ``json_expr`` is the SQL expression yielding the JSON document
    (typically a scalar subquery like ``(SELECT l2_yaml FROM
    <p>_config)``). ``array_path`` is the SQL/JSON path to the array
    (e.g. ``'$.rails'``). ``alias`` is the per-row alias the matview
    SQL uses to reference the iteration (e.g. ``rail``).
    """
    if dialect is Dialect.SQLITE:
        return f"json_each({json_expr}, '{array_path}') {alias}"
    if dialect is Dialect.POSTGRES:
        # PG 17+ JSON_TABLE is SQL/JSON-standard; cast to `json`
        # (not jsonb — banned by the project's portability constraint).
        return (
            f"JSON_TABLE(({json_expr})::json, '{array_path}[*]' "
            f"COLUMNS (value json PATH '$')) {alias}"
        )
    # Oracle 12c+: VARCHAR2(4000) FORMAT JSON for the value column.
    return (
        f"JSON_TABLE({json_expr}, '{array_path}[*]' COLUMNS "
        f"(value VARCHAR2(4000) FORMAT JSON PATH '$')) {alias}"
    )


def json_field_extract(value_expr: str, field_path: str, dialect: Dialect) -> str:
    """Extract a scalar field from a per-row JSON element (e.g. one
    iteration of `json_array_iterate`).

    All dialects use SQL/JSON-standard path syntax — no JSONB-specific
    operators (the project bans `->>`, `->`, `@>` per the portability
    constraint).

    - **SQLite**: ``json_extract(<value_expr>, '<field_path>')``
    - **PG 12+ / Oracle 12c+**: ``JSON_VALUE(<value_expr>,
      '<field_path>')`` — SQL/JSON-standard scalar extract.

    ``field_path`` is the SQL/JSON path (e.g. ``'$.name'``).
    """
    if dialect is Dialect.SQLITE:
        return f"json_extract({value_expr}, '{field_path}')"
    return f"JSON_VALUE({value_expr}, '{field_path}')"


def json_check(col: str, dialect: Dialect) -> str:
    """``CHECK (col IS NULL OR col IS JSON)`` in PG / Oracle; SQLite
    uses ``CHECK (col IS NULL OR json_valid(col))``.

    The ``IS JSON`` SQL/JSON-standard constraint is supported in
    Postgres 16+ and Oracle 12.2+ — bytes-identical there. SQLite
    has no ``IS JSON`` predicate but ships ``json_valid(text)`` (returns
    1 if the argument is well-formed JSON; 0 otherwise) via the
    JSON1 extension (built into stdlib ``sqlite3`` since 3.38).
    """
    if dialect is Dialect.SQLITE:
        return f"CHECK ({col} IS NULL OR json_valid({col}))"
    return f"CHECK ({col} IS NULL OR {col} IS JSON)"


# -- Date / time arithmetic --------------------------------------------------


def epoch_seconds_between(later: str, earlier: str, dialect: Dialect) -> str:
    """Difference between two timestamps in whole + fractional seconds.

    Postgres ``EXTRACT(EPOCH FROM (later - earlier))``. Oracle has
    no EPOCH unit; the equivalent for TIMESTAMP arithmetic (which
    yields ``INTERVAL DAY TO SECOND``) is the sum of
    EXTRACT(DAY/HOUR/MINUTE/SECOND FROM …) terms. SQLite uses
    ``(julianday(later) - julianday(earlier)) * 86400`` — julianday
    returns the Julian Day Number as a REAL, so subtracting two
    julianday values yields fractional days; multiplying by 86400
    gives seconds.
    """
    if dialect is Dialect.POSTGRES:
        return f"EXTRACT(EPOCH FROM ({later} - {earlier}))"
    if dialect is Dialect.SQLITE:
        return (
            f"((julianday({later}) - julianday({earlier})) * 86400)"
        )
    diff = f"({later} - {earlier})"
    return (
        f"(EXTRACT(DAY FROM {diff}) * 86400 "
        f"+ EXTRACT(HOUR FROM {diff}) * 3600 "
        f"+ EXTRACT(MINUTE FROM {diff}) * 60 "
        f"+ EXTRACT(SECOND FROM {diff}))"
    )


def interval_days(n: int, dialect: Dialect) -> str:
    """A SQL interval literal of ``n`` days.

    Postgres ``INTERVAL '<n> day'`` / Oracle ``INTERVAL '<n>' DAY``.
    SQLite has no INTERVAL type — date arithmetic uses the ``date()``
    function with a ``'+N days'`` modifier (see ``date_minus_days``);
    a bare interval literal isn't usable on its own. This helper
    returns the plain string ``'<n> days'`` for SQLite so callers
    that compose it with ``date(expr, '<interval>')`` work; sites
    that try to subtract a bare interval (e.g. ``RANGE BETWEEN
    INTERVAL ... PRECEDING``) should use ``range_interval_days``
    instead, which adapts to SQLite's numeric-only RANGE frames via
    a Julian-day projection.
    """
    if dialect is Dialect.POSTGRES:
        return f"INTERVAL '{n} day'"
    if dialect is Dialect.SQLITE:
        return f"'{n} days'"
    return f"INTERVAL '{n}' DAY"


def range_interval_days(n: int, dialect: Dialect) -> str:
    """Day-interval expression for use inside a window-function
    ``RANGE BETWEEN <expr> PRECEDING`` clause.

    PG / Oracle take ordinary INTERVAL literals — same form
    ``interval_days`` returns. SQLite's ``RANGE BETWEEN`` only
    accepts numeric expressions (the ORDER BY column must be numeric
    too), so the call site needs to project ``posted_day`` through
    ``julianday()`` and use a bare integer here. Returns ``str(n)``
    for SQLite so a ``RANGE BETWEEN N PRECEDING`` frame, paired with
    ``ORDER BY julianday(posted_day)``, gives the same per-day
    semantics PG / Oracle deliver via INTERVAL.
    """
    if dialect is Dialect.SQLITE:
        return str(n)
    return interval_days(n, dialect)


def order_by_day_expr(day_col: str, dialect: Dialect) -> str:
    """Per-dialect ``ORDER BY`` projection for date-keyed window
    functions paired with ``range_interval_days``.

    PG / Oracle: bare column name (intervals work directly against
    DATE / TIMESTAMP). SQLite: wrap in ``julianday(<col>)`` so the
    RANGE frame's numeric arithmetic lands on the same scale as
    ``range_interval_days(N, SQLITE) = str(N)``.
    """
    if dialect is Dialect.SQLITE:
        return f"julianday({day_col})"
    return day_col


def date_minus_days(date_expr: str, n: int, dialect: Dialect) -> str:
    """Subtract ``n`` days from a date expression.

    Postgres uses ``date - INTERVAL '<n> day'``; Oracle's DATE
    arithmetic interprets ``date - n`` as N days directly. SQLite
    uses ``date(expr, '-N days')``.
    """
    if dialect is Dialect.POSTGRES:
        return f"({date_expr} - {interval_days(n, dialect)})"
    if dialect is Dialect.SQLITE:
        return f"date({date_expr}, '-{n} days')"
    return f"({date_expr} - {n})"


def date_trunc_day(timestamp_expr: str, dialect: Dialect) -> str:
    """Truncate a timestamp expression to its day boundary, preserving
    a timestamp-shaped result type so downstream JOINs against
    TIMESTAMP columns don't fall through implicit conversion.

    Postgres ``DATE_TRUNC('day', expr)`` returns the same type as the
    input (TIMESTAMP → TIMESTAMP at 00:00:00). Oracle's ``TRUNC(X)``
    on a TIMESTAMP returns a DATE, which loses subseconds + the
    timestamp shape; wrapping in ``CAST(... AS TIMESTAMP)`` puts it
    back in the timestamp domain so the L1 invariant matviews compare
    equality the same way on both dialects. SQLite uses
    ``datetime(expr, 'start of day')`` — returns ``YYYY-MM-DD HH:MM:SS``
    text at midnight, sortable + groupable + JOIN-able against the
    other text-shaped timestamp columns.

    Distinct from ``to_date`` (which returns DATE-shape on both): use
    ``date_trunc_day`` when the result needs to behave as a timestamp
    in joins / comparisons; use ``to_date`` when the result is the
    final column the dashboard reads as a date.
    """
    if dialect is Dialect.POSTGRES:
        return f"DATE_TRUNC('day', {timestamp_expr})"
    if dialect is Dialect.SQLITE:
        return f"datetime({timestamp_expr}, 'start of day')"
    return f"CAST(TRUNC({timestamp_expr}) AS TIMESTAMP)"


def day_text(timestamp_expr: str, dialect: Dialect) -> str:
    """Render a timestamp expression as its ``YYYY-MM-DD`` day string.

    Unlike :func:`date_trunc_day` (which keeps a timestamp-shaped result
    for JOIN/equality against TIMESTAMP columns), this collapses to a
    plain text day key for comparisons that must tolerate *either* side
    being a string. Used by the Daily Statement balance-date narrow
    (AO.2/AO.10): the pushed-down ``<<$pL1DsBalanceDate>>`` param arrives
    as an ISO string in every renderer, and ``TRUNC(<string>)`` is an
    ORA-00932 on Oracle — comparing day-text sidesteps it. Pair with
    ``SUBSTR(<param>, 1, 10)`` on the param side (date or datetime → its
    ``YYYY-MM-DD`` prefix); ISO day strings also compare correctly with
    ``<`` / ``>=`` lexically.

    Postgres + Oracle: ``TO_CHAR(expr, 'YYYY-MM-DD')``. SQLite:
    ``strftime('%Y-%m-%d', expr)`` (the stored ``YYYY-MM-DD HH:MM:SS``
    text → its date portion).
    """
    if dialect is Dialect.SQLITE:
        return f"strftime('%Y-%m-%d', {timestamp_expr})"
    return f"TO_CHAR({timestamp_expr}, 'YYYY-MM-DD')"


def concat_agg(column_expr: str, separator: str, dialect: Dialect) -> str:
    """Aggregate text values from a group into a delimited string.

    First introduced for AB.3.3's ``_xor_group_violation`` matview
    (``fired_rails`` column — comma-separated rail names per Transfer).
    Dialect mapping:

    - Postgres: ``STRING_AGG(col, ',')`` — null-safe (NULLs skipped),
      no implicit ordering.
    - Oracle: ``LISTAGG(col, ',') WITHIN GROUP (ORDER BY col)`` —
      deterministic ordering by value; truncation at 4000 chars by
      default (acceptable for fired-rails: bounded by leg_rails count).
    - SQLite: ``GROUP_CONCAT(col, ',')`` — null-safe, no implicit
      ordering (matches PG's contract).

    ``separator`` is wrapped in single quotes; callers pass the bare
    delimiter (e.g., ``','`` or ``', '``). ``column_expr`` is interpolated
    raw — caller's responsibility to quote / cast as appropriate for
    the dialect.
    """
    sep_literal = f"'{separator}'"
    if dialect is Dialect.POSTGRES:
        return f"STRING_AGG({column_expr}, {sep_literal})"
    if dialect is Dialect.SQLITE:
        return f"GROUP_CONCAT({column_expr}, {sep_literal})"
    return (
        f"LISTAGG({column_expr}, {sep_literal}) "
        f"WITHIN GROUP (ORDER BY {column_expr})"
    )


# -- DDL idempotency ---------------------------------------------------------


def drop_table_if_exists(name: str, dialect: Dialect) -> str:
    """Idempotent DROP TABLE — emits CASCADE so dependent FKs / views
    drop transitively (where the dialect supports CASCADE).

    Postgres has native ``DROP TABLE IF EXISTS … CASCADE``. Oracle 19c
    needs a PL/SQL block that catches ORA-00942 (table not found).
    SQLite has ``DROP TABLE IF EXISTS …`` but no CASCADE keyword —
    SQLite enforces FK-cascading via ``PRAGMA foreign_keys`` plus
    ``ON DELETE CASCADE`` declarations on the FK; the schema we emit
    has no FKs, so omitting CASCADE has no behavioral impact.

    Returned string is **fully terminated** (Postgres trailing ``;``,
    Oracle ``END;`` PL/SQL terminator, SQLite trailing ``;``). Callers
    concatenate directly without appending ``;`` to avoid a
    double-semicolon that Oracle's PL/SQL parser rejects.
    """
    if dialect is Dialect.POSTGRES:
        return f"DROP TABLE IF EXISTS {name} CASCADE;"
    if dialect is Dialect.SQLITE:
        return f"DROP TABLE IF EXISTS {name};"
    return _oracle_drop_if_exists(
        f"DROP TABLE {name} CASCADE CONSTRAINTS", ignore_codes=(-942,),
    )


def drop_matview_if_exists(name: str, dialect: Dialect) -> str:
    """Idempotent DROP MATERIALIZED VIEW.

    Postgres ``DROP MATERIALIZED VIEW IF EXISTS …;`` / Oracle PL/SQL
    block catching ORA-12003 (matview does not exist). SQLite has no
    ``CREATE MATERIALIZED VIEW``: matviews are emitted as plain
    tables (``CREATE TABLE name AS SELECT …``), so the SQLite branch
    drops them as ordinary tables (``DROP TABLE IF EXISTS name;``).
    Returned string is **fully terminated** — same convention as
    ``drop_table_if_exists``.

    Oracle half-dropped-state hardening (Y.7-followup): a
    ``CREATE MATERIALIZED VIEW`` interrupted mid-flight (e.g. a
    SIGTERM-killed schema apply) can leave the matview's *container
    table* behind without the matview metadata — ``DROP MATERIALIZED
    VIEW`` then swallows ORA-12003 ("no such matview") while the stray
    table survives, and the next ``CREATE MATERIALIZED VIEW`` hits
    ORA-00955 ("name already used"). So the Oracle branch emits a
    ``DROP TABLE`` of the same name on the LINE AFTER the matview drop
    (swallowing ORA-00942 = nothing there, and ORA-12083 = it's a live
    container the matview drop already handled). On a healthy matview
    the table drop is a harmless no-op; on a half-dropped one it's the
    cleanup that makes the re-apply idempotent.

    The two blocks are joined with ``\n`` (not a space): ``split_oracle
    _script`` is a line scanner that treats a line beginning ``BEGIN``
    / ``DECLARE`` and ending ``END;`` as one PL/SQL block — two blocks
    on one line would be handed to ``cursor.execute`` as a single
    ``BEGIN…END; BEGIN…END;`` string, which Oracle's PL/SQL parser
    rejects (PLS-00103 on the second ``BEGIN``). All callers
    ``"\n".join`` the drop strings, so an internal newline is fine.
    """
    if dialect is Dialect.POSTGRES:
        return f"DROP MATERIALIZED VIEW IF EXISTS {name};"
    if dialect is Dialect.SQLITE:
        return f"DROP TABLE IF EXISTS {name};"
    return (
        _oracle_drop_if_exists(
            f"DROP MATERIALIZED VIEW {name}", ignore_codes=(-12003, -942),
        )
        + "\n"
        + _oracle_drop_if_exists(
            f"DROP TABLE {name} CASCADE CONSTRAINTS", ignore_codes=(-942, -12083),
        )
    )


def drop_index_if_exists(name: str, dialect: Dialect) -> str:
    """Idempotent DROP INDEX.

    Postgres ``DROP INDEX IF EXISTS …;`` / Oracle PL/SQL block
    catching ORA-01418 (index does not exist) / SQLite native
    ``DROP INDEX IF EXISTS …;``. Returned string is
    **fully terminated**.
    """
    if dialect is Dialect.POSTGRES:
        return f"DROP INDEX IF EXISTS {name};"
    if dialect is Dialect.SQLITE:
        return f"DROP INDEX IF EXISTS {name};"
    return _oracle_drop_if_exists(
        f"DROP INDEX {name}", ignore_codes=(-1418,),
    )


def drop_view_if_exists(name: str, dialect: Dialect) -> str:
    """Idempotent DROP VIEW.

    Postgres ``DROP VIEW IF EXISTS …;`` / Oracle PL/SQL block
    catching ORA-00942 / SQLite native ``DROP VIEW IF EXISTS …;``.
    Returned string is **fully terminated**.
    """
    if dialect is Dialect.POSTGRES:
        return f"DROP VIEW IF EXISTS {name};"
    if dialect is Dialect.SQLITE:
        return f"DROP VIEW IF EXISTS {name};"
    return _oracle_drop_if_exists(
        f"DROP VIEW {name}", ignore_codes=(-942,),
    )


def _oracle_drop_if_exists(
    drop_stmt: str, *, ignore_codes: tuple[int, ...],
) -> str:
    """Wrap an Oracle DROP statement in a PL/SQL block that swallows
    "does not exist" errors so the script is idempotent.

    Re-raises any other SQLCODE so genuine failures (privilege issues,
    bad syntax) still surface. ``ignore_codes`` lists the negative
    SQLCODE values to swallow per object type (e.g. ORA-00942 = -942
    for TABLE / VIEW; ORA-01418 = -1418 for INDEX; ORA-12003 = -12003
    for MATERIALIZED VIEW).
    """
    not_in = " AND ".join(f"SQLCODE != {c}" for c in ignore_codes)
    return (
        f"BEGIN EXECUTE IMMEDIATE '{drop_stmt}'; "
        f"EXCEPTION WHEN OTHERS THEN IF {not_in} THEN RAISE; END IF; END;"
    )


# -- Materialized views ------------------------------------------------------


def create_matview(name: str, body_sql: str, dialect: Dialect) -> str:
    """``CREATE MATERIALIZED VIEW`` with the right options per dialect.

    Postgres: bare ``CREATE MATERIALIZED VIEW name AS body`` (build-
    on-create + manual refresh are the defaults). Oracle: explicit
    ``BUILD IMMEDIATE REFRESH ON DEMAND`` so behavior matches the
    Postgres expectation; without those options Oracle defaults to
    ``REFRESH FORCE ON DEMAND`` (incremental fast-refresh attempt
    first), which has more setup requirements. SQLite has no
    ``CREATE MATERIALIZED VIEW``: emits ``CREATE TABLE name AS body``
    (the matview becomes a plain table populated at create time).
    Refresh becomes ``DELETE FROM name; INSERT INTO name <body>;``
    (see ``refresh_matview``).

    Note this helper does NOT add a trailing ``;`` — it's a one-shot
    convenience for callers that want the whole CREATE in one string,
    but most callers in this codebase splice the ``BUILD IMMEDIATE …``
    suffix into a template via ``matview_options(dialect)`` instead
    so the SELECT body stays inline + readable.
    """
    if dialect is Dialect.POSTGRES:
        return f"CREATE MATERIALIZED VIEW {name} AS {body_sql}"
    if dialect is Dialect.SQLITE:
        return f"CREATE TABLE {name} AS {body_sql}"
    return (
        f"CREATE MATERIALIZED VIEW {name} "
        f"BUILD IMMEDIATE REFRESH COMPLETE ON DEMAND AS {body_sql}"
    )


def matview_options(dialect: Dialect) -> str:
    """Per-dialect suffix between ``CREATE MATERIALIZED VIEW <name>`` and
    ``AS <body>``. Postgres takes none; Oracle needs ``BUILD IMMEDIATE
    REFRESH COMPLETE ON DEMAND`` to match Postgres's build-on-create +
    manual-REFRESH semantics (without it Oracle defaults to
    ``REFRESH FORCE ON DEMAND``, which has more setup requirements).
    SQLite uses ``CREATE TABLE … AS`` (see ``matview_create_keyword``);
    no per-keyword suffix applies, so returns the empty string.

    Used by ``common.l2.schema`` to splice the suffix into per-matview
    template strings (so the SELECT body stays inline + readable).
    Returns the empty string on Postgres + SQLite so the substitution
    is a no-op.
    """
    if dialect is Dialect.POSTGRES:
        return ""
    if dialect is Dialect.SQLITE:
        return ""
    return " BUILD IMMEDIATE REFRESH COMPLETE ON DEMAND"


def matview_create_keyword(dialect: Dialect) -> str:
    """The ``CREATE …`` keyword the matview templates emit per dialect.

    Postgres + Oracle: ``CREATE MATERIALIZED VIEW``. SQLite: just
    ``CREATE TABLE`` (matviews land as plain tables — refresh
    becomes a DELETE + INSERT pair, see ``refresh_matview``). Used by
    ``common.l2.schema`` so the per-matview template strings can stay
    one-line + dialect-clean instead of branching at every site.
    """
    if dialect is Dialect.SQLITE:
        return "CREATE TABLE"
    return "CREATE MATERIALIZED VIEW"


def refresh_matview(name: str, dialect: Dialect) -> str:
    """``REFRESH MATERIALIZED VIEW`` per dialect.

    Postgres: ``REFRESH MATERIALIZED VIEW name;``. Oracle: a PL/SQL
    block invoking ``DBMS_MVIEW.REFRESH('name', method => 'C')`` —
    ``C`` = complete refresh, matching Postgres semantics. SQLite:
    NOT a single statement — refresh on a matview-as-table is a
    DELETE + INSERT pair, but the body SELECT lives in the schema
    template, not here. The SQLite branch returns a sentinel that
    callers in ``common.l2.schema.refresh_matviews_sql`` route
    through ``_emit_sqlite_refresh_block`` (which knows the SELECT
    body for each matview). Returned string is
    **fully terminated** for PG / Oracle.
    """
    if dialect is Dialect.POSTGRES:
        return f"REFRESH MATERIALIZED VIEW {name};"
    if dialect is Dialect.SQLITE:
        # Sentinel — the per-matview SELECT body is needed to refresh,
        # which the helper here doesn't know. ``refresh_matviews_sql``
        # in ``common.l2.schema`` substitutes the right body.
        raise NotImplementedError(
            "SQLite refresh requires the matview body SELECT; call "
            "refresh_matviews_sql in common.l2.schema instead.",
        )
    return f"BEGIN DBMS_MVIEW.REFRESH('{name}', method => 'C'); END;"


def analyze_table(name: str, dialect: Dialect) -> str:
    """Refresh planner statistics on a table or matview.

    Postgres: ``ANALYZE name;``. Oracle: ``BEGIN
    DBMS_STATS.GATHER_TABLE_STATS(USER, 'name'); END;``. SQLite:
    ``ANALYZE name;`` (same syntax as Postgres). Returned string is
    **fully terminated**.
    """
    if dialect is Dialect.POSTGRES:
        return f"ANALYZE {name};"
    if dialect is Dialect.SQLITE:
        return f"ANALYZE {name};"
    return f"BEGIN DBMS_STATS.GATHER_TABLE_STATS(USER, '{name}'); END;"


# -- Constant SELECT (no real source table) ---------------------------------


def dual_from(dialect: Dialect) -> str:
    """Suffix that makes a constant SELECT valid on both dialects.

    Postgres accepts ``SELECT 'x' AS col`` with no FROM. Oracle 19c
    requires every SELECT to have a FROM clause; the canonical Oracle
    pseudo-table for "one row, no real source" is ``dual``. SQLite
    accepts the bare ``SELECT`` (like Postgres).

    Returns ``""`` on Postgres + SQLite and ``" FROM dual"`` on
    Oracle. Compose inline at the end of the SELECT list:
    ``f"SELECT {expr} AS col{dual_from(dialect)}"``. Combine with
    ``WHERE 1=0`` (works on every dialect) for an empty-row sentinel
    branch — ``WHERE FALSE`` is Postgres-only and breaks Oracle.
    """
    if dialect is Dialect.POSTGRES:
        return ""
    if dialect is Dialect.SQLITE:
        return ""
    return " FROM dual"


# -- Recursive CTE -----------------------------------------------------------


def with_recursive(dialect: Dialect) -> str:
    """Recursive-CTE preamble keyword.

    Postgres requires the explicit ``WITH RECURSIVE`` keyword. Oracle
    19c infers recursion from the CTE body's self-reference and
    accepts (but does not require) ``RECURSIVE`` — emit just ``WITH``
    for portability across older Oracle releases. SQLite requires
    ``WITH RECURSIVE`` (same as Postgres).
    """
    if dialect is Dialect.POSTGRES:
        return "WITH RECURSIVE"
    if dialect is Dialect.SQLITE:
        return "WITH RECURSIVE"
    return "WITH"
