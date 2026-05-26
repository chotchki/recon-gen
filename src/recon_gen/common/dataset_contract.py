"""Dataset column contracts and shared dataset-building helpers.

A DatasetContract declares the column interface a dataset produces.
The SQL is one implementation of that contract (against the demo schema);
customers swap in their own SQL. Everything downstream (visuals, filters,
drill-downs) binds to contract columns, not SQL specifics.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from recon_gen.common.config import Config
from recon_gen.common.models import (
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
    # Rail name — the L2-declared Rail.name (Z.B subsumed
    # ``transfer_type`` into ``rail_name`` 2026-05-15; the
    # ``<prefix>_transactions.rail_name`` column carries the value).
    RAIL_NAME = "rail_name"

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


class Storage(Enum):
    """BH.24 (2026-05-25) — per-column storage shape.

    Distinguishes columns whose values come back from the DB driver as
    **raw BIGINT cents** (the AO.1.impl Studio slice's matview storage
    contract) from columns whose values come back as **already-converted
    float / Decimal dollars** (the legacy pre-AO.1 pattern where the
    dataset SELECT wraps with ``cents_to_dollars_sql``).

    Why this matters: App2's ``_measure_sql`` and ``_apply_cents_to_dollars``
    both divide currency values by 100 at the renderer layer per AO.1's
    "matview is cents, renderer converts" convention. If the dataset SQL
    ALSO divides (the production L1 / Inv / Exec / L2FT datasets all do
    today via ``cents_to_dollars_sql``), the result is 100× off (BG.7
    surfaced this on the Daily Statement KPIs: rendered -$11,993.10 vs
    matview -$1,199,309.68).

    Default ``DOLLARS`` — matches today's production behavior (every
    dataset pre-converts in SQL). Storage.CENTS is explicitly opt-in
    for columns that legitimately project raw cents (typically Studio
    bare-matview reads). ``currency=True`` becomes a pure FORMAT flag
    (USD symbol + 2-decimal QS format / "$" prefix on App2); the
    cents→dollars divide is gated on Storage.CENTS, not on currency=True.
    """

    DOLLARS = "dollars"
    CENTS = "cents"


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

    ``currency`` + ``storage`` (BH.24, 2026-05-25): explicit currency-
    column declaration. ``currency=True`` tells renderers to apply USD
    formatting (``$`` prefix, 2 decimals). ``storage`` declares how
    the value comes back from the DB driver — ``DOLLARS`` (default,
    matches today's "dataset SQL pre-converts via cents_to_dollars_sql"
    production pattern) or ``CENTS`` (raw BIGINT cents, renderer must
    divide). The two are independent: a column can be currency=True
    + storage=DOLLARS (the production case), currency=True + storage=
    CENTS (raw-matview Studio access), currency=False + storage=DOLLARS
    (a non-money decimal), etc. Renderers consult ``storage`` to decide
    whether to apply the /100 divide; ``currency`` only drives format.
    """
    name: str
    type: str  # STRING | DECIMAL | INTEGER | DATETIME | BIT
    shape: ColumnShape | None = None  # only set for drill-eligible columns
    display_name: str | None = None
    currency: bool = False
    storage: Storage = Storage.DOLLARS

    @property
    def human_name(self) -> str:
        """Plain-English header label for this column.

        ``display_name`` if set, else snake_case → "Title Case" with
        common initialisms preserved (id → ID, eod → EOD, etc.).
        """
        if self.display_name is not None:
            return self.display_name
        return _smart_title(self.name)

    def to_input_column(self) -> InputColumn:
        return InputColumn(Name=self.name, Type=self.type)


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

    def to_input_columns(self) -> list[InputColumn]:
        return [c.to_input_column() for c in self.columns]

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


# Y.2.app2.cde — visual_identifier → the dataset's QS `DatasetParameter`
# list. App2's `_sql_executor` reads this to resolve a `<<$paramName>>`
# placeholder's *default* (string-substituted) when the URL doesn't
# supply that param — keeping the freshly-loaded App2 page consistent
# with how QuickSight renders the dashboard (where the dataset
# parameter's DefaultValues apply on initial load). Empty list for
# datasets with no parameters; populated by `build_dataset()` alongside
# `register_sql`.
_DSP_REGISTRY: dict[str, list[DatasetParameter]] = {}


def register_dataset_params(
    visual_identifier: str, params: list[DatasetParameter],
) -> None:
    """Register a visual_identifier → dataset-parameter list mapping.

    Same overwrite-on-repeat semantics as ``register_sql``. Pass ``[]``
    (or omit, via ``build_dataset(dataset_parameters=None)``) for a
    dataset with no parameters — ``get_dataset_params`` then returns
    ``[]`` and the App2 executor leaves any stray placeholder for the
    bind-variable fallback.
    """
    _DSP_REGISTRY[visual_identifier] = list(params)


