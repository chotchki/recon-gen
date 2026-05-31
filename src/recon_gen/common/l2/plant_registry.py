"""BU.1 — Plant registry for the Trainer surface.

The single source of truth for every violation kind the Trainer
surfaces (per BU.0 Lock 7). The registry is a tuple of
``PlantKindEntry`` records; the Trainer UI + the plant invocation
+ the tour iframe + the parameterized e2e contract all data-drive
off the entry.

Per BU.0 Lock 8 — the registry is a **thin index** over typed
violation-class sections (``InvariantSection`` / ``L2FTExceptionSection``
/ ``L2TriageGapSection``). Display strings (title / short statement
/ remediation) live on the section. The entry just carries:

- ``kind`` — canonical machine name; matches the section's ``kind``
  (with optional ``section_kind`` override for slug-mismatch edge
  cases per BU.0 round-4 Notes).
- ``category`` — discriminator for which typed-section lookup to use
  via ``resolve_section`` (Lock 8).
- ``family`` — accordion grouping on the landing page.
- ``plant_function`` — callable that returns the SQL string applied
  to the demo DB.
- ``primitives`` — typed form-field schema operator fills in.
- ``tour_destination`` — iframe URL + sheet anchor for the Tour page.
- ``dashboard_check`` — parameterized e2e contract (BU.0 Lock 9).

Per BU.1 (vertical slice) — this module ships with ONE entry
(``phantom_rail``) so the rendering + tour + e2e patterns can be
validated end-to-end before scaling. BU.2b populates the remaining
20 entries; BU.3.x adds the 5 needs-build plants.

Adding a future violation kind = one row here + one markdown section
in the existing handbook parser + zero new UI / test files.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Final


class PlantCategory(str, Enum):
    """Per BU.0 §0.5 matrix — drives ``resolve_section`` dispatch."""

    L1_INVARIANT = "l1_invariant"
    L2_TRIAGE = "l2_triage"
    L2_COVERAGE = "l2_coverage"
    L2FT_HYGIENE = "l2ft_hygiene"


# -- Form primitive types ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class PrimitiveStringField:
    """Free-text string input. Renders as ``<input type="text">``."""

    name: str
    label: str
    help_text: str
    default: str


@dataclass(frozen=True, slots=True)
class PrimitiveIntField:
    """Integer input. Renders as ``<input type="number">`` with
    optional min/max attrs."""

    name: str
    label: str
    help_text: str
    default: int
    min_value: int | None = None
    max_value: int | None = None


# Union of all primitive field shapes.
PrimitiveField = PrimitiveStringField | PrimitiveIntField


# -- Tour destination -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TourDestination:
    """Where the Tour page iframes into.

    ``primary_url`` is the full URL (no query-param expansion in the
    slice; BU.2+ may extend with `{prefix}` / `{form_<field>}` template
    substitution if needed).
    """

    primary_url: str
    secondary_links: tuple[tuple[str, str], ...] = ()


# -- Dashboard-check contract (BU.0 Lock 9) ---------------------------------


@dataclass(frozen=True, slots=True)
class DashboardCheck:
    """The parameterized e2e contract: after the plant fires + the
    matview refresh runs, what should be observable on the dashboard?

    Two shapes:

    - ``matview_name`` + ``min_row_count`` — for kinds backed by a
      precomputed matview (L1 + L2FT Hygiene). The e2e queries the
      matview directly + asserts row count.
    - ``url_path`` + ``expect_text_contains`` — for kinds whose
      "matview" is computed at-query-time by the route (L2 Triage —
      `/etl/triage` renders the gap card from `detect_gaps()` against
      the live tables, no stored matview).

    Exactly one shape is set per entry; the parameterized e2e
    branches on which is present.
    """

    matview_name: str | None = None
    min_row_count: int = 1
    url_path: str | None = None
    expect_text_contains: str | None = None


# -- The registry entry -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlantKindEntry:
    """Thin index per BU.0 Lock 8 — display strings live on the
    typed violation section referenced by ``kind`` (or
    ``section_kind`` when there's a slug mismatch with the handbook
    parser's auto-derived key).
    """

    kind: str
    category: PlantCategory
    family: str
    plant_function: Callable[..., str]
    primitives: tuple[PrimitiveField, ...]
    tour_destination: TourDestination
    dashboard_check: DashboardCheck
    # Optional override when the canonical kind doesn't match the
    # typed-section's auto-derived slug (per BU.0 round-4 Notes,
    # e.g. `dead_metadata` vs `dead_metadata_declarations`).
    section_kind: str | None = None


# -- Plant-function adapters ------------------------------------------------


def _invoke_phantom_rail_plant(
    *,
    prefix: str,
    dialect: object,  # typing-smell: ignore[explicit-any]: Dialect — avoid circular import w/ common.sql.dialect at module load
    anchor: datetime,
    count: int,
    rail_name: str,
) -> str:
    """Adapter from registry primitives → ``add_phantom_rail_gap_rows``.

    Keeps the registry decoupled from ``demo_etl_gaps``'s exact
    signature so the BU.2b populate step can swap adapters per kind
    without touching the renderer.
    """
    from recon_gen.common.l2.demo_etl_gaps import add_phantom_rail_gap_rows  # noqa: PLC0415
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    return add_phantom_rail_gap_rows(
        prefix=prefix,
        dialect=dialect if isinstance(dialect, Dialect) else Dialect.SQLITE,
        anchor=anchor,
        count=count,
        rail_name=rail_name,
    )


# -- THE registry -----------------------------------------------------------


PLANT_REGISTRY: Final[tuple[PlantKindEntry, ...]] = (
    PlantKindEntry(
        kind="phantom_rail",
        category=PlantCategory.L2_TRIAGE,
        family="L2 Triage gaps",
        # BU.2a — bridge to the typed L2_Triage_Gaps.md section. The
        # registry kind is operator-vocabulary ("phantom_rail" reads
        # well in the Trainer URL); the underlying GapKind literal is
        # the parser key.
        section_kind="unmatched_rail",
        plant_function=_invoke_phantom_rail_plant,
        primitives=(
            PrimitiveIntField(
                name="count",
                label="Number of rows",
                help_text=(
                    "How many transaction rows to plant. Triage's "
                    "volume badge reads this count directly."
                ),
                default=3,
                min_value=1,
                max_value=100,
            ),
            PrimitiveStringField(
                name="rail_name",
                label="Rail name",
                help_text=(
                    "The rail_name value to plant. Must NOT match any "
                    "rail declared in your L2 (the whole point of the "
                    "demo). Default reads like a plausible legacy rail."
                ),
                default="legacy_card_swipe",
            ),
        ),
        tour_destination=TourDestination(
            primary_url="/etl/triage",
        ),
        dashboard_check=DashboardCheck(
            url_path="/etl/triage",
            expect_text_contains="legacy_card_swipe",
        ),
    ),
)


# -- Registry helpers -------------------------------------------------------


def get_entry(kind: str) -> PlantKindEntry | None:
    """O(N) lookup — N is small (~21 at full scale)."""
    for entry in PLANT_REGISTRY:
        if entry.kind == kind:
            return entry
    return None


def entries_by_family() -> Mapping[str, tuple[PlantKindEntry, ...]]:
    """Group registry entries by family for the landing accordion."""
    groups: dict[str, list[PlantKindEntry]] = {}
    for entry in PLANT_REGISTRY:
        groups.setdefault(entry.family, []).append(entry)
    return {f: tuple(es) for f, es in groups.items()}


# Field placeholder for future tooling — silences unused-import warnings
# while the registry skeleton is small. Drops in BU.2b once the full
# 21-entry populate lands.
_RESERVED: Final[tuple[object, ...]] = (field,)
