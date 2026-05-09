"""Dataset column contracts and shared dataset-building helpers.

A DatasetContract declares the column interface a dataset produces.
The SQL is one implementation of that contract (against the demo schema);
customers swap in their own SQL. Everything downstream (visuals, filters,
drill-downs) binds to contract columns, not SQL specifics.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from quicksight_gen.common.config import Config
from quicksight_gen.common.models import (
    CustomSql,
    DataSet,
    DatasetParameter,
    DataSetUsageConfiguration,
    InputColumn,
    LogicalTable,
    LogicalTableSource,
    PhysicalTable,
    ResourcePermission,
)
from quicksight_gen.common.sql import Dialect, column_name


class ColumnShape(Enum):
    """Application-level value shape of a drill-eligible column.

    Layered above the AWS coarse type (STRING/DATETIME/...) so that two
    columns sharing a wire type but representing different semantic values
    cannot be cross-wired to the same drill parameter. K.2 spike found a
    silent zero-row bug where ``exception_date`` (DATETIME) was bound to
    a SINGLE_VALUED string parameter; QuickSight coerced it to the full
    timestamp text ``"2026-04-07 00:00:00.000"`` which never matched the
    destination's ``posted_date`` column (also STRING but ``YYYY-MM-DD``
    formatted via TO_CHAR). The shape captures both the encoding and the
    semantic, so the typed drill helper can refuse the wiring at code-gen
    time instead of silently producing zero rows.

    Tag a column with a shape only if it's an actual drill source or
    destination — every other column stays ``shape=None`` and is rejected
    by ``DrillSourceField`` resolution.
    """

    # Date encodings ---------------------------------------------------
    # YYYY-MM-DD text, e.g. ``TO_CHAR(posted_at, 'YYYY-MM-DD')``. Compatible
    # with SINGLE_VALUED string params bound to TO_CHAR-formatted columns.
    DATE_YYYY_MM_DD_TEXT = "date_yyyy_mm_dd_text"
    # True DATETIME column, suitable for a DateTimeParameter target. Not
    # interchangeable with the YYYY-MM-DD text shape — different wire type
    # on both ends.
    DATETIME_DAY = "datetime_day"

    # Account identifiers — distinct nominal types so writing an
    # account_id into a parameter expecting a transfer_id fails.
    # SUBLEDGER_ACCOUNT_ID and LEDGER_ACCOUNT_ID are subtypes of
    # ACCOUNT_ID: a sub-ledger or ledger id is always a valid account
    # id, but not vice versa. Assignment compatibility encodes this.
    ACCOUNT_ID = "account_id"
    SUBLEDGER_ACCOUNT_ID = "subledger_account_id"
    LEDGER_ACCOUNT_ID = "ledger_account_id"
    # Concatenated display label, e.g. ``"Sasquatch Sips (gl-1850)"``.
    # Used as a single-string surrogate that is both human-readable
    # AND uniquely keyed (the embedded id disambiguates name
    # collisions). Wired to the K.4.8 Account Network anchor parameter
    # so the Sankey can self-walk: clicking a node delivers the node's
    # display label, the calc field compares displays, the dropdown
    # shows the same labels. Not assignable to ACCOUNT_ID because the
    # id-only consumer can't parse the label back out.
    ACCOUNT_DISPLAY = "account_display"

    # Transfer identifiers
    TRANSFER_ID = "transfer_id"
    TRANSFER_TYPE = "transfer_type"

    # PR identifiers
    SETTLEMENT_ID = "settlement_id"
    PAYMENT_ID = "payment_id"
    EXTERNAL_TXN_ID = "external_txn_id"

    # L2-declared name (a Rail.name or TransferTemplate.name — both
    # Identifiers in the L2 SPEC). Used by the L2 Flow Tracing
    # Exceptions table's drill, which writes ``entity_a`` (a STRING
    # holding either a rail or template name depending on check_type)
    # into the destination sheet's filter parameter. Both Rails sheet
    # (``rail_name`` column) and Chains sheet (``parent_chain_name``
    # column) accept this shape — the chain parent column legitimately
    # holds either a rail OR a template name per SPEC.
    L2_DECLARED_NAME = "l2_declared_name"

    def can_assign_to(self, other: "ColumnShape") -> bool:
        """True iff a value of ``self`` is acceptable into a ``other`` param.

        Identical shapes are always assignable. SUBLEDGER_ACCOUNT_ID and
        LEDGER_ACCOUNT_ID widen to ACCOUNT_ID (the destination
        ``daily_balances.account_id`` column holds both ledger and
        sub-ledger ids). Date encodings do NOT widen — DATETIME and
        YYYY-MM-DD text are different wire types and cross-wiring them
        is the K.2 bug class.
        """
        if self is other:
            return True
        if other is ColumnShape.ACCOUNT_ID and self in (
            ColumnShape.SUBLEDGER_ACCOUNT_ID,
            ColumnShape.LEDGER_ACCOUNT_ID,
        ):
            return True
        return False


@dataclass
class ColumnSpec:
    """Declared column on a dataset's contract.

    ``display_name`` (v8.5.0): plain-English header label QuickSight
    table visuals use as the column header. When omitted, defaults to
    a title-cased rewrite of the snake_case ``name`` — e.g.
    ``account_id`` → "Account ID" (with smart-uppercasing of common
    initialisms via ``_smart_title``). Override when the auto-derived
    form is awkward — e.g. ``amount_money`` defaults to "Amount
    Money", but Investigation tables read better with the explicit
    override "Amount" since the surrounding context already implies
    money.
    """
    name: str
    type: str  # STRING | DECIMAL | INTEGER | DATETIME | BIT
    shape: ColumnShape | None = None  # only set for drill-eligible columns
    display_name: str | None = None

    @property
    def human_name(self) -> str:
        """Plain-English header label for this column.

        ``display_name`` if set, else snake_case → "Title Case" with
        common initialisms preserved (id → ID, eod → EOD, etc.).
        """
        if self.display_name is not None:
            return self.display_name
        return _smart_title(self.name)

    def to_input_column(self, dialect: Dialect) -> InputColumn:
        """Emit the ``InputColumn`` shape QuickSight reads as Dataset.Columns.

        The emitted ``Name`` is the column's **dialect-natural unquoted-
        identifier case**: lowercase on Postgres + SQLite, UPPERCASE on
        Oracle. QuickSight quotes this name verbatim when building visual
        queries (``SELECT "<name>" FROM (<custom_sql>)``); each engine's
        unquoted-DDL columns are stored in that same natural case, so a
        case-correct quoted reference finds the column without the
        case-bridging wrapper that f.4 will drop.
        """
        return InputColumn(Name=column_name(self.name, dialect), Type=self.type)


# Initialisms that should stay uppercase in the auto-derived label.
# These are the snake_case word forms (lowercase, no separators) we'll
# uppercase after the title() call. Picked from the column names in
# the shipped 4 apps' contracts; extend here as new ones surface.
_INITIALISMS: frozenset[str] = frozenset({
    "id", "eod", "url", "sql", "json", "uuid", "ip", "api",
    "aws", "qs", "etl", "csv", "tsv", "uri", "tz", "utc",
})


def _smart_title(snake: str) -> str:
    """Convert ``snake_case_with_id`` → ``"Snake Case With ID"``.

    Standard ``str.title()`` on the result of ``replace("_", " ")``
    would produce "Snake Case With Id" — common initialisms get
    awkward. This helper post-processes the title-cased words and
    re-uppercases any token whose lowercased form is in
    ``_INITIALISMS``.
    """
    titled = snake.replace("_", " ").title()
    return " ".join(
        word.upper() if word.lower() in _INITIALISMS else word
        for word in titled.split(" ")
    )


@dataclass
class DatasetContract:
    columns: list[ColumnSpec]

    def to_input_columns(self, dialect: Dialect) -> list[InputColumn]:
        """Emit the full ``Columns`` list QuickSight reads as Dataset metadata.

        Forwards ``dialect`` to each ``ColumnSpec.to_input_column`` so the
        emitted name is dialect-natural (UPPERCASE on Oracle; lowercase on
        PG + SQLite). See ``ColumnSpec.to_input_column`` for the why.
        """
        return [c.to_input_column(dialect) for c in self.columns]

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> ColumnSpec:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(
            f"Column {name!r} not declared on this contract. Known: "
            f"{self.column_names}"
        )


# Module-level registry of visual_identifier -> contract. Populated by
# build_dataset() at construction time so that downstream drill code can
# look up a column's shape from just the visual identifier (the same id
# the visuals pass as ``DataSetIdentifier=`` in field references) and
# the column name. The alternative — threading the contract through
# every visual call site — would fight the existing visual-builder
# shape (which already imports the ``DS_*`` constants).
_CONTRACT_REGISTRY: dict[str, DatasetContract] = {}


def register_contract(
    visual_identifier: str, contract: DatasetContract,
) -> None:
    """Register a visual_identifier -> contract mapping for shape lookup.

    The key is the visual identifier (e.g. ``"ar-ledger-balance-drift-ds"``),
    the same string the visuals use as ``DataSetIdentifier=`` and that the
    analysis maps to a real DataSet ARN via DataSetIdentifierDeclaration.

    Idempotent for the same (visual_identifier, contract) pair; raises if a
    different contract is already registered under the same identifier
    (catches accidental identifier collisions).
    """
    existing = _CONTRACT_REGISTRY.get(visual_identifier)
    if existing is not None and existing is not contract:
        raise ValueError(
            f"visual_identifier {visual_identifier!r} already registered to "
            f"a different contract instance. Two datasets cannot share an "
            f"identifier."
        )
    _CONTRACT_REGISTRY[visual_identifier] = contract


def get_contract(visual_identifier: str) -> DatasetContract:
    """Look up the contract registered under ``visual_identifier``.

    Raises ``KeyError`` if not registered — usually means the dataset
    hasn't been built yet in the current process. Tests / generators
    should call ``build_dataset()`` before reaching code that resolves
    drill source fields.
    """
    try:
        return _CONTRACT_REGISTRY[visual_identifier]
    except KeyError:
        raise KeyError(
            f"No contract registered for visual_identifier "
            f"{visual_identifier!r}. Call build_dataset() for it before "
            f"resolving drill sources."
        )


# Module-level registry of visual_identifier → SQL string. X.2.g.0
# uses this to resolve a Visual's dataset SQL at fetcher-build time
# without each app having to expose a parallel lookup. Populated by
# build_dataset() right after the Oracle alias-wrapper transform — so
# the SQL stored here is the dialect-correct form (the same string
# that lands inside ``CustomSql.SqlQuery`` on the AWS DataSet).
_SQL_REGISTRY: dict[str, str] = {}


def register_sql(visual_identifier: str, sql: str) -> None:
    """Register a visual_identifier → dataset SQL mapping.

    Idempotent for the same (visual_identifier, sql) pair; the second
    call with the same identifier overwrites with the new SQL (matches
    the typical "rebuild for a different dialect" workflow — same
    identifier, dialect-specific SQL). Tests that need to assert
    "build was idempotent" should snapshot the registry before /
    after.
    """
    _SQL_REGISTRY[visual_identifier] = sql


def get_sql(visual_identifier: str) -> str:
    """Look up the SQL registered under ``visual_identifier``.

    Raises ``KeyError`` if not registered — usually means the dataset
    hasn't been built yet in the current process (call
    ``build_all_datasets(cfg)`` from the relevant app before reaching
    fetcher-construction code).
    """
    try:
        return _SQL_REGISTRY[visual_identifier]
    except KeyError:
        raise KeyError(
            f"No SQL registered for visual_identifier "
            f"{visual_identifier!r}. Call build_dataset() (typically via "
            f"the app's build_all_datasets(cfg)) before constructing the "
            f"App2 tree fetcher."
        )


DATASET_ACTIONS = [
    "quicksight:DescribeDataSet",
    "quicksight:DescribeDataSetPermissions",
    "quicksight:PassDataSet",
    "quicksight:DescribeIngestion",
    "quicksight:ListIngestions",
    "quicksight:UpdateDataSet",
    "quicksight:DeleteDataSet",
    "quicksight:CreateIngestion",
    "quicksight:CancelIngestion",
    "quicksight:UpdateDataSetPermissions",
]


def dataset_permissions(cfg: Config) -> list[ResourcePermission] | None:
    if not cfg.principal_arns:
        return None
    return [
        ResourcePermission(Principal=arn, Actions=DATASET_ACTIONS)
        for arn in cfg.principal_arns
    ]


def _oracle_lowercase_alias_wrapper(
    sql: str, contract: DatasetContract, cfg: Config,
) -> str:
    """Wrap Oracle CustomSQL with an alias-rename outer SELECT.

    **Pre-Y.3.f:** the wrapper bridged QuickSight's lowercase Columns
    declaration against Oracle's UPPERCASE-stored unquoted identifiers
    (``qs_inner."ACCOUNT_ID" AS "account_id"``). Without it every
    Oracle visual failed with ``ORA-00904: "account_id": invalid
    identifier``.

    **Post-Y.3.f.2 (current):** ``DatasetContract.to_input_columns``
    now case-folds the QS Columns name to dialect-natural case
    (UPPERCASE on Oracle), so QS quotes ``"ACCOUNT_ID"`` directly.
    The wrapper still wraps but the alias side now matches
    (``qs_inner."ACCOUNT_ID" AS "ACCOUNT_ID"``) — functionally a
    no-op rename. **Y.3.f.4 drops the wrapper entirely** (the inner
    SELECT projects UPPERCASE columns natively, QS's quoted-UPPERCASE
    references match without an outer SELECT).

    No-op on Postgres + SQLite (both fold unquoted identifiers to
    lowercase; the existing SQL works without rewrapping).
    """
    from quicksight_gen.common.sql import Dialect

    if cfg.dialect is not Dialect.ORACLE:
        return sql
    # Y.3.f.2: alias to dialect-natural case (UPPERCASE on Oracle) so the
    # wrapper output matches the case-folded ``Columns`` QuickSight now
    # declares post-f.2. f.4 drops this wrapper entirely — both sides are
    # then a no-op and removing the outer SELECT is byte-clean.
    aliases = ", ".join(
        f'qs_inner."{c.name.upper()}" AS "{column_name(c.name, cfg.dialect)}"'
        for c in contract.columns
    )
    return f"SELECT {aliases} FROM (\n{sql}\n) qs_inner"


def build_dataset(
    cfg: Config,
    dataset_id: str,
    name: str,
    table_key: str,
    sql: str,
    contract: DatasetContract,
    visual_identifier: str,
    dataset_parameters: list[DatasetParameter] | None = None,
    app2_sql: str | None = None,
) -> DataSet:
    """Build an AWS-shape DataSet.

    ``dataset_parameters``: optional list of dataset-level parameters
    that get substituted into ``sql`` via the ``<<$paramName>>``
    syntax at QuickSight query time. Bridge to analysis params via
    ``MappedDataSetParameters`` on the analysis ParameterDeclaration.

    ``app2_sql`` (X.2.g.1.b): optional App2-dialect SQL variant
    registered for the HTMX dialect's tree fetcher (X.2.g.0). QS
    uses the ``<<$paramName>>`` substitution mechanism for filter
    values; App2's executor (``_sql_executor``) uses ``:name``-style
    bind placeholders. They're incompatible at the SQL-string level,
    so apps that need filter-bound SQL provide both: the QS variant
    in ``sql`` (hits ``CustomSql.SqlQuery``); the App2 variant in
    ``app2_sql`` (hits the registry that ``make_tree_db_fetcher``
    consumes). When omitted, both dialects see the same ``sql``.
    """
    sql = _oracle_lowercase_alias_wrapper(sql, contract, cfg)
    # X.2.g.0 / X.2.g.1.b — register the dialect-correct SQL so the
    # App2 tree fetcher can resolve a Visual's dataset SQL by
    # visual_identifier. App2-specific variant wins when provided
    # (same Oracle alias-wrapper transform applied for parity).
    if app2_sql is not None:
        app2_sql = _oracle_lowercase_alias_wrapper(app2_sql, contract, cfg)
        register_sql(visual_identifier, app2_sql)
    else:
        register_sql(visual_identifier, sql)
    columns = contract.to_input_columns(cfg.dialect)
    # Config.__post_init__ guarantees datasource_arn is non-None
    # post-construction (raises if neither it nor demo_database_url
    # is provided). The dataclass default is None for ergonomics, but
    # by the time build_dataset runs the value is a real ARN string.
    assert cfg.datasource_arn is not None
    physical = {
        table_key: PhysicalTable(
            CustomSql=CustomSql(
                Name=name,
                DataSourceArn=cfg.datasource_arn,
                SqlQuery=sql,
                Columns=columns,
            )
        )
    }
    logical = {
        f"{table_key}-logical": LogicalTable(
            Alias=name,
            Source=LogicalTableSource(PhysicalTableId=table_key),
        )
    }
    register_contract(visual_identifier, contract)
    return DataSet(
        AwsAccountId=cfg.aws_account_id,
        DataSetId=dataset_id,
        Name=name,
        PhysicalTableMap=physical,
        LogicalTableMap=logical,
        ImportMode="DIRECT_QUERY",
        DataSetUsageConfiguration=DataSetUsageConfiguration(),
        Permissions=dataset_permissions(cfg),
        Tags=cfg.tags(),
        DatasetParameters=dataset_parameters,
    )
