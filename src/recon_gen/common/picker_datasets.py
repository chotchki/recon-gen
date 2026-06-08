"""CQ.3.c — Shared LinkedValues source datasets for L2-declared
picker universes.

Pre-CQ.3 every L2-derived dropdown (rail / template / account_role /
metadata_key) bound to ``StaticValues([sentinel + <l2-derived list>])``.
At sasquatch_pr scale the lists are small (≤32 across the bundled
demo), but the AWS ``ParameterDropDownControl.StaticValues`` ceiling
is 32 — a real customer L2 declaring 40+ rails crashes the QS deploy
at build time with no operator recourse (the picker source IS code;
they can't fix it without an upstream patch).

CQ.3.c replaces every such site with ``LinkedValues.from_column(<this
module's dataset>)``. The dataset queries a ``_v_config_*`` typed
view derived from ``<prefix>_config_kv`` (BC.12) — so the option
universe is L2-declarative, unbounded, and read at query time. The
CQ.2 typeahead infrastructure picks up the new pickers without any
per-dropdown wiring: ``LinkedValues`` pickers auto-route to the
JSON ``dropdown-search/...`` endpoint + matview-direct fetch via
``PickerMatviewHint``.

Both L1 + L2FT reference the same 4 datasets (rails / templates /
account_roles / metadata_keys) — per operator lock 2026-06-08,
anything sourced from ``_kv``-derived views is shared cross-app.
Each app's ``build_all_datasets`` calls
:func:`build_shared_picker_datasets` and extends its return list;
the AWS deploy de-dups by ``DataSetId``.

Static-values dropdowns sourced from fixed code enums (Check Type /
Supersedes / TX Status / Bundle Status / TT Completion — bounded
≤7) keep ``StaticValues``: they're frozen at code time, not L2-time,
and cannot grow.
"""

from __future__ import annotations

from recon_gen.common.config import Config
from recon_gen.common.dataset_contract import (
    ColumnSpec,
    DatasetContract,
    build_dataset,
)
from recon_gen.common.html._tree_fetcher import PickerMatviewHint
from recon_gen.common.models import DataSet


# Visual identifiers — stable, app-agnostic. App trees reference these
# via ``datasets[DS_RAILS]["name"]`` etc.
DS_RAILS = "v-config-rails-ds"
DS_TEMPLATES = "v-config-transfer-templates-ds"
DS_ACCOUNT_ROLES = "v-config-account-roles-ds"
DS_METADATA_KEYS = "v-config-metadata-keys-ds"
DS_CHAIN_PARENTS = "v-config-chain-parents-ds"


# Per-dataset contracts. Each picker source projects ONE column —
# the value the picker binds.
_RAILS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("name", "STRING"),
])
_TEMPLATES_CONTRACT = DatasetContract(columns=[
    ColumnSpec("name", "STRING"),
])
_ACCOUNT_ROLES_CONTRACT = DatasetContract(columns=[
    ColumnSpec("account_role", "STRING"),
])
_METADATA_KEYS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("metadata_key", "STRING"),
])
_CHAIN_PARENTS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("parent_name", "STRING"),
])


def _build_rails_dataset(cfg: Config) -> DataSet:
    """``DS_RAILS`` — declared Rail names (one column: ``name``).

    Source: ``<prefix>_v_config_rails.name``. Drives the L1 Rail
    dropdowns (Pending / Unbundled / etc.) + the L2FT Rail dropdowns.

    Note: pre-CQ.3, ``l1_rail_universe_values`` widened this source
    with ``LimitSchedule.rail`` for the L1 Drift / Limit Breach
    sheets — limit_breach can surface rows for a rail declared only
    via a LimitSchedule. CQ.3 narrows to ``Rail.name`` only as the
    UX trade-off: operators picking a limit-schedule-only rail on
    those sheets would see an empty matview anyway. If a customer's
    LS-only-rail universe matters in practice, follow-up
    ``_v_config_rails_universe`` UNION view can be added without
    touching this dataset's consumers.
    """
    prefix = cfg.db_table_prefix
    sql = f"SELECT name FROM {prefix}_v_config_rails"
    return build_dataset(
        cfg, cfg.prefixed("v-config-rails-dataset"),
        "Picker — Rails", "v-config-rails",
        sql, _RAILS_CONTRACT,
        visual_identifier=DS_RAILS,
        picker_matview_hint=PickerMatviewHint(
            matview=f"{prefix}_v_config_rails",
            select_expr="name",
        ),
    )


def _build_templates_dataset(cfg: Config) -> DataSet:
    """``DS_TEMPLATES`` — declared TransferTemplate names.

    Source: ``<prefix>_v_config_transfer_templates.name``. Drives the
    L2FT Template dropdown.
    """
    prefix = cfg.db_table_prefix
    sql = f"SELECT name FROM {prefix}_v_config_transfer_templates"
    return build_dataset(
        cfg, cfg.prefixed("v-config-transfer-templates-dataset"),
        "Picker — Transfer Templates", "v-config-transfer-templates",
        sql, _TEMPLATES_CONTRACT,
        visual_identifier=DS_TEMPLATES,
        picker_matview_hint=PickerMatviewHint(
            matview=f"{prefix}_v_config_transfer_templates",
            select_expr="name",
        ),
    )


