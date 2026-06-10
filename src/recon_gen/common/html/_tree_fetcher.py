"""X.2.g.0 — generic per-tree DataFetcher factory.

Given a tree ``App``, walk its visuals and build an async
``DataFetcher`` that dispatches by ``visual_id``. The fetcher
resolves each visual's dataset SQL from the registry populated by
``build_dataset()`` (in ``common/dataset_contract.py``), executes
via ``_sql_executor.execute_visual_sql_async``, and shapes via
``_data_shape.shape_for_kind``.

Per-app wiring (X.2.g.1 onward):

    from recon_gen.apps.executives.app import build_executives_app
    from recon_gen.apps.executives.datasets import build_all_datasets
    from recon_gen.common.db import make_connection_pool
    build_all_datasets(cfg)         # populates the SQL registry
    tree_app = build_executives_app(cfg, l2_instance=instance)
    pool = await make_connection_pool(cfg, max_size=10)
    fetcher = make_tree_db_fetcher(tree_app, cfg, pool=pool)

No per-app fetcher code. The tree is the source of truth; visual
kinds drive the shape; dataset identifiers drive the SQL.

Visuals without a recoverable dataset (e.g. ``SheetTextBox``,
text-only Info panels) get a ``visual_id → None`` mapping at build
time, and the fetcher returns an empty payload for them — the d3
hydrators handle empty payloads gracefully.

X.2.n.4 — the fetcher is now ``async def``. The tree walk +
SQL-registry resolution + ``wrap_for_visual`` + ``shape_for_kind``
remain sync (pure CPU); only the SQL-execute roundtrip is awaited.
``DataFetcher`` is the new ``Awaitable``-returning type alias;
``SyncDataFetcher`` stays available so test stubs and the legacy
``_db_fetcher.py`` code paths continue to work without rewrite.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple

# X.2.b URL contract: query params come back as a multi-dict (a key
# can repeat — ``?param_pRail=A&param_pRail=B``). The fetcher carries
# the full ``list[str]`` per key; the SQL executor picks the last
# value for single binds and expands 2+ values into an ``IN``-list
# (Y.2.app2.cde.multivalued). ``list[str]`` (not ``Sequence[str]``)
# on purpose — ``str`` IS a ``Sequence[str]``, so a stray ``{"x": "a"}``
# would type-check against ``Mapping[str, Sequence[str]]`` and then
# silently do ``"a"[-1]``; ``Mapping[str, list[str]]`` rejects it.

from recon_gen.common.config import Config
from recon_gen.common.dataset_contract import (
    Storage,
    get_contract,
    get_dataset_params,
    get_sql,
)
from recon_gen.common.db import AsyncConnectionPool
from recon_gen.common.env_keys import (
    RECON_GEN_PICKER_MAX_QUERY_LEN,
    EnvVarInvalid,
)
from recon_gen.common.html._data_shape import shape_for_kind
from recon_gen.common.html._sql_executor import execute_visual_sql_async
from recon_gen.common.html._visual_sql import wrap_for_visual
from recon_gen.common.ids import VisualId
from recon_gen.common.money import Cents
from recon_gen.common.sql.dialect import (
    Dialect,
    case_insensitive_substring_match,
    column_name,
    escape_like_pattern,
)
from recon_gen.common.tree.fields import Dim, Measure
from recon_gen.common.tree.structure import App
# AO.R.1 — reuse the EXACT label QuickSight stamps on a table header so
# App2 headers match QS by construction (single source of truth; the
# AO.R.5 parity gate asserts they stay in lock-step).
from recon_gen.common.tree.visuals import _field_label


# Async fetcher shape — what production callers (the App2 server)
# get from ``make_tree_db_fetcher``. ``VisualId`` (X.2.o.3) ties the
# fetcher to the tree's typed visual identifier — passing a SheetId
# or DashboardId here is a type error at the call site.
# ``Mapping[str, list[str]]`` (not ``dict``) so callers signal "I'm
# not going to mutate the URL params" at the type level; the
# ``list[str]`` value carries the full multi-dict (a query key can
# repeat — ``?param_pRail=A&param_pRail=B``).
DataFetcher = Callable[[VisualId, Mapping[str, list[str]]], Awaitable[Any]]
# Legacy sync alias, used by stub fetchers in tests + the older
# ``_db_fetcher.py`` code paths. The server route accepts both via
# ``inspect.iscoroutinefunction`` dispatch (X.2.n.5).
SyncDataFetcher = Callable[[VisualId, Mapping[str, list[str]]], Any]


# X.2.g.5.followon + X.2.h.5 — server-side pagination + sort for Table
# visuals. The renderer (``bootstrap.js::renderTable``) reads
# ``page_offset`` / ``page_size`` / ``total_rows`` / ``sort_column`` off
# the data fragment and re-fetches ``?page_offset=N&page_size=M&
# sort_column=<col>:<asc|desc>`` on pager / header clicks; without the
# server honoring these the fetcher returned EVERY row — a 68k-row
# L1-transactions table → a ~20 MB JSON fragment → the browser freezes
# building 68k <tr>s before any client-side pagination runs. Default
# page size mirrors the renderer's "0–50 of N" pager; capped so a
# crafted ``page_size`` can't OOM the server.
_TABLE_PAGE_SIZE = 50
_TABLE_PAGE_SIZE_MAX = 10_000
# A bare SQL identifier — the ONLY thing we'll splice into ORDER BY
# (the renderer sends ``<column-name>:<dir>``; the column name comes
# from the shaped result columns). Anything else → fall back to the
# stable ``ORDER BY 1``. (The worst a crafted name can do is name a
# non-existent column → SQL error → the fragment 500s; this guard is
# belt-and-braces, not the only line of defense — there's no untrusted
# input path to App2 in practice.)
_BARE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _page_int(params: Mapping[str, list[str]], key: str, default: int) -> int:
    """Read a non-negative int off the URL multi-dict (last value);
    fall back to ``default`` on missing / blank / non-numeric."""
    vals = params.get(key, [])
    raw = vals[-1].strip() if vals else ""
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        return default
    return n if n >= 0 else default


def _parse_sort(params: Mapping[str, list[str]]) -> tuple[str, bool]:
    """Parse the renderer's ``sort_column=<name>:<asc|desc>`` URL param.

    Returns ``(column_name, descending)`` — ``("", False)`` when absent,
    malformed, or the name isn't a bare identifier (→ the table page is
    ordered by column 1 instead).
    """
    vals = params.get("sort_column", [])
    raw = vals[-1].strip() if vals else ""
    if ":" not in raw:
        return "", False
    name, _, direction = raw.partition(":")
    name = name.strip()
    if not _BARE_IDENT_RE.match(name):
        return "", False
    return name, direction.strip().lower() == "desc"


def _paginate_table_sql(
    base_sql: str, *, offset: int, limit: int,
    sort_col: str, sort_desc: bool, dialect: Dialect,
) -> str:
    """Wrap ``base_sql`` with an ORDER BY + dialect-correct OFFSET/LIMIT
    + a ``COUNT(*) OVER ()`` total column (appended last; the fetcher
    strips it positionally, so the alias name is cosmetic).

    With a ``sort_col`` (a bare identifier — see ``_parse_sort``):
    ``ORDER BY <case-correct ref> [DESC], 1`` (the trailing ``1`` is a
    deterministic tiebreak so equal sort values don't shuffle page
    boundaries). Without one: ``ORDER BY 1`` — so pagination is stable
    across requests regardless of whether the base query's own
    ``ORDER BY`` survives the derived-table wrap (PG/Oracle don't
    promise it does). ``qs_page`` is letter-initial — Oracle rejects a
    leading-underscore identifier unquoted.
    """
    if sort_col:
        ref = column_name(sort_col, dialect)
        order_by = f"ORDER BY {ref}{' DESC' if sort_desc else ''}, 1"
    else:
        order_by = "ORDER BY 1"
    page_clause = (
        f"OFFSET {offset} ROWS FETCH NEXT {limit} ROWS ONLY"
        if dialect is Dialect.ORACLE
        else f"LIMIT {limit} OFFSET {offset}"  # postgres / sqlite
    )
    return (
        f"SELECT qs_page.*, COUNT(*) OVER () AS qs_row_total "
        f"FROM ({base_sql}) qs_page {order_by} {page_clause}"
    )


# Visual fields that may carry Dim/Measure references back to a
# Dataset. Order matters — we return the FIRST dataset found, on
# the assumption that a visual's primary dataset is the one its
# values / category fields point at. Walk order matches typical
# visual construction (values for KPI / group-by-Table; category for
# BarChart; columns for a flat-dump Table — X.2.u.3.fix: a Table
# built with `columns=[...]` and no group_by/values pointed nowhere,
# so its App2 fetch returned `{}` → an empty 0-row 0-col table).
_FIELDS_WITH_DATASET_REFS: tuple[str, ...] = (
    "values",
    "columns",
    "category",
    "color",
    "source",
    "destination",
    "weight",
    "group_by",
)


def _find_visual_dataset_identifier(visual: Any) -> str | None:  # typing-smell: ignore[explicit-any]: walks dynamic visual subtypes via getattr; static union of every Visual subtype would be fragile across tree changes
    """Walk a visual's known fields, return the first dataset
    identifier we find on a Dim or Measure.

    Returns ``None`` for visuals that don't carry a SQL-driven
    dataset (text boxes, non-data primitives). Callers treat that
    as "fetcher returns empty payload".
    """
    for field_name in _FIELDS_WITH_DATASET_REFS:
        field_val: Any = getattr(visual, field_name, None)  # typing-smell: ignore[explicit-any]: getattr returns Any; explicit annotation collapses to a known shape so the iteration below stays typeable
        if field_val is None:
            continue
        # Most visual fields are lists of Dim/Measure; a few
        # (Sankey source/target on certain shapes) are scalar refs.
        # Narrowing list[Any] from `isinstance(field_val, list)` keeps
        # the element type as Unknown — explicit annotation collapses
        # it back to ``list[Any]`` so pyright stops complaining about
        # the per-item walk below.
        if isinstance(field_val, list):
            candidates: list[Any] = field_val  # pyright: ignore[reportUnknownVariableType]  # typing-smell: ignore[explicit-any]: list elements are Dim/Measure unions narrowed by the per-item walk below
        else:
            candidates = [field_val]
        for item in candidates:
            ds: Any = getattr(item, "dataset", None)  # typing-smell: ignore[explicit-any]: dynamic getattr against Dim/Measure refs
            if ds is None:
                continue
            identifier: Any = getattr(ds, "identifier", None)  # typing-smell: ignore[explicit-any]: same dynamic-getattr pattern; coerced to str on return
            if identifier:
                return str(identifier)
    return None


@dataclass(frozen=True)
class _ChartMeta:
    """Per-chart presentation derived from a BarChart / LineChart's field
    wells (AO.R.2), so App2 charts match QuickSight: the series/``colors``
    dim (``series_column_name``, resolved to a column index at fetch
    time), plain-English axis labels, the value ``currency`` / ``number``
    format, and ``stacked`` (``bars_arrangement="STACKED"``)."""

    series_column_name: str | None
    x_label: str
    y_label: str
    value_format: str
    stacked: bool
    log_scale: bool = False  # BQ.5 — BarChart.log_scale parity for App2


@dataclass(frozen=True)
class _VisualPlan:
    """Pre-resolved per-visual fetch plan, built once at fetcher-construction
    and reused per request. ``column_labels`` / ``column_formats`` (AO.R.1)
    are keyed by raw SQL column name and carry the SAME per-column
    presentation QuickSight derives (contract ``human_name`` header +
    ``currency`` measure format) so App2 renders identical headers + money.
    ``chart`` (AO.R.2) is set for BarChart / LineChart visuals only.

    ``money_columns`` (AO.1.impl Studio slice) — SQL column names whose
    storage is BIGINT cents and need to display as dollars at App2 render
    time. Populated from the visual's ``currency=True`` fields (matches
    ``column_formats[name] == 'currency'`` exactly; kept as a separate
    field for the chart / KPI / Sankey shape paths where the format-by-
    name map doesn't apply directly).
    """

    kind: str
    sql: str | None
    ds_id: str | None
    column_labels: Mapping[str, str]
    column_formats: Mapping[str, str]
    #: CY.4.1 — set of SQL column names the App2 table renderer should
    #: hide (no ``<th>`` header, no per-row ``<td>`` cell) while still
    #: shipping the value through the row payload positionally so popups
    #: / drills can read it. Derived from the dataset contract's
    #: ``ColumnSpec.hidden`` flag (minus any column the visual EXPLICITLY
    #: lands on a field well — author intent overrides contract default).
    column_hidden: Mapping[str, bool]
    chart: _ChartMeta | None
    money_columns: frozenset[str]
    #: KPI's value format (``"currency"`` / ``"number"``), derived
    #: from the visual's first value measure's ``currency`` flag at
    #: plan-build time. ``None`` for non-KPI visuals. v11.21.0 cold-
    #: read finding #14 fix (BH.14): without this, KPIs emitted no
    #: ``format`` field → JS fell to the no-format toLocaleString
    #: path → currency values rendered with 3 decimal places (vs the
    #: 2-decimal contract that ``feedback_kpi_currency_decimals_strict``
    #: pins).
    kpi_format: str | None
    #: BK.2 — when True, ``shape_kpi`` stamps ``state_icon`` +
    #: ``state_color`` on each value entry so the renderer paints the
    #: accessible icon-with-color state next to the number (parity with
    #: the QS-side ``KPIValueZeroIndicator`` emit on the same Visual).
    #: False for KPIs without a zero-indicator + every non-KPI.
    kpi_zero_is_healthy: bool
    #: BK.9 — when True, ``shape_kpi`` stamps the sign-indicator pair
    #: (▲ green when value ≥ 0, ▼ red when value < 0) — App2 parity
    #: with the QS-side ``KPIValueSignIndicator``. Mutually exclusive
    #: with ``kpi_zero_is_healthy`` (the KPI constructor blocks both).
    kpi_inflow_is_healthy: bool
    #: CF.X-infra — (amber_at, red_at) tuple when the KPI carries a
    #: ``KPIValueThresholdBanding``; ``shape_kpi`` stamps the 3-band
    #: indicator (✓ green / ⚠ amber / ✗ red). None on KPIs without
    #: the indicator + every non-KPI. Mutually exclusive with
    #: ``kpi_zero_is_healthy`` / ``kpi_inflow_is_healthy`` (the KPI
    #: constructor's 3-way mutex blocks combinations).
    kpi_threshold_banding: tuple[int, int] | None


def _apply_cents_to_dollars(
    rows: list[tuple[Any, ...]],  # typing-smell: ignore[explicit-any]: heterogeneous DB row tuples (per-column DB driver types) — same justification as the other tuple-of-Any returns in this module
    columns: list[str],
    money_columns: frozenset[str],
) -> list[tuple[Any, ...]]:  # typing-smell: ignore[explicit-any]: heterogeneous DB row tuples — same justification as the input shape
    """AO.1.impl (Studio slice) — convert BIGINT cents → float dollars
    in-place for any column named in ``money_columns``.

    Money columns are stored as integer cents per the AO.1 contract
    (``recon_gen.common.money.Cents``); App2's renderer formats them as
    currency assuming dollars. Without this conversion ``$1,234.56``
    renders as ``$123,456.00`` (100× off). Bare-Table visuals only —
    KPI / BarChart / LineChart / Sankey route their aggregations through
    ``_measure_sql`` which divides by 100.0 at the SQL boundary instead.

    Matches column names case-insensitively (Oracle returns column names
    uppercased via the driver's case-folding); ``money_columns`` is the
    set of lowercased identifiers from the visual's currency-flagged
    field leaves. None values pass through (NULL columns). Type-coerces
    via ``Cents.from_db(int(v)).to_dollars()`` then floats for JSON
    serialization (Decimal isn't JSON-native).
    """
    if not money_columns or not rows:
        return rows
    # Resolve column index → conversion flag once per request (Oracle's
    # uppercased column names + the lowercase money_columns spelling
    # converge on lowercase compare).
    convert_idx = [
        i for i, c in enumerate(columns) if str(c).lower() in money_columns
    ]
    if not convert_idx:
        return rows
    out: list[tuple[Any, ...]] = []  # typing-smell: ignore[explicit-any]: heterogeneous DB row tuples — same justification as the function signature
    for row in rows:
        as_list = list(row)
        for idx in convert_idx:
            v = as_list[idx]
            if v is None:
                continue
            try:
                as_list[idx] = float(Cents.from_db(int(v)).to_dollars())
            except (TypeError, ValueError):
                # Already-converted floats / Decimals / strings pass
                # through untouched — protects against double-convert
                # paths and SQLite TEXT-affinity fallbacks.
                pass
        out.append(tuple(as_list))
    return out


def _leaf_column_name(leaf: Any) -> str | None:  # typing-smell: ignore[explicit-any]: walks dynamic Dim/Measure leaves via getattr
    """The SQL column name a Dim/Measure leaf projects (its ``Column`` /
    ``CalcField`` ``name``), or None when there's no resolvable column."""
    col: Any = getattr(leaf, "column", None)  # typing-smell: ignore[explicit-any]: leaf.column is Column | CalcField | str
    name = getattr(col, "name", None)
    if name is None and isinstance(col, str):
        name = col
    return str(name) if name else None


def _table_column_meta(
    visual: Any,  # typing-smell: ignore[explicit-any]: dynamic visual subtype walked via getattr
    ds_id: str | None,
) -> tuple[dict[str, str], dict[str, str], dict[str, bool]]:
    """AO.R.1 — per-column ``(label, format, hidden)`` for a visual,
    derived from the SAME sources QuickSight uses so App2 renders
    identical headers + money.

    - ``label`` ← the dataset contract's ``ColumnSpec.human_name`` (the
      ``display_name`` override or smart-titled snake_case) for every
      contract column — exactly what QS's ``_field_label`` resolves to.
    - ``format`` ← the visual's field leaves: a ``Measure`` formats as
      ``"currency"`` (when ``currency=True``) else ``"number"``; a ``Dim``
      formats as ``"currency"`` only when it carries ``currency=True``.
      Dimension ids stay unformatted (no thousands-separator on an id) —
      mirrors QS's measure-vs-dimension number formatting.
    - ``hidden`` ← the dataset contract's ``ColumnSpec.hidden`` flag
      (CY.4.1). Set when the column ships through the SELECT for popup /
      row-level wiring payload but should NOT render as a column header
      / cell on the App2 table. QS-side parity is implicit: QS only
      declares columns that appear on the visual's field wells, so a
      hidden contract column never lands in QS's visual unless it was
      put there explicitly.

    Empty maps when the visual has no resolvable contract (text boxes etc.):
    the renderer then falls back to the raw column name, unformatted.
    """
    labels: dict[str, str] = {}
    formats: dict[str, str] = {}
    hidden: dict[str, bool] = {}
    if ds_id is not None:
        try:
            contract = get_contract(ds_id)
        except KeyError:
            contract = None
        if contract is not None:
            for spec in contract.columns:
                labels[spec.name] = spec.human_name
                if getattr(spec, "hidden", False):
                    hidden[spec.name] = True
    for field_name in _FIELDS_WITH_DATASET_REFS:
        field_val: Any = getattr(visual, field_name, None)  # typing-smell: ignore[explicit-any]: getattr returns Any; collapsed to a known shape below
        if field_val is None:
            continue
        if isinstance(field_val, list):
            items: list[Any] = field_val  # pyright: ignore[reportUnknownVariableType]  # typing-smell: ignore[explicit-any]: list of Dim/Measure unions narrowed by the isinstance walk below
        else:
            items = [field_val]
        for item in items:
            if not isinstance(item, (Dim, Measure)):
                continue
            name = _leaf_column_name(item)
            if name is None:
                continue
            # Authoritative header — the same _field_label QS emits as the
            # column's CustomLabel (overrides the contract entry for calc
            # fields, which aren't in the contract).
            labels[name] = _field_label(item)
            if isinstance(item, Measure):
                formats[name] = "currency" if getattr(item, "currency", False) else "number"
            elif getattr(item, "currency", False):
                formats[name] = "currency"
            # CY.4.1 — a column that the visual EXPLICITLY puts on a field
            # well is by definition not hidden (the author asked for it).
            # The contract-derived hidden flag is the default; the visual's
            # field-wells list is the override.
            hidden.pop(name, None)
    return labels, formats, hidden


def _chart_meta(visual: Any) -> _ChartMeta | None:  # typing-smell: ignore[explicit-any]: dynamic visual subtype walked via getattr
    """AO.R.2 — per-chart presentation for a BarChart / LineChart, from
    the SAME field wells QuickSight reads. ``None`` for any other kind.

    - ``series_column_name`` ← the BarChart's first ``colors`` dim (the
      stacked/grouped series); ``None`` when there's no series dim.
    - ``x_label`` / ``y_label`` ← the author's ``category_label`` /
      ``value_label`` override, else ``_field_label`` of the first
      category / value leaf (the same human label QS axis-labels with).
    - ``value_format`` ← ``"currency"`` when the first value measure is
      ``currency=True``, else ``"number"``.
    - ``stacked`` ← ``bars_arrangement`` is ``STACKED`` / ``STACKED_PERCENT``.
    """
    kind = type(visual).__name__
    if kind not in ("BarChart", "LineChart"):
        return None
    cats = getattr(visual, "category", []) or []
    vals = getattr(visual, "values", []) or []
    if not cats or not vals:
        return None
    colors = getattr(visual, "colors", []) or []
    series_name = _leaf_column_name(colors[0]) if colors else None
    x_label = getattr(visual, "category_label", None) or _field_label(cats[0])
    y_label = getattr(visual, "value_label", None) or _field_label(vals[0])
    value_format = "currency" if getattr(vals[0], "currency", False) else "number"
    stacked = getattr(visual, "bars_arrangement", None) in (
        "STACKED", "STACKED_PERCENT",
    )
    log_scale = bool(getattr(visual, "log_scale", False))
    return _ChartMeta(
        series_column_name=series_name,
        x_label=str(x_label),
        y_label=str(y_label),
        value_format=value_format,
        stacked=stacked,
        log_scale=log_scale,
    )


def _resolve_money_columns(
    ds_id: str | None, col_formats: Mapping[str, str],
) -> frozenset[str]:
    """BH.24.6 — `_apply_cents_to_dollars` needs the set of columns
    whose raw cursor value is BIGINT cents AND whose visual wants
    currency display. Both halves must hold: the visual's
    ``currency=True`` flag says "format as $-prefixed money," and the
    contract's ``storage=CENTS`` says "the value is raw cents —
    divide by 100 to get dollars."

    Contract REQUIRED post-BH.24.6 (user 2026-05-25, per
    `feedback_no_compat_shims`): the pre-BH.24.6 no-contract
    fallback ("currency=True == cents") hid the BG.7 100× systemic
    bug. Any visual-served dataset must register a contract via
    `build_dataset` (production) or `register_contract` (tests).
    Production already does this; tests register contracts in
    module-level setup. ds_id None (visual without a SQL-backed
    dataset, e.g. text-box) returns empty set — no money columns to
    convert.
    """
    currency_cols = {
        name for name, fmt in col_formats.items() if fmt == "currency"
    }
    if not currency_cols:
        return frozenset()
    if ds_id is None:
        # Visual without a dataset — no money columns to convert.
        # (Caller's currency_cols would be empty anyway since
        # _table_column_meta needs a contract to populate; defensive
        # short-circuit.)
        return frozenset()
    contract = get_contract(ds_id)  # raises KeyError if not registered
    cents_names = {
        getattr(col, "name", "")
        for col in contract.columns
        if getattr(col, "storage", Storage.DOLLARS) is Storage.CENTS
    }
    cents_names.discard("")
    return frozenset(currency_cols & cents_names)


def _kpi_format(visual: object) -> str | None:
    """v11.21.0 finding #14 (BH.14) — return ``"currency"`` when the
    visual is a KPI whose first value measure carries ``currency=True``,
    else ``"number"`` for non-currency KPIs, else ``None`` for non-KPI
    visuals.

    Mirrors ``_chart_meta``'s value_format derivation for charts: a
    KPI value measure's currency flag is the source of truth for the
    rendered format. Plumbing this into ``shape_kpi``'s ``format``
    kwarg drives the JS formatter's currency-2-decimal path (vs the
    no-format default that emits 3-decimal toLocaleString output).
    """
    if type(visual).__name__ != "KPI":
        return None
    vals = getattr(visual, "values", []) or []
    if not vals:
        return None
    return "currency" if getattr(vals[0], "currency", False) else "number"


def _kpi_zero_is_healthy(visual: object) -> bool:
    """BK.2 — read the tree KPI's ``value_zero_indicator`` setting.
    Returns True when a single-value KPI carries a
    ``KPIValueZeroIndicator(healthy_when_zero=True)`` — the App2-side
    payload then ships ``state_icon`` / ``state_color`` for each value
    entry (mirrors the QS-side ConditionalFormatting emit)."""
    if type(visual).__name__ != "KPI":
        return False
    indicator: Any = getattr(visual, "value_zero_indicator", None)  # typing-smell: ignore[explicit-any]: dynamic getattr against KPI subtype — narrowing to KPIValueZeroIndicator | None would force a tree → html dependency that inverts the existing layer
    if indicator is None:
        return False
    return bool(getattr(indicator, "healthy_when_zero", False))


def _kpi_inflow_is_healthy(visual: object) -> bool:
    """BK.9 — read the tree KPI's ``value_sign_indicator`` setting.
    Mirror of ``_kpi_zero_is_healthy`` for the sign-aware shape."""
    if type(visual).__name__ != "KPI":
        return False
    indicator: Any = getattr(visual, "value_sign_indicator", None)  # typing-smell: ignore[explicit-any]: dynamic getattr against KPI subtype — narrowing to KPIValueSignIndicator | None would force a tree → html dependency that inverts the existing layer
    if indicator is None:
        return False
    return bool(getattr(indicator, "inflow_is_healthy", False))


def _kpi_threshold_banding(visual: object) -> tuple[int, int] | None:
    """CF.X-infra — read the tree KPI's ``value_threshold_banding``
    setting. Returns ``(amber_at, red_at)`` tuple when set, None
    otherwise. Mirror of ``_kpi_zero_is_healthy`` for the 3-band
    threshold shape."""
    if type(visual).__name__ != "KPI":
        return None
    indicator: Any = getattr(visual, "value_threshold_banding", None)  # typing-smell: ignore[explicit-any]: dynamic getattr against KPI subtype — narrowing to KPIValueThresholdBanding | None would force a tree → html dependency that inverts the existing layer
    if indicator is None:
        return None
    amber_at = getattr(indicator, "amber_at", None)
    red_at = getattr(indicator, "red_at", None)
    if not isinstance(amber_at, int) or not isinstance(red_at, int):
        return None
    return (amber_at, red_at)


def make_tree_db_fetcher(
    tree_app: App,
    cfg: Config,
    *,
    pool: AsyncConnectionPool,
) -> DataFetcher:
    """Return an async ``DataFetcher`` that resolves any visual in
    ``tree_app`` to its dataset SQL → executes via the pool → shapes
    per kind.

    Construction-time invariants:

    1. ``tree_app.resolve_auto_ids()`` runs once so visual IDs are
       stable strings (not the AUTO sentinel) by the time we walk.
    2. The SQL registry must already be populated for every dataset
       the tree references — typically by calling the per-app
       ``build_all_datasets(cfg)`` BEFORE this factory. The factory
       eagerly resolves every visual's SQL so a missing entry
       fails loudly here, not silently inside a hot HTMX swap.
    3. ``pool`` is required — the App2 server's startup hook opens
       it via ``make_connection_pool(cfg)``; tests build a pool
       against in-memory SQLite via the same factory.

    Args:
        tree_app: The App whose visuals need data. Must have its
            analysis attached (validated implicitly via the walk).
        cfg: Loaded config; supplies dialect for SQL placeholder
            rewriting.
        pool: An open ``AsyncConnectionPool`` (PG / Oracle / SQLite).
            Lifecycle (open + close) belongs to the caller — usually
            the server's startup / shutdown hooks.

    Returns:
        An async ``DataFetcher`` matching the ``server.make_app``
        contract: ``await fetcher(visual_id, params) -> Any``.
    """
    tree_app.resolve_auto_ids()
    if tree_app.analysis is None:
        raise ValueError(
            f"App {tree_app.name!r} has no analysis attached — "
            f"can't build a fetcher with no visuals."
        )

    # Phase BM — BL.2's pre-resolved default-date-range capture +
    # per-request bind-layer patch both dissolved. Date narrowing
    # lives in each date-scoped dataset's `<<$pXxxDateStart>>` /
    # `<<$pXxxDateEnd>>` SQL parameters, whose StaticValues defaults
    # are picked up by the existing
    # `apply_dataset_param_defaults` substitution when the URL omits
    # `param_pXxxDate*` keys.

    # Pre-resolve every visual's (kind, sql) at build time. Failures
    # surface here, not at request time. Visuals without datasets
    # land with sql=None and return empty payloads at fetch time.
    # X.2.g.1.c — wrap the dataset SQL with the visual's declared
    # aggregation (KPI count → SELECT COUNT, BarChart → GROUP BY
    # category, etc.). Without this, KPI visuals would render one
    # card per dataset row instead of the aggregated value QS shows.
    visual_index: dict[VisualId, _VisualPlan] = {}
    for sheet in tree_app.analysis.sheets:
        for visual in sheet.visuals:
            # ``visual.visual_id`` is ``VisualId | AutoResolved`` per
            # the tree types; ``resolve_auto_ids()`` above guarantees
            # we land on the str-shaped VisualId branch. Re-wrap for
            # the type checker without changing runtime behavior
            # (NewType is identity at runtime).
            vid_raw = getattr(visual, "visual_id", None)
            if not isinstance(vid_raw, str) or not vid_raw:
                continue
            vid = VisualId(vid_raw)
            kind = type(visual).__name__
            ds_id = _find_visual_dataset_identifier(visual)
            sql: str | None = None
            if ds_id is not None:
                base_sql = get_sql(ds_id)
                # BH.24.6 — contract is REQUIRED; raises if not
                # registered. The pre-BH.24.6 _safe_get_contract
                # fallback hid the BG.7 100× systemic bug class.
                # Every visual-served dataset registers a contract
                # via build_dataset (production) or register_contract
                # (tests). Raises loudly if missing — the actionable
                # signal vs the silent-misbehavior fallback shape.
                contract = get_contract(ds_id)
                sql = wrap_for_visual(base_sql, visual, contract=contract)
            col_labels, col_formats, col_hidden = _table_column_meta(visual, ds_id)
            money_cols = _resolve_money_columns(ds_id, col_formats)
            visual_index[vid] = _VisualPlan(
                kind=kind, sql=sql, ds_id=ds_id,
                column_labels=col_labels, column_formats=col_formats,
                column_hidden=col_hidden,
                chart=_chart_meta(visual),
                money_columns=money_cols,
                kpi_format=_kpi_format(visual),
                kpi_zero_is_healthy=_kpi_zero_is_healthy(visual),
                kpi_inflow_is_healthy=_kpi_inflow_is_healthy(visual),
                kpi_threshold_banding=_kpi_threshold_banding(visual),
            )

    base_prefix = str(cfg.db_table_prefix)
    base_prefix_tok = f"{base_prefix}_"

    async def fetcher(visual_id: VisualId, params: Mapping[str, list[str]]) -> Any:  # typing-smell: ignore[explicit-any]: per-visual-kind shape (KPI float, Sankey {nodes,links}, etc.) — JSON-serialized downstream, so a real union here would be every renderer's shape
        if visual_id not in visual_index:
            # Unknown visual_id — typically a stale URL from a
            # cached page. Return empty so the d3 renderers paint
            # an empty visual instead of throwing.
            return {}
        plan = visual_index[visual_id]
        kind, sql, ds_id = plan.kind, plan.sql, plan.ds_id
        if sql is None:
            # Visual without a SQL-backed dataset (text box etc.).
            # Empty payload renders as a blank visual — fine for
            # the page-chrome-only case.
            return {}
        # BV.4.8.P1.1 — `?prefix=<alt>` URL param (typically
        # `<base>_v` from the dual-prefix Trainer's Violation Tour
        # link) re-targets the SQL at an alternate prefix at fetch
        # time. The pre-resolved SQL string in ``plan.sql`` has the
        # cfg base prefix baked in; we substitute the leading
        # ``<base>_`` token with ``<alt>_`` so every referenced
        # table/matview retargets together. Anchoring on the
        # trailing underscore avoids accidental hits when one
        # prefix is a substring of another (e.g. "recon" vs
        # "recon-test"). No-op when ``prefix`` is absent, equals
        # the base, or isn't a non-empty string.
        prefix_vals = params.get("prefix") or []
        alt_prefix = prefix_vals[-1].strip() if prefix_vals else ""
        if alt_prefix and alt_prefix != base_prefix:
            sql = sql.replace(base_prefix_tok, f"{alt_prefix}_")
        # Y.2.app2.cde — resolve `<<$paramName>>` defaults from the
        # dataset's QS parameters when the URL doesn't supply them
        # (keeps the freshly-loaded page consistent with QS). Phase BM —
        # this same path now resolves the universal date-range pickers'
        # initial-load defaults; the BL.2 bind-layer prepop dissolved
        # because the date-pushdown DateTimeDatasetParameters carry the
        # same StaticValues defaults the analysis picker shows.
        dataset_params = get_dataset_params(ds_id) if ds_id else []
        if kind == "Table":
            # X.2.g.5.followon + X.2.h.5 — page (and sort) the table
            # SERVER-side. Without this a 68k-row dataset shipped 68k
            # rows in one ~20 MB JSON fragment and the browser froze
            # building the DOM. The renderer sends ``page_offset`` /
            # ``page_size`` / ``sort_column`` on pager / header clicks.
            offset = _page_int(params, "page_offset", 0)
            limit = max(1, min(
                _page_int(params, "page_size", _TABLE_PAGE_SIZE),
                _TABLE_PAGE_SIZE_MAX,
            ))
            sort_col, sort_desc = _parse_sort(params)
            paginated_sql = _paginate_table_sql(
                sql, offset=offset, limit=limit,
                sort_col=sort_col, sort_desc=sort_desc, dialect=cfg.dialect,
            )
            rows, columns = await execute_visual_sql_async(
                pool, paginated_sql, params, dialect=cfg.dialect,
                dataset_parameters=dataset_params,
            )
            # Last column is COUNT(*) OVER () — strip it positionally
            # (the alias name varies by dialect / driver case-folding).
            total = int(rows[0][-1]) if rows and rows[0] else 0
            page_rows_tuples = [tuple(r[:-1]) for r in rows]
            page_cols = list(columns[:-1])
            # AO.1.impl (Studio slice) — Table visuals project raw rows
            # straight from the dataset SQL (no aggregation wrap), so
            # any money column lands as BIGINT cents. Convert by name
            # against the visual's currency-marked field leaves before
            # shaping.
            converted = _apply_cents_to_dollars(
                page_rows_tuples, page_cols, plan.money_columns,
            )
            page_rows = [list(r) for r in converted]
            # Echo the *resolved* sort back (not the raw URL value) so
            # the renderer's sort badge + next-direction logic stays
            # consistent — ``""`` when it didn't parse / wasn't given.
            echo_sort = (
                f"{sort_col}:{'desc' if sort_desc else 'asc'}"
                if sort_col else ""
            )
            return shape_for_kind(
                "Table", page_rows, page_cols,
                page_offset=offset, page_size=limit, total_rows=total,
                sort_column=echo_sort,
                column_labels=plan.column_labels,
                column_formats=plan.column_formats,
                column_hidden=plan.column_hidden,
            )
        rows, columns = await execute_visual_sql_async(
            pool, sql, params, dialect=cfg.dialect,
            dataset_parameters=dataset_params,
        )
        # AO.R.2 — BarChart / LineChart carry per-chart presentation
        # (series/colors dim → multi-series, axis labels, currency
        # format, stacked) derived from the tree at build time. Resolve
        # the series column to a positional index against the live
        # result columns (case-insensitive — Oracle upper-cases) and
        # pass the chart kwargs the shaper + d3 renderer read.
        if plan.chart is not None:
            series_column: int | None = None
            name = plan.chart.series_column_name
            if name:
                lowered = [str(c).lower() for c in columns]
                if name.lower() in lowered:
                    series_column = lowered.index(name.lower())
            chart_kwargs: dict[str, Any] = {  # typing-smell: ignore[explicit-any]: heterogeneous shape-fn kwargs (int|str|bool), splatted into shape_for_kind
                "series_column": series_column,
                "x_label": plan.chart.x_label,
                "y_label": plan.chart.y_label,
                "format": plan.chart.value_format,
            }
            if kind == "BarChart":
                chart_kwargs["stacked"] = plan.chart.stacked
                chart_kwargs["log_scale"] = plan.chart.log_scale
            return shape_for_kind(kind, rows, columns, **chart_kwargs)
        if kind == "KPI":
            # v11.21.0 finding #14 fix (BH.14): pass the visual's
            # value-measure format to shape_kpi so the emitted payload
            # carries ``format="currency"``. Without this, JS's
            # formatKPIValue falls to the no-format toLocaleString
            # path, which emits 3-decimal output on values like
            # ``-11993.097`` — the cold-read's literal misread-risk
            # shape. The JS currency branch forces ``minimumFractionDigits
            # = maximumFractionDigits = 2``.
            kpi_kwargs: dict[str, Any] = {}  # typing-smell: ignore[explicit-any]: heterogeneous shape-fn kwargs — same justification as chart_kwargs
            if plan.kpi_format is not None:
                kpi_kwargs["format"] = plan.kpi_format
            if plan.kpi_zero_is_healthy:
                kpi_kwargs["zero_is_healthy"] = True
            if plan.kpi_inflow_is_healthy:
                kpi_kwargs["inflow_is_healthy"] = True
            if plan.kpi_threshold_banding is not None:
                kpi_kwargs["threshold_banding"] = plan.kpi_threshold_banding
            return shape_for_kind(kind, rows, columns, **kpi_kwargs)
        # ForceGraph + Sankey have specialized projectors today
        # (_db_fetcher._topology_to_force_graph, etc.); the generic
        # SQL path handles KPI / Table / Sankey via shape_for_kind.
        # Visual kinds without a SQL adapter raise from shape_for_kind —
        # same loud-failure pattern as the SQL lookup above.
        return shape_for_kind(kind, rows, columns)

    return fetcher


# CQ.2 — picker option universe is searched server-side, not
# materialized client-side. Pre-CQ.2 ``make_options_fetcher`` ran a
# ``LIMIT 2000`` DISTINCT once at sheet-render time and baked every
# option into the ``<select>``; Tom Select filtered client-side. Past
# the 2000th alphabetical option the tail was silently unreachable
# (typing never re-queried the DB). Per operator direction
# 2026-06-08 ("truncating at 2000 rows SUCKS in production. That is
# NOT the right approach and we shouldn't even have it as a fallback.
# We must do server side querying."), the cap is gone and typeahead
# IS the option-fetch path. The render-time eager fetch is dissolved
# too — sheets render with empty ``<option>`` lists (Tom Select's
# ``preload: 'focus'`` fires one ``load('')`` on first focus to
# populate the seed page; subsequent keystrokes fire ``load(query)``).


# Per-picker page size for the server-side typeahead path. Dropdown-
# fits-screen, NOT a truncation surrogate — the operator narrows
# further by typing more characters. Search result sets that hit this
# cap are the "first page of matches" UX, not a silent truncation.
PICKER_PAGE_SIZE = 100


@dataclass(frozen=True)
class PickerMatviewHint:
    """CQ.2.g — bypass the dataset CustomSql wrap and search the
    underlying matview directly.

    Picker source datasets (e.g. ``DS_L1_DS_ACCOUNTS``) wrap a SELECT
    over a matview in a CustomSql block (with COALESCE / aliasing /
    GROUP BY). For the typeahead-search path the wrap is dead weight:
    ``SELECT DISTINCT col FROM (<wrapped sql>) WHERE col ILIKE ...``
    runs the wrap before the ILIKE filter, defeating the planner.

    Datasets whose universe IS one single matview (``DS_L1_DS_ACCOUNTS``
    → ``<prefix>_current_daily_balances``, ``DS_L1_TX_IDS`` →
    ``<prefix>_current_transactions``, ``DS_INV_ANETWORK_ACCOUNTS`` →
    ``<prefix>_inv_money_trail_edges``) attach this hint. The search
    fetcher then queries the matview directly with the same
    ``select_expr`` the dataset's wrapped SQL projects — same option
    set, no wrap cost.

    Datasets whose universe is multiple matviews (``DS_L1_ACCOUNTS``
    UNION ALL over daily_balances + transactions + l1_exceptions per
    BL.3) do NOT attach a hint and stay on the wrap path; the backlog
    item is to materialize that universe as a dedicated picker matview.

    Attributes:
        matview: the matview name (already prefixed via
            ``cfg.prefixed`` / ``cfg.db_table_prefix``).
        select_expr: the SAME projection expression the dataset's
            wrapped SQL uses for the picker column. Both sides MUST
            match or the hint path returns a different option set
            than the wrap path. For account_display columns this is
            typically ``account_display_expr(name_col, id_col)``.
        where_clause: optional WHERE-clause fragment the dataset's
            wrapped SQL applies to narrow the source (e.g.
            ``"account_scope = 'internal'"`` from CQ.4.a). The
            matview-direct path MUST apply the same narrowing or
            it leaks rows the dataset filtered out — surfaced
            2026-06-08 when external counterparties appeared in
            the Daily Statement Account picker post-CQ.4.a deploy
            even though the dataset SQL filtered them out. None
            means no extra narrowing.
    """
    matview: str
    select_expr: str
    where_clause: str | None = None


# Per-visual-identifier registry of matview hints. ``register_picker_matview_hint``
# called by ``build_dataset(..., picker_matview_hint=...)``;
# ``get_picker_matview_hint`` looked up by the search endpoint to pick
# matview-direct vs wrap path.
_PICKER_MATVIEW_HINT_REGISTRY: dict[str, PickerMatviewHint] = {}


def register_picker_matview_hint(
    visual_identifier: str, hint: PickerMatviewHint,
) -> None:
    """Register a matview-direct hint for a picker-source dataset.

    Same overwrite-on-repeat semantics as ``register_sql`` — re-building
    a dataset under a new dialect overwrites the prior hint with the
    re-rendered matview name (which may itself be dialect-folded via
    ``column_name`` for Oracle).
    """
    _PICKER_MATVIEW_HINT_REGISTRY[visual_identifier] = hint


def get_picker_matview_hint(
    visual_identifier: str,
) -> PickerMatviewHint | None:
    """Look up the matview hint registered under ``visual_identifier``.

    Returns ``None`` when the dataset has no hint registered — the
    search fetcher falls back to the wrap path.
    """
    return _PICKER_MATVIEW_HINT_REGISTRY.get(visual_identifier)


# Max user-typed query length. Caps the LIKE planner-DOS attack
# surface (a 1 MB POST body with a million-char ``q`` would still bind
# but waste planner time). 100 chars is comfortable for any real
# account-display search.
_MAX_QUERY_LEN_DEFAULT = 500


def _picker_query_cap() -> int:
    """Return the active per-keystroke query length cap.

    CR.2 — operator-tunable via ``RECON_GEN_PICKER_MAX_QUERY_LEN``;
    falls back to ``_MAX_QUERY_LEN_DEFAULT`` (500). Pre-CR.2 the cap
    was a hardcoded 100 chars + silently truncated on overflow with
    no signal back; the JSON typeahead response now carries a
    ``"truncated"`` flag so the UI can banner when a customer-typed
    query hit the cap. Resolved per-call (not cached) so a test or
    operator that flips the env mid-process gets the new value.
    """
    try:
        override = RECON_GEN_PICKER_MAX_QUERY_LEN.get_or_none()
    except EnvVarInvalid:
        # Invalid override → fall back to default. The env-var
        # validator already raised the operator-facing error; we don't
        # want a malformed env to crash every dropdown click.
        return _MAX_QUERY_LEN_DEFAULT
    return override if override is not None else _MAX_QUERY_LEN_DEFAULT


class OptionsSearchResult(NamedTuple):
    """CR.2 — picker-search fetcher return shape.

    ``options`` is the matched option labels (same tuple the pre-CR.2
    fetcher returned). ``truncated`` is True if the caller's ``query``
    was longer than the active cap and the fetcher trimmed it. The
    JSON typeahead route surfaces ``truncated`` to the client as a
    response field so the UI can banner the silent-match-failure
    that pre-CR.2 customers had no way to detect.
    """
    options: tuple[str, ...]
    truncated: bool


def _picker_search_sql_wrap(
    base_sql: str, column: str, *, dialect: Dialect,
    limit: int = PICKER_PAGE_SIZE,
) -> str:
    """Build the WRAP-path search SQL — for pickers whose source dataset
    is a multi-matview UNION (DS_L1_ACCOUNTS) or has no matview hint.

    Wraps the dataset's CustomSql in an outer ``SELECT DISTINCT col
    WHERE col ILIKE ...`` so the option universe matches what the
    dataset declares. Pays the wrap cost (planner can't push the
    predicate through the wrapped UNION); the matview-direct path
    (see :func:`_picker_search_sql_matview_direct`) is the perf-fast
    alternative for single-matview pickers.

    ``:q`` is bound by the caller through the standard ``_sql_executor``
    pipeline (PG → ``%(q)s``, DuckDB → ``$q``, Oracle → ``:q``). The
    bound value MUST be pre-escaped via
    :func:`escape_like_pattern`.
    """
    col_ref = column_name(column, dialect)
    where_clause = case_insensitive_substring_match(col_ref, "q", dialect)
    limit_clause = (
        f"FETCH FIRST {limit} ROWS ONLY"
        if dialect is Dialect.ORACLE
        else f"LIMIT {limit}"
    )
    return (
        f"SELECT DISTINCT {col_ref} AS opt FROM ({base_sql}) opt_src "
        f"WHERE {col_ref} IS NOT NULL AND {where_clause} "
        f"ORDER BY 1 {limit_clause}"
    )


def _picker_search_sql_matview_direct(
    hint: PickerMatviewHint, *, dialect: Dialect,
    limit: int = PICKER_PAGE_SIZE,
) -> str:
    """CQ.2.g — build the MATVIEW-DIRECT search SQL.

    Queries the matview directly (no wrap). DISTINCT + ILIKE both push
    to the storage layer; predicate runs ahead of materializing the
    full universe. Sub-10ms at 50k accounts vs. the wrap path's
    100-200ms.

    ``hint.select_expr`` is the SAME projection the dataset's wrap
    uses (e.g. ``COALESCE(account_name, account_id) || ' (' ||
    account_id || ')'``). Both sides MUST match — see PickerMatviewHint
    docstring.
    """
    ilike_clause = case_insensitive_substring_match(
        hint.select_expr, "q", dialect,
    )
    extra = f" AND ({hint.where_clause})" if hint.where_clause else ""
    limit_clause = (
        f"FETCH FIRST {limit} ROWS ONLY"
        if dialect is Dialect.ORACLE
        else f"LIMIT {limit}"
    )
    return (
        f"SELECT DISTINCT {hint.select_expr} AS opt "
        f"FROM {hint.matview} "
        f"WHERE {hint.select_expr} IS NOT NULL AND {ilike_clause}{extra} "
        f"ORDER BY 1 {limit_clause}"
    )


def _picker_seed_sql_wrap(
    base_sql: str, column: str, *, dialect: Dialect,
    limit: int = PICKER_PAGE_SIZE,
) -> str:
    """Empty-query seed page on the WRAP path — no ILIKE clause, just
    the top-N alphabetical universe. Drives the ``preload: 'focus'``
    initial-load semantics.
    """
    col_ref = column_name(column, dialect)
    limit_clause = (
        f"FETCH FIRST {limit} ROWS ONLY"
        if dialect is Dialect.ORACLE
        else f"LIMIT {limit}"
    )
    return (
        f"SELECT DISTINCT {col_ref} AS opt FROM ({base_sql}) opt_src "
        f"WHERE {col_ref} IS NOT NULL ORDER BY 1 {limit_clause}"
    )


def _picker_seed_sql_matview_direct(
    hint: PickerMatviewHint, *, dialect: Dialect,
    limit: int = PICKER_PAGE_SIZE,
) -> str:
    """Empty-query seed page on the MATVIEW-DIRECT path."""
    extra = f" AND ({hint.where_clause})" if hint.where_clause else ""
    limit_clause = (
        f"FETCH FIRST {limit} ROWS ONLY"
        if dialect is Dialect.ORACLE
        else f"LIMIT {limit}"
    )
    return (
        f"SELECT DISTINCT {hint.select_expr} AS opt "
        f"FROM {hint.matview} "
        f"WHERE {hint.select_expr} IS NOT NULL{extra} "
        f"ORDER BY 1 {limit_clause}"
    )


# CQ.2 — server-side typeahead fetcher. ``(dataset_id, column, query,
# url_params) → tuple[str, ...]``. The route layer (server.py) calls
# this from both the JSON typeahead endpoint (``dropdown-search/...``)
# and the HTML cascade endpoint (``dropdown-options/...``); the cascade
# route passes ``query=''`` to get the seed page of the narrowed
# universe. Tom Select's ``load`` callback fires this on every typed
# keystroke (debounced 300ms via ``loadThrottle``).
OptionsSearchFetcher = Callable[
    [str, str, str, Mapping[str, list[str]]],
    Awaitable[OptionsSearchResult],
]


def make_options_search_fetcher(
    cfg: Config,
    *,
    pool: AsyncConnectionPool,
) -> OptionsSearchFetcher:
    """CQ.2.b/g — server-side typeahead + seed page fetcher.

    Dispatch:
    - If the dataset has a registered ``PickerMatviewHint`` →
      matview-direct path (single matview, sub-10ms at 50k accounts).
    - Otherwise → wrap path (wraps the dataset CustomSql, slower but
      universe-correct for multi-matview UNION pickers).

    Empty ``query`` → seed page (top-N alphabetical, drives Tom
    Select's ``preload: 'focus'``).

    Typed ``query`` → case-insensitive substring search. The query is
    truncated to ``_picker_query_cap()`` characters (default 500,
    overridable via ``RECON_GEN_PICKER_MAX_QUERY_LEN``) + LIKE-escaped
    via :func:`escape_like_pattern` BEFORE binding (without escape,
    typing ``5%`` matches every row containing ``5``). ``OptionsSearchResult.truncated``
    surfaces the overflow signal to the route layer so the JSON
    typeahead response can banner the UI on silent-match-failure
    (CR.2 — pre-CR.2 the cap was 100 + silently truncated with no
    signal back, so a customer with > 100-char identifiers got
    zero matches and no way to discover why).

    Routes both the JSON typeahead endpoint
    (``dropdown-search/{dataset}/{column}?q=...``) and the HTML
    cascade endpoint (``dropdown-options/{dataset}/{column}``,
    ``query=''``) through this single fetcher — same option semantics,
    different response shape.
    """
    async def fetch(
        dataset_identifier: str,
        column: str,
        query: str,
        url_params: Mapping[str, list[str]],
    ) -> OptionsSearchResult:
        cap = _picker_query_cap()
        trimmed_query = query[:cap]
        truncated = len(query) > cap
        hint = get_picker_matview_hint(dataset_identifier)
        if trimmed_query:
            escaped = escape_like_pattern(trimmed_query)
            extra_binds: Mapping[str, str] = {"q": escaped}
            options_sql = (
                _picker_search_sql_matview_direct(hint, dialect=cfg.dialect)
                if hint is not None
                else _picker_search_sql_wrap(
                    get_sql(dataset_identifier), column, dialect=cfg.dialect,
                )
            )
        else:
            extra_binds = {}
            options_sql = (
                _picker_seed_sql_matview_direct(hint, dialect=cfg.dialect)
                if hint is not None
                else _picker_seed_sql_wrap(
                    get_sql(dataset_identifier), column, dialect=cfg.dialect,
                )
            )
        rows, _columns = await execute_visual_sql_async(
            pool, options_sql, url_params, dialect=cfg.dialect,
            dataset_parameters=get_dataset_params(dataset_identifier),
            extra_binds=extra_binds,
        )
        return OptionsSearchResult(
            options=tuple(str(r[0]) for r in rows if r[0] is not None),
            truncated=truncated,
        )

    return fetch


# CQ.2.e — pre-CQ.2 ``OptionsFetcher`` + ``make_options_fetcher`` +
# ``_OPTIONS_CAP = 2000`` deleted 2026-06-08 per the operator-locked
# direction "truncating at 2000 rows SUCKS in production... we must
# do server side querying." Both the JSON typeahead endpoint
# (per-keystroke search) and the HTML cascade endpoint (sibling-
# change seed-page refresh) now route through
# ``make_options_search_fetcher`` — cascade passes ``query=''`` to
# get the seed page, typeahead passes the user-typed ``q``. No
# silent truncation; no two parallel code paths.