def get_dataset_params(visual_identifier: str) -> list[DatasetParameter]:
    """Look up the dataset-parameter list registered under
    ``visual_identifier``. Returns ``[]`` if nothing was registered —
    unlike ``get_sql`` this is not an error (most datasets have no
    parameters; the App2 executor handles the empty case gracefully).
    """
    return list(_DSP_REGISTRY.get(visual_identifier, []))


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
    """Wrap Oracle CustomSQL with a lowercase-aliased outer SELECT.

    Oracle case-folds unquoted identifiers to UPPERCASE at parse time,
    so a CustomSQL like ``SELECT * FROM <matview>`` (matview built with
    unquoted DDL columns) returns ACCOUNT_ID, not account_id, in the
    column metadata. QuickSight then quotes the lowercase column names
    from its declared ``Columns`` list when building visual queries
    (``SELECT "account_id" FROM (<custom_sql>)``), and Oracle responds
    with ``ORA-00904: "account_id": invalid identifier`` because no
    such case-preserved identifier exists.

    Fix: re-alias every projected column from its UPPERCASE
    Oracle-stored form to a lowercase double-quoted alias matching
    what QS expects. The wrapper is keyed off the contract column
    names — those ARE the QS-side column names — so the alias list
    is generated from the same source of truth that QS reads.

    No-op on Postgres (it folds unquoted identifiers to lowercase by
    default; the existing SQL works without rewrapping).
    """
    from recon_gen.common.sql import Dialect

    if cfg.dialect is not Dialect.ORACLE:
        return sql
    aliases = ", ".join(
        f'qs_inner."{c.name.upper()}" AS "{c.name}"' for c in contract.columns
    )
    return f"SELECT {aliases} FROM (\n{sql}\n) qs_inner"


_DSP_VARIANT_FIELDS = (
    "StringDatasetParameter",
    "IntegerDatasetParameter",
    "DecimalDatasetParameter",
    "DateTimeDatasetParameter",
)


def _assign_dataset_param_ids(
    dataset_id: str, params: list[DatasetParameter],
) -> list[DatasetParameter]:
    """AK.1 — stamp each dataset parameter with a deterministic,
    dataset-scoped UUID.

    QuickSight requires every ``DataSetParameter.Id`` to be a real UUID
    and rejects an analysis whose datasets carry colliding parameter Ids.
    Construction sites leave ``Id`` unset (``""``); here we derive
    ``auto_id(f"{dataset_id}:dsparam:{Name}")`` — a UUIDv5 that is stable
    across runs (deterministic emit / idempotent deploy) yet unique per
    (dataset, param name), so two datasets that share a param name (e.g.
    ``pKey`` across several L2FT datasets) never collide.
    """
    from recon_gen.common.tree._helpers import auto_id

    out: list[DatasetParameter] = []
    for p in params:
        for field_name in _DSP_VARIANT_FIELDS:
            variant = getattr(p, field_name)
            if variant is not None:
                new_id = auto_id(f"{dataset_id}:dsparam:{variant.Name}")
                out.append(
                    replace(p, **{field_name: replace(variant, Id=new_id)})
                )
                break
        else:  # pragma: no cover — a wrapper with no variant set is a bug
            out.append(p)
    return out


def build_dataset(
    cfg: Config,
    dataset_id: str,
    name: str,
    table_key: str,
    sql: str,
    contract: DatasetContract,
    visual_identifier: str,
    dataset_parameters: list[DatasetParameter] | None = None,
    *,
    app2_date_column: str | None = None,
) -> DataSet:
    """Build an AWS-shape DataSet.

    ``dataset_parameters``: optional list of dataset-level parameters
    that get substituted into ``sql`` via the ``<<$paramName>>``
    syntax at QuickSight query time. Bridge to analysis params via
    ``MappedDataSetParameters`` on the analysis ParameterDeclaration.

    ``app2_date_column`` (Y.5.a, replaced raw ``app2_sql=``): name of
    the date column the universal date-range filter narrows on. When
    set, ``sql`` is treated as a template containing a literal
    ``{date_filter}`` placeholder, and ``build_dataset`` emits both
    SQL variants:

    - QS gets ``sql.format(date_filter="")`` — the analysis-level
      ``TimeRangeFilter`` narrows after-the-fact.
    - App2 gets ``sql.format(date_filter=app2_date_filter(
      app2_date_column, cfg.dialect))`` — the ``:date_from`` /
      ``:date_to`` URL binds narrow at the DB.

    Pre-Y.5.a callers passed an already-formatted ``app2_sql=`` string
    plus an empty ``{date_filter}`` substitution into ``sql``. That
    repeated boilerplate (``sql_template.format(date_filter=
    app2_date_filter("col", cfg.dialect))``) at every call site and
    silently broke when the operator forgot the App2 variant. The
    new shape lets the dataset declare *the date column*, and
    ``build_dataset`` handles substitution + registration.
    """
    if app2_date_column is not None:
        # Y.5.a — sql is a template with a {date_filter} slot.
        from recon_gen.common.sql import app2_date_filter
        qs_sql = sql.format(date_filter="")
        app2_sql: str | None = sql.format(
            date_filter=app2_date_filter(app2_date_column, cfg.dialect),
        )
    else:
        qs_sql = sql
        app2_sql = None
    sql = _oracle_lowercase_alias_wrapper(qs_sql, contract, cfg)
    # X.2.g.0 / X.2.g.1.b — register the dialect-correct SQL so the
    # App2 tree fetcher can resolve a Visual's dataset SQL by
    # visual_identifier. App2-specific variant wins when provided
    # (same Oracle alias-wrapper transform applied for parity).
    if app2_sql is not None:
        app2_sql = _oracle_lowercase_alias_wrapper(app2_sql, contract, cfg)
        register_sql(visual_identifier, app2_sql)
    else:
        register_sql(visual_identifier, sql)
    # Y.2.app2.cde — register the dataset's QS parameters too, so the
    # App2 executor can resolve a `<<$paramName>>` placeholder's default
    # (string-substituted) when the URL doesn't supply that param.
    # AK.1 — assign deterministic dataset-scoped UUIDs before registering
    # + emitting. App2 keys off the param Name, so the Id remap is QS-side
    # only; QS rejects colliding/non-UUID parameter Ids across an analysis.
    params = (
        _assign_dataset_param_ids(dataset_id, dataset_parameters)
        if dataset_parameters else None
    )
    register_dataset_params(visual_identifier, params or [])
    columns = contract.to_input_columns()
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
        DatasetParameters=params,
    )
