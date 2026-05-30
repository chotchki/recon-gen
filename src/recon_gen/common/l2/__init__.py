"""LAYER 2 institutional model — typed primitives + loader + validator.

The production library code that drives every per-prefix L2 surface:

  primitives.py — typed dataclasses for every L2 SPEC primitive.
  loader.py     — YAML → primitives, with friendly error messages.
  validate.py   — load-time SPEC validation rules + rejection tests.
  schema.py     — prefix-aware SQL emission for L1 + L2 tables + matviews.
  seed.py       — 90-day baseline + plant overlays (emit_full_seed).
  derived.py    — Current* / computed_* helpers (PostedRequirements etc.).

External callers import from this package's public surface
(``from recon_gen.common.l2 import L2Instance``), not from any
internal submodule.
"""

from .contract import (
    ChainEdgeContract,
    ColumnContracts,
    ColumnPredicate,
    LimitContract,
    PredicateKind,
    RailContract,
    RowSelector,
    TemplateContract,
    derive_column_contracts,
)
from .derived import PARENT_TRANSFER_ID, posted_requirements_for
from .loader import (
    L2LoaderError,
    default_l2_bytes,
    default_l2_instance,
    load_instance,
)
from .schema import emit_schema, emit_schema_drop_sql, refresh_matviews_sql
from .theme import ThemePreset
from .validate import L2ValidationError, validate
from .primitives import (
    Account,
    AccountTemplate,
    BundlesActivityRef,
    CadenceExpression,
    Chain,
    ChainChildSpec,
    CompletionExpression,
    Duration,
    Identifier,
    L2Instance,
    LegDirection,
    LimitSchedule,
    Money,
    Name,
    Origin,
    Rail,
    RailName,
    RoleExpression,
    Scope,
    SingleLegRail,
    SupersedeReason,
    TransferTemplate,
    TransferType,
    TwoLegRail,
)

__all__ = [
    "Account",
    "AccountTemplate",
    "BundlesActivityRef",
    "CadenceExpression",
    "Chain",
    "ChainChildSpec",
    "ChainEdgeContract",
    "ColumnContracts",
    "ColumnPredicate",
    "CompletionExpression",
    "Duration",
    "Identifier",
    "L2Instance",
    "L2LoaderError",
    "L2ValidationError",
    "LegDirection",
    "LimitContract",
    "LimitSchedule",
    "Money",
    "Name",
    "Origin",
    "PARENT_TRANSFER_ID",
    "PredicateKind",
    "Rail",
    "RailContract",
    "RailName",
    "RoleExpression",
    "RowSelector",
    "Scope",
    "SingleLegRail",
    "SupersedeReason",
    "TemplateContract",
    "ThemePreset",
    "TransferTemplate",
    "TransferType",
    "TwoLegRail",
    "derive_column_contracts",
    "emit_schema",
    "emit_schema_drop_sql",
    "refresh_matviews_sql",
    "load_instance",
    "default_l2_instance",
    "default_l2_bytes",
    "posted_requirements_for",
    "validate",
]