def _build_account_roles_dataset(cfg: Config) -> DataSet:
    """``DS_ACCOUNT_ROLES`` — DISTINCT account roles across L2's
    accounts + account_templates.

    Source: ``<prefix>_v_config_account_roles.account_role`` (already
    DISTINCT inside the view body). Drives the L1 Account-Role
    dropdowns (Drift / Drift Timelines / Overdraft).
    """
    prefix = cfg.db_table_prefix
    sql = f"SELECT account_role FROM {prefix}_v_config_account_roles"
    return build_dataset(
        cfg, cfg.prefixed("v-config-account-roles-dataset"),
        "Picker — Account Roles", "v-config-account-roles",
        sql, _ACCOUNT_ROLES_CONTRACT,
        visual_identifier=DS_ACCOUNT_ROLES,
        picker_matview_hint=PickerMatviewHint(
            matview=f"{prefix}_v_config_account_roles",
            select_expr="account_role",
        ),
    )


def _build_metadata_keys_dataset(cfg: Config) -> DataSet:
    """``DS_METADATA_KEYS`` — DISTINCT metadata keys declared on rails.

    Source: ``SELECT DISTINCT metadata_key FROM <prefix>_v_config_
    rail_metadata_keys``. The underlying view is rail-scoped (CQ.3.b,
    one row per ``(rail_name, metadata_key)`` pair) so a future rail-
    filtered narrowing capability stays cheap; the picker just
    DISTINCTs across rails today.

    Drives the L2FT Metadata Key dropdowns (Rails / Chains / Transfer
    Templates sheets).
    """
    prefix = cfg.db_table_prefix
    sql = (
        f"SELECT DISTINCT metadata_key "
        f"FROM {prefix}_v_config_rail_metadata_keys"
    )
    return build_dataset(
        cfg, cfg.prefixed("v-config-metadata-keys-dataset"),
        "Picker — Metadata Keys", "v-config-metadata-keys",
        sql, _METADATA_KEYS_CONTRACT,
        visual_identifier=DS_METADATA_KEYS,
        picker_matview_hint=PickerMatviewHint(
            matview=f"{prefix}_v_config_rail_metadata_keys",
            select_expr="metadata_key",
        ),
    )


def _build_chain_parents_dataset(cfg: Config) -> DataSet:
    """``DS_CHAIN_PARENTS`` — DISTINCT chain parent names declared in L2.

    Source: ``SELECT DISTINCT parent_name FROM
    <prefix>_v_config_chain_children`` (BS.5's existing view; one row
    per declared ChainChildSpec, parent_name repeats across each
    child). Drives the L2FT Chains sheet's Chain dropdown.
    """
    prefix = cfg.db_table_prefix
    sql = (
        f"SELECT DISTINCT parent_name "
        f"FROM {prefix}_v_config_chain_children"
    )
    return build_dataset(
        cfg, cfg.prefixed("v-config-chain-parents-dataset"),
        "Picker — Chain Parents", "v-config-chain-parents",
        sql, _CHAIN_PARENTS_CONTRACT,
        visual_identifier=DS_CHAIN_PARENTS,
        picker_matview_hint=PickerMatviewHint(
            matview=f"{prefix}_v_config_chain_children",
            select_expr="parent_name",
        ),
    )


# CR.x — per-dataset public builders. CI failure on cf032797 surfaced
# the "L1 emits templates but no L1 visual binds it → DataSetIdentifier-
# Declarations missing → structural test fails" shape: L1 was using
# the all-5 ``build_shared_picker_datasets`` even though it doesn't
# bind Templates. Each app now imports + emits the specific shared
# datasets it actually uses; AWS DataSetId de-dup still applies when
# two apps emit the same dataset.
def build_picker_rails_dataset(cfg: Config) -> DataSet:
    return _build_rails_dataset(cfg)


def build_picker_templates_dataset(cfg: Config) -> DataSet:
    return _build_templates_dataset(cfg)


def build_picker_account_roles_dataset(cfg: Config) -> DataSet:
    return _build_account_roles_dataset(cfg)


def build_picker_metadata_keys_dataset(cfg: Config) -> DataSet:
    return _build_metadata_keys_dataset(cfg)


def build_picker_chain_parents_dataset(cfg: Config) -> DataSet:
    return _build_chain_parents_dataset(cfg)


def build_shared_picker_datasets(cfg: Config) -> list[DataSet]:
    """Build all 5 shared picker-source datasets.

    Apps that use ALL shared pickers (currently L2FT — uses every one)
    can call this for convenience. Apps that use a subset (currently
    L1 — doesn't bind Templates) should call the specific
    ``build_picker_*`` builders directly so the dashboard's
    DataSetIdentifierDeclarations matches what gets emitted.

    AWS deploy de-dups by ``DataSetId``; the registry-side
    (``register_sql`` / ``register_picker_matview_hint``) is
    idempotent on repeat (overwrite-with-identical-content).
    """
    return [
        _build_rails_dataset(cfg),
        _build_templates_dataset(cfg),
        _build_account_roles_dataset(cfg),
        _build_metadata_keys_dataset(cfg),
        _build_chain_parents_dataset(cfg),
    ]
