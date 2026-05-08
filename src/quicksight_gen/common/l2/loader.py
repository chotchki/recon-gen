"""YAML → ``L2Instance`` loader.

Single entry point ``load_instance(path)`` reads an L2 YAML file, walks
the top-level sections (``accounts`` / ``account_templates`` / ``rails``
/ ``transfer_templates`` / ``chains`` / ``limit_schedules``), and emits
a fully-typed ``L2Instance`` per the SPEC.

What this module does:
- YAML parsing via ``yaml.safe_load`` (file/line in syntax errors comes
  from PyYAML for free).
- Required-field + type-shape checks per primitive (e.g. ``Account.id``
  is required + must be a string).
- ``Decimal(str(value))`` coercion for every Money-typed field via the
  shared ``_load_money`` helper (per F4 — dodges YAML float precision).
- ``InstancePrefix`` regex + length validation via ``_load_instance_prefix``
  (per F5 — pinned in SPEC as ``^[a-z][a-z0-9_]*$``, max 30 chars).
- Rail discrimination: presence of ``source_role`` / ``destination_role``
  → ``TwoLegRail``; presence of ``leg_role`` / ``leg_direction`` →
  ``SingleLegRail``; both or neither → error.
- ``RoleExpression`` normalization: a single string YAML value becomes
  a 1-tuple; a YAML list of strings becomes a tuple.

Cross-entity validation (M.1.3 ``validate.py``) runs automatically as
the last step of ``load_instance`` (per M.2d.2 — every cross-entity
rule is a YAML parse-time error by default). The loader still owns
syntactic + per-entity errors; the validator owns reference-resolution,
cardinality, state-dependent, vocabulary, and per-leg-Origin rules.
Tests that need to construct intentionally-incomplete instances may
opt out via ``load_instance(path, validate=False)``.

Errors raise ``L2LoaderError`` (loader-side) or ``L2ValidationError``
(validator-side) with a logical path (e.g. ``accounts[2].id``) so the
caller can pinpoint the bad field.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

import yaml

from quicksight_gen.common.env_keys import QS_GEN_RUN_DIR

from .primitives import (
    Account,
    AccountTemplate,
    BundlesActivityRef,
    CadenceExpression,
    ChainEntry,
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
    RoleExpression,
    Scope,
    SingleLegRail,
    TransferTemplate,
    TransferType,
    TwoLegRail,
)
from .theme import ThemePreset
from quicksight_gen.common.persona import DemoPersona, GLAccount


# -- Errors -------------------------------------------------------------------


class L2LoaderError(ValueError):
    """Raised when an L2 YAML fails to load or fails per-entity validation."""


# -- Identifier validation (F5) ----------------------------------------------


# Per SPEC's Instance Prefix Format rule (F5 amendment):
#   MUST match ^[a-z][a-z0-9_]*$, max 30 characters.
# Lowercase-only avoids Postgres' quoted-vs-unquoted hazard;
# 30-char cap leaves room for the longest table-name suffix within
# Postgres' 63-char identifier limit.
_INSTANCE_PREFIX_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_INSTANCE_PREFIX_MAX = 30


def _load_instance_prefix(raw: object, *, path: str) -> Identifier:
    """Validate and return an InstancePrefix per SPEC's F5 rules."""
    if not isinstance(raw, str):
        raise L2LoaderError(
            f"{path}: expected a string instance prefix, "
            f"got {type(raw).__name__}"
        )
    if not _INSTANCE_PREFIX_RE.match(raw):
        raise L2LoaderError(
            f"{path}={raw!r}: must match {_INSTANCE_PREFIX_RE.pattern!r} "
            f"(SQL-identifier-safe; lowercase start; alphanumeric or "
            f"underscore thereafter)"
        )
    if len(raw) > _INSTANCE_PREFIX_MAX:
        raise L2LoaderError(
            f"{path}={raw!r}: max {_INSTANCE_PREFIX_MAX} characters "
            f"(got {len(raw)})"
        )
    return Identifier(raw)


def _load_identifier(raw: object, *, path: str) -> Identifier:
    """Validate and return a generic Identifier (Role / Rail / Account ID / etc.).

    Loose constraint: must be a non-empty string. Per the F5 finding,
    the strict regex applies to InstancePrefix only — other identifier
    fields use whatever conventions the SPEC's worked examples follow
    (PascalCase Roles, snake_case TransferTypes, …) and the SPEC
    doesn't pin a single regex for all of them.
    """
    if not isinstance(raw, str):
        raise L2LoaderError(
            f"{path}: expected a string identifier, got {type(raw).__name__}"
        )
    if not raw:
        raise L2LoaderError(f"{path}: identifier must be non-empty")
    return Identifier(raw)


# -- Money coercion (F4) -----------------------------------------------------


def _load_money(raw: object, *, path: str) -> Money:
    """Coerce a YAML numeric to Decimal via ``Decimal(str(raw))`` per F4.

    YAML's ``safe_load`` returns ``int``/``float`` for numerics; constructing
    ``Decimal`` from ``float`` loses precision (``Decimal(0.1) ==
    Decimal('0.10000000000000000555...')``). The fix is to round-trip
    through ``str``: ``Decimal(str(0.1))`` produces the expected
    ``Decimal('0.1')``. Also accepts string + Decimal inputs for
    integrators who prefer to author Money explicitly.
    """
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, (int, float, str)):
        try:
            return Decimal(str(raw))
        except InvalidOperation as exc:
            raise L2LoaderError(
                f"{path}={raw!r}: not a valid decimal money value"
            ) from exc
    raise L2LoaderError(
        f"{path}: expected money (number or decimal string), "
        f"got {type(raw).__name__}"
    )


# -- Duration parsing (ISO 8601, M.1a.2) -------------------------------------


# ISO 8601 duration subset supporting the SPEC's worked-example shapes:
# ``PT24H`` (hours), ``PT4H`` (hours), ``PT30M`` (minutes), ``P1D`` (days),
# ``P7D`` (days), and combinations like ``P1DT12H`` (mixed days+hours).
# Years/months are deliberately rejected — neither has a fixed
# ``timedelta`` representation (a "month" depends on which calendar
# month you're in). Aging windows in v1 are short enough that
# days+hours+minutes+seconds covers every realistic case.
_ISO_DURATION_RE = re.compile(
    r"^P"
    r"(?:(?P<days>\d+)D)?"
    r"(?:T"
    r"(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)S)?"
    r")?$"
)


def _load_duration(raw: object, *, path: str) -> Duration:
    """Parse an ISO 8601 duration literal into ``datetime.timedelta``.

    Accepts the shapes the SPEC's aging-window examples use: ``PT24H``,
    ``PT4H``, ``PT30M``, ``P1D``, ``P7D``, plus combined forms
    (``P1DT12H``). Rejects year/month forms (no fixed duration) and any
    string that doesn't fit the ISO 8601 grammar.
    """
    if not isinstance(raw, str):
        raise L2LoaderError(
            f"{path}: expected ISO 8601 duration string (e.g. 'PT24H'), "
            f"got {type(raw).__name__}"
        )
    match = _ISO_DURATION_RE.match(raw)
    if match is None or raw == "P" or raw == "PT":
        raise L2LoaderError(
            f"{path}={raw!r}: not an ISO 8601 duration literal "
            f"(expected forms like 'PT24H', 'P1D', 'P1DT12H'; year/month "
            f"forms not supported)"
        )
    parts = {k: int(v) for k, v in match.groupdict().items() if v is not None}
    if not parts:
        raise L2LoaderError(
            f"{path}={raw!r}: empty duration (no numeric components)"
        )
    return timedelta(**parts)


# -- Generic field helpers ---------------------------------------------------


def _as_mapping(raw: object, *, path: str, what: str) -> dict[str, object]:
    """Narrow a raw YAML value to ``dict[str, object]`` or fail loudly.

    PyYAML's ``safe_load`` returns ``Any``; pyright strict surfaces every
    downstream use as ``Unknown``. Centralizing the ``isinstance`` check
    here lets each per-primitive loader work with a precisely-typed
    mapping (and produces a uniform error message including the
    primitive name).
    """
    if not isinstance(raw, dict):
        raise L2LoaderError(
            f"{path}: {what} must be a mapping, got {type(raw).__name__}"
        )
    return cast("dict[str, object]", raw)


def _as_list(raw: object, *, path: str) -> list[object]:
    """Narrow a raw YAML value to ``list[object]``; ``None`` → ``[]``.

    Used for the top-level section lists (``accounts``, ``rails``, …)
    where missing/null is fine and means "no entries of this kind".
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise L2LoaderError(
            f"{path}: expected a list, got {type(raw).__name__}"
        )
    return cast("list[object]", raw)


def _require(raw: dict[str, object], key: str, *, path: str) -> object:
    """Pull a required field; raise if missing."""
    if key not in raw:
        raise L2LoaderError(f"{path}: missing required field {key!r}")
    return raw[key]


def _load_string(raw: object, *, path: str) -> str:
    """Validate a non-empty string."""
    if not isinstance(raw, str):
        raise L2LoaderError(
            f"{path}: expected string, got {type(raw).__name__}"
        )
    if not raw:
        raise L2LoaderError(f"{path}: string must be non-empty")
    return raw


def _load_scope(raw: object, *, path: str) -> Scope:
    if raw not in ("internal", "external"):
        raise L2LoaderError(
            f"{path}={raw!r}: scope must be 'internal' or 'external'"
        )
    return raw  # type: ignore[return-value]


def _load_leg_direction(raw: object, *, path: str) -> LegDirection:
    if raw not in ("Debit", "Credit", "Variable"):
        raise L2LoaderError(
            f"{path}={raw!r}: leg_direction must be 'Debit', 'Credit', "
            f"or 'Variable'"
        )
    return raw  # type: ignore[return-value]


_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


_THEME_COLOR_FIELDS: tuple[str, ...] = (
    # 17 single-color UI fields on ThemePreset.
    "empty_fill_color",
    "primary_bg",
    "secondary_bg",
    "primary_fg",
    "secondary_fg",
    "accent",
    "accent_fg",
    "link_tint",
    "danger",
    "danger_fg",
    "warning",
    "warning_fg",
    "success",
    "success_fg",
    "dimension",
    "dimension_fg",
    "measure",
    "measure_fg",
)


def _load_role_business_day_offsets(
    raw: object, *, path: str,
) -> dict[str, int] | None:
    """Optional ``{role_name: hours}`` map (M.4.4.14).

    Each value must be an int in [0, 24); the seed adds the hours to
    midnight-of-day to compute ``business_day_start`` (and the same
    offset propagates to ``business_day_end`` so the 24-hour window
    contract holds). Roles absent from the map default to
    midnight-aligned.

    None / missing returns None — caller treats that as
    "every role midnight-aligned".
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise L2LoaderError(
            f"{path}: role_business_day_offsets must be a mapping, "
            f"got {type(raw).__name__}"
        )
    raw_dict = cast(dict[Any, Any], raw)
    out: dict[str, int] = {}
    for role_raw, hours_raw in raw_dict.items():
        if not isinstance(role_raw, str) or not role_raw:
            raise L2LoaderError(
                f"{path}.{role_raw!r}: keys must be non-empty role-name "
                f"strings, got {type(role_raw).__name__}"
            )
        if not isinstance(hours_raw, int) or isinstance(hours_raw, bool):
            raise L2LoaderError(
                f"{path}.{role_raw!r}={hours_raw!r}: value must be an int "
                f"hours offset, got {type(hours_raw).__name__}"
            )
        if not (0 <= hours_raw < 24):
            raise L2LoaderError(
                f"{path}.{role_raw!r}={hours_raw!r}: hours must be in [0, 24)"
            )
        out[role_raw] = hours_raw
    return out or None


def _load_hex_color(raw: object, *, path: str) -> str:
    """Parse a single hex color string. Returns the original-case string."""
    if not isinstance(raw, str):
        raise L2LoaderError(
            f"{path}: must be a hex color string, got {type(raw).__name__}"
        )
    if not _HEX_COLOR_RE.match(raw):
        raise L2LoaderError(
            f"{path}={raw!r}: must match #RRGGBB hex color format "
            f"(e.g. '#1B2A4A')"
        )
    return raw


def _load_theme(
    raw: object, *, path: str, base_dir: Path | None = None,
) -> ThemePreset | None:
    """Optional inline theme block per N.1.

    YAML shape mirrors the ``ThemePreset`` dataclass 1:1:

        theme:
          theme_name: "Sasquatch National Bank Theme"
          version_description: "Forest green palette"
          analysis_name_prefix: "Demo"      # null/omit for production
          data_colors:                       # >=1 hex strings
            - "#2D6A4F"
            - "#C49A2A"
          empty_fill_color: "#D6D6CE"
          gradient: ["#C5DDD3", "#1B4332"]   # exactly 2: [light, dark]
          primary_bg: "#FFFFFF"
          # ...all 17 single-color UI fields...

    None / missing returns None — the app falls back to the registry
    ``default`` preset.
    """
    if raw is None:
        return None
    raw_d = _as_mapping(raw, path=path, what="theme")

    theme_name_raw = _require(raw_d, "theme_name", path=path)
    theme_name = _load_string(theme_name_raw, path=f"{path}.theme_name")

    version_description_raw = _require(raw_d, "version_description", path=path)
    version_description = _load_string(
        version_description_raw, path=f"{path}.version_description",
    )

    analysis_name_prefix: str | None
    if "analysis_name_prefix" in raw_d and raw_d["analysis_name_prefix"] is not None:
        analysis_name_prefix = _load_string(
            raw_d["analysis_name_prefix"],
            path=f"{path}.analysis_name_prefix",
        )
    else:
        analysis_name_prefix = None

    data_colors_raw = _as_list(
        _require(raw_d, "data_colors", path=path),
        path=f"{path}.data_colors",
    )
    if not data_colors_raw:
        raise L2LoaderError(
            f"{path}.data_colors: must contain at least one color"
        )
    data_colors = [
        _load_hex_color(c, path=f"{path}.data_colors[{i}]")
        for i, c in enumerate(data_colors_raw)
    ]

    gradient_raw = _as_list(
        _require(raw_d, "gradient", path=path),
        path=f"{path}.gradient",
    )
    if len(gradient_raw) != 2:
        raise L2LoaderError(
            f"{path}.gradient: must be exactly 2 colors [light, dark], "
            f"got {len(gradient_raw)}"
        )
    gradient = [
        _load_hex_color(c, path=f"{path}.gradient[{i}]")
        for i, c in enumerate(gradient_raw)
    ]

    colors: dict[str, str] = {}
    for field_name in _THEME_COLOR_FIELDS:
        value_raw = _require(raw_d, field_name, path=path)
        colors[field_name] = _load_hex_color(
            value_raw, path=f"{path}.{field_name}",
        )

    # Optional brand assets — URL, absolute file path, or path relative
    # to the L2 YAML's directory (resolved at load time).
    logo = _load_optional_brand_asset(
        raw_d.get("logo"), path=f"{path}.logo", base_dir=base_dir,
    )
    favicon = _load_optional_brand_asset(
        raw_d.get("favicon"), path=f"{path}.favicon", base_dir=base_dir,
    )

    return ThemePreset(
        theme_name=theme_name,
        version_description=version_description,
        analysis_name_prefix=analysis_name_prefix,
        data_colors=data_colors,
        gradient=gradient,
        empty_fill_color=colors["empty_fill_color"],
        primary_bg=colors["primary_bg"],
        secondary_bg=colors["secondary_bg"],
        primary_fg=colors["primary_fg"],
        secondary_fg=colors["secondary_fg"],
        accent=colors["accent"],
        accent_fg=colors["accent_fg"],
        link_tint=colors["link_tint"],
        danger=colors["danger"],
        danger_fg=colors["danger_fg"],
        warning=colors["warning"],
        warning_fg=colors["warning_fg"],
        success=colors["success"],
        success_fg=colors["success_fg"],
        dimension=colors["dimension"],
        dimension_fg=colors["dimension_fg"],
        measure=colors["measure"],
        measure_fg=colors["measure_fg"],
        logo=logo,
        favicon=favicon,
    )


def _load_optional_brand_asset(
    raw: object, *, path: str, base_dir: Path | None = None,
) -> str | None:
    """Validate the YAML value for ``theme.logo`` / ``theme.favicon``.

    Accepts either:
    - A URL (``http://``, ``https://``, or protocol-relative ``//``)
    - An absolute file path (must start with ``/``)
    - A path relative to the L2 YAML file's directory (``logo.png``,
      ``./img/mark.svg``, ``../branding/favicon.ico``) — resolved to
      an absolute path against ``base_dir``. Falls through as a bare
      string when ``base_dir`` is ``None`` (defensive, only happens
      if a caller bypasses ``load_instance``).

    ``None`` / missing → no override; the docs site falls back to
    whatever ``mkdocs.yml`` ships with.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise L2LoaderError(
            f"{path}: must be a string (URL or file path), "
            f"got {type(raw).__name__}"
        )
    value = raw.strip()
    if not value:
        return None
    if value.startswith(("http://", "https://", "//")):
        return value
    if value.startswith("/"):
        return value
    # Relative path — resolve against the L2 YAML's parent directory.
    if base_dir is None:
        raise L2LoaderError(
            f"{path}: relative path {value!r} cannot be resolved — "
            f"caller did not provide base_dir. Pass an absolute path "
            f"or load via ``load_instance(yaml_path)``."
        )
    return str((base_dir / value).resolve())


def _load_persona(raw: object, *, path: str) -> DemoPersona | None:
    """Optional ``persona:`` block — institution flavor strings for handbook.

    YAML shape mirrors the ``DemoPersona`` dataclass:

    .. code-block:: yaml

        persona:
          institution: ["Sasquatch National Bank", "SNB"]
          stakeholders: ["Federal Reserve Bank", "Fed", "..."]
          gl_accounts:
            - {code: "gl-1010", name: "Cash & Due From FRB", note: "..."}
          merchants: ["Big Meadow Dairy", "Bigfoot Brews", "..."]
          flavor: ["Margaret Hollowcreek", "Pacific Northwest", "..."]

    Each top-level key is optional and defaults to an empty tuple.
    Returns ``None`` when the entire ``persona:`` block is absent —
    handbook templates render neutral prose in that case.
    """
    if raw is None:
        return None
    raw_d = _as_mapping(raw, path=path, what="persona")

    def _str_tuple(key: str) -> tuple[str, ...]:
        sub = raw_d.get(key)
        if sub is None:
            return ()
        items = _as_list(sub, path=f"{path}.{key}")
        out: list[str] = []
        for i, item in enumerate(items):
            if not isinstance(item, str):
                raise L2LoaderError(
                    f"{path}.{key}[{i}]: must be a string, "
                    f"got {type(item).__name__}"
                )
            out.append(item)
        return tuple(out)

    institution = _str_tuple("institution")
    stakeholders = _str_tuple("stakeholders")
    merchants = _str_tuple("merchants")
    flavor = _str_tuple("flavor")

    gl_accounts_raw = raw_d.get("gl_accounts")
    gl_accounts: tuple[GLAccount, ...]
    if gl_accounts_raw is None:
        gl_accounts = ()
    else:
        items = _as_list(gl_accounts_raw, path=f"{path}.gl_accounts")
        out_gl: list[GLAccount] = []
        for i, item in enumerate(items):
            sub_path = f"{path}.gl_accounts[{i}]"
            sub_d = _as_mapping(item, path=sub_path, what="gl_account")
            code_raw = _require(sub_d, "code", path=sub_path)
            name_raw = _require(sub_d, "name", path=sub_path)
            note_raw = sub_d.get("note", "")
            code = _load_string(code_raw, path=f"{sub_path}.code")
            name = _load_string(name_raw, path=f"{sub_path}.name")
            note = (
                _load_string(note_raw, path=f"{sub_path}.note")
                if note_raw != "" else ""
            )
            out_gl.append(GLAccount(code=code, name=name, note=note))
        gl_accounts = tuple(out_gl)

    return DemoPersona(
        institution=institution,
        stakeholders=stakeholders,
        gl_accounts=gl_accounts,
        merchants=merchants,
        flavor=flavor,
    )


def _load_description(raw: object, *, path: str) -> str | None:
    """Parse an optional description field per SPEC's "Description fields".

    Free-form prose; library does no pre-processing on the value. Type
    must be string when present (a YAML mapping or list under
    ``description:`` is almost certainly a key collision and worth
    erroring on). Empty string is rejected — if you mean "no description"
    omit the key entirely.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise L2LoaderError(
            f"{path}: description must be a string, "
            f"got {type(raw).__name__}"
        )
    if not raw.strip():
        raise L2LoaderError(
            f"{path}: description is empty; omit the key instead of "
            f"declaring it blank"
        )
    return raw


def _load_identifier_list(
    raw: object, *, path: str, allow_empty: bool = True,
) -> tuple[Identifier, ...]:
    """A YAML list of identifier strings → tuple of Identifiers."""
    if raw is None and allow_empty:
        return ()
    if not isinstance(raw, list):
        raise L2LoaderError(
            f"{path}: expected a list of identifiers, "
            f"got {type(raw).__name__}"
        )
    items = cast("list[object]", raw)
    return tuple(
        _load_identifier(item, path=f"{path}[{i}]")
        for i, item in enumerate(items)
    )


def _load_role_expression(raw: object, *, path: str) -> RoleExpression:
    """Single role string → 1-tuple; YAML list of role strings → tuple.

    Per the primitives module's normalization choice — RoleExpression is
    always a tuple, single-role becomes a 1-tuple. Avoids the union-vs-
    string hazard everywhere downstream.
    """
    if isinstance(raw, str):
        return (_load_identifier(raw, path=path),)
    if isinstance(raw, list):
        items = cast("list[object]", raw)
        if not items:
            raise L2LoaderError(
                f"{path}: role expression list must not be empty"
            )
        return tuple(
            _load_identifier(item, path=f"{path}[{i}]")
            for i, item in enumerate(items)
        )
    raise L2LoaderError(
        f"{path}: role expression must be a string or list of strings, "
        f"got {type(raw).__name__}"
    )


# -- Per-primitive loaders ---------------------------------------------------


def _load_account(raw: object, *, path: str) -> Account:
    raw_d = _as_mapping(raw, path=path, what="account")
    eod = raw_d.get("expected_eod_balance")
    return Account(
        id=_load_identifier(_require(raw_d, "id", path=path), path=f"{path}.id"),
        scope=_load_scope(_require(raw_d, "scope", path=path), path=f"{path}.scope"),
        name=Name(_load_string(raw_d["name"], path=f"{path}.name"))
        if "name" in raw_d else None,
        role=_load_identifier(raw_d["role"], path=f"{path}.role")
        if "role" in raw_d else None,
        parent_role=_load_identifier(raw_d["parent_role"], path=f"{path}.parent_role")
        if "parent_role" in raw_d else None,
        expected_eod_balance=_load_money(eod, path=f"{path}.expected_eod_balance")
        if eod is not None else None,
        description=_load_description(
            raw_d.get("description"), path=f"{path}.description",
        ),
    )


def _load_account_template(raw: object, *, path: str) -> AccountTemplate:
    raw_d = _as_mapping(raw, path=path, what="account_template")
    eod = raw_d.get("expected_eod_balance")
    return AccountTemplate(
        role=_load_identifier(_require(raw_d, "role", path=path), path=f"{path}.role"),
        scope=_load_scope(_require(raw_d, "scope", path=path), path=f"{path}.scope"),
        parent_role=_load_identifier(raw_d["parent_role"], path=f"{path}.parent_role")
        if "parent_role" in raw_d else None,
        expected_eod_balance=_load_money(eod, path=f"{path}.expected_eod_balance")
        if eod is not None else None,
        description=_load_description(
            raw_d.get("description"), path=f"{path}.description",
        ),
        instance_id_template=_load_instance_template(
            raw_d.get("instance_id_template"),
            path=f"{path}.instance_id_template",
        ),
        instance_name_template=_load_instance_template(
            raw_d.get("instance_name_template"),
            path=f"{path}.instance_name_template",
        ),
    )


def _load_instance_template(raw: object | None, *, path: str) -> str | None:
    """M.4.2b: parse + validate an AccountTemplate instance display template.

    Returns the format string if set; ``None`` if the field is absent
    (the seed falls back to the legacy synthetic pattern). Validates
    that the format string only references the supported placeholders
    ``{role}`` and ``{n}`` — any other placeholder is a hard load error
    so an integrator's typo doesn't silently produce a broken seed.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise L2LoaderError(
            f"{path}: instance template must be a string, got {type(raw).__name__}"
        )
    # str.Formatter().parse() yields (literal_text, field_name, format_spec,
    # conversion) per parsed segment; field_name is None on plain text and
    # the placeholder name on `{name}` substitutions.
    import string
    valid_placeholders = {"role", "n"}
    for _literal, field_name, _format_spec, _conversion in (
        string.Formatter().parse(raw)
    ):
        if field_name is None:
            continue
        if field_name not in valid_placeholders:
            raise L2LoaderError(
                f"{path}={raw!r}: unknown placeholder {field_name!r}; only "
                f"{sorted(valid_placeholders)!r} are supported"
            )
    return raw


def _load_rail(raw: object, *, path: str) -> Rail:
    """Discriminate two-leg vs single-leg by which keys are present.

    Per M.1a SPEC catch-up: ``origin`` is optional at the rail level
    (per-leg ``source_origin`` / ``destination_origin`` may cover both
    legs on a two-leg rail; the validator's O1 rule checks resolution).
    Per-leg overrides on a single-leg rail are a hard load-time error.
    """
    raw_d = _as_mapping(raw, path=path, what="rail")

    name = _load_identifier(_require(raw_d, "name", path=path), path=f"{path}.name")
    transfer_type: TransferType = _load_string(
        _require(raw_d, "transfer_type", path=path),
        path=f"{path}.transfer_type",
    )
    origin_raw = raw_d.get("origin")
    origin: Origin | None = (
        _load_string(origin_raw, path=f"{path}.origin")
        if origin_raw is not None else None
    )
    metadata_keys = _load_identifier_list(
        raw_d.get("metadata_keys"), path=f"{path}.metadata_keys",
    )

    # PostedRequirements (integrator-declared); see derived.py for the
    # full computed set that unions in TransferKey + chain-required.
    posted_requirements = _load_identifier_list(
        raw_d.get("posted_requirements"),
        path=f"{path}.posted_requirements",
    )

    # Aging watches.
    mpa_raw = raw_d.get("max_pending_age")
    max_pending_age: Duration | None = (
        _load_duration(mpa_raw, path=f"{path}.max_pending_age")
        if mpa_raw is not None else None
    )
    mua_raw = raw_d.get("max_unbundled_age")
    max_unbundled_age: Duration | None = (
        _load_duration(mua_raw, path=f"{path}.max_unbundled_age")
        if mua_raw is not None else None
    )

    # Aggregating flags can appear on either shape.
    aggregating: bool = bool(raw_d.get("aggregating", False))
    bundles_activity = tuple(
        BundlesActivityRef(_load_identifier(item, path=f"{path}.bundles_activity[{i}]"))
        for i, item in enumerate(
            _as_list(raw_d.get("bundles_activity"), path=f"{path}.bundles_activity")
        )
    )
    cadence_raw = raw_d.get("cadence")
    cadence: CadenceExpression | None = (
        _load_string(cadence_raw, path=f"{path}.cadence")
        if cadence_raw is not None else None
    )

    description = _load_description(
        raw_d.get("description"), path=f"{path}.description",
    )

    # M.4.2b: per-key metadata value examples. Loader normalizes the
    # YAML mapping into a tuple-of-tuples (frozen-dataclass-friendly).
    # Validator R13 checks every key exists in metadata_keys.
    metadata_value_examples = _load_metadata_value_examples(
        raw_d.get("metadata_value_examples"),
        path=f"{path}.metadata_value_examples",
    )

    has_two_leg_fields = "source_role" in raw_d or "destination_role" in raw_d
    has_single_leg_fields = "leg_role" in raw_d or "leg_direction" in raw_d

    if has_two_leg_fields and has_single_leg_fields:
        raise L2LoaderError(
            f"{path}: rail must declare EITHER two-leg "
            f"(source_role + destination_role) OR single-leg "
            f"(leg_role + leg_direction), not both"
        )
    if not has_two_leg_fields and not has_single_leg_fields:
        raise L2LoaderError(
            f"{path}: rail must declare EITHER two-leg "
            f"(source_role + destination_role) OR single-leg "
            f"(leg_role + leg_direction)"
        )

    if has_two_leg_fields:
        if "source_role" not in raw_d or "destination_role" not in raw_d:
            raise L2LoaderError(
                f"{path}: two-leg rail requires both source_role and "
                f"destination_role"
            )
        en = raw_d.get("expected_net")
        # Per-leg Origin overrides. Loader pulls them; validator's O1
        # rule checks every leg resolves under the SPEC's resolution table.
        so_raw = raw_d.get("source_origin")
        source_origin: Origin | None = (
            _load_string(so_raw, path=f"{path}.source_origin")
            if so_raw is not None else None
        )
        do_raw = raw_d.get("destination_origin")
        destination_origin: Origin | None = (
            _load_string(do_raw, path=f"{path}.destination_origin")
            if do_raw is not None else None
        )
        return TwoLegRail(
            name=name,
            transfer_type=transfer_type,
            metadata_keys=metadata_keys,
            source_role=_load_role_expression(
                raw_d["source_role"], path=f"{path}.source_role",
            ),
            destination_role=_load_role_expression(
                raw_d["destination_role"], path=f"{path}.destination_role",
            ),
            origin=origin,
            source_origin=source_origin,
            destination_origin=destination_origin,
            expected_net=_load_money(en, path=f"{path}.expected_net")
            if en is not None else None,
            posted_requirements=posted_requirements,
            max_pending_age=max_pending_age,
            max_unbundled_age=max_unbundled_age,
            aggregating=aggregating,
            bundles_activity=bundles_activity,
            cadence=cadence,
            description=description,
            metadata_value_examples=metadata_value_examples,
        )

    # Single-leg
    if "leg_role" not in raw_d or "leg_direction" not in raw_d:
        raise L2LoaderError(
            f"{path}: single-leg rail requires both leg_role and leg_direction"
        )
    # Per-leg Origin overrides only make sense on a 2-leg rail. Hard
    # error per the M.1a design call (no warning channel today).
    for forbidden in ("source_origin", "destination_origin"):
        if forbidden in raw_d:
            raise L2LoaderError(
                f"{path}.{forbidden}: per-leg Origin overrides are only "
                f"valid on two-leg rails; remove this field or restructure "
                f"the rail as two-leg"
            )
    return SingleLegRail(
        name=name,
        transfer_type=transfer_type,
        metadata_keys=metadata_keys,
        leg_role=_load_role_expression(
            raw_d["leg_role"], path=f"{path}.leg_role",
        ),
        leg_direction=_load_leg_direction(
            raw_d["leg_direction"], path=f"{path}.leg_direction",
        ),
        origin=origin,
        posted_requirements=posted_requirements,
        max_pending_age=max_pending_age,
        max_unbundled_age=max_unbundled_age,
        aggregating=aggregating,
        bundles_activity=bundles_activity,
        cadence=cadence,
        description=description,
        metadata_value_examples=metadata_value_examples,
    )


def _load_metadata_value_examples(
    raw: object | None, *, path: str,
) -> tuple[tuple[Identifier, tuple[str, ...]], ...]:
    """M.4.2b: parse per-key metadata value example lists.

    Expected YAML shape:
        metadata_value_examples:
          merchant_id: ["m-001", "m-002", "m-003"]
          settlement_period: ["2026-04", "2026-05"]

    Returns ``()`` when absent. Validates that every value-list is a
    list of strings (not arbitrary Python objects). Cross-key validation
    (every key must be in ``metadata_keys``) is the validator's R13
    job — it has the rail context this loader doesn't.
    """
    if raw is None:
        return ()
    raw_d = _as_mapping(raw, path=path, what="metadata_value_examples")
    items: list[tuple[Identifier, tuple[str, ...]]] = []
    for raw_key, raw_values in raw_d.items():
        key: str = str(raw_key)
        # _as_list narrows from `object` and rejects non-list inputs
        # with a typed error message — same shape used elsewhere in
        # this loader for safe_load Any cascade narrowing.
        values_list = _as_list(raw_values, path=f"{path}.{key}")
        if not values_list:
            raise L2LoaderError(
                f"{path}.{key}: example list must be non-empty"
            )
        coerced: list[str] = []
        for i, v in enumerate(values_list):
            if not isinstance(v, str):
                raise L2LoaderError(
                    f"{path}.{key}[{i}]: example values must be strings, "
                    f"got {type(v).__name__}"
                )
            coerced.append(v)
        items.append((Identifier(key), tuple(coerced)))
    # Sorted by key for deterministic dataclass equality.
    items.sort(key=lambda kv: str(kv[0]))
    return tuple(items)


def _load_transfer_template(raw: object, *, path: str) -> TransferTemplate:
    raw_d = _as_mapping(raw, path=path, what="transfer_template")
    completion: CompletionExpression = _load_string(
        _require(raw_d, "completion", path=path), path=f"{path}.completion",
    )
    return TransferTemplate(
        name=_load_identifier(
            _require(raw_d, "name", path=path), path=f"{path}.name",
        ),
        transfer_type=_load_string(
            _require(raw_d, "transfer_type", path=path),
            path=f"{path}.transfer_type",
        ),
        expected_net=_load_money(
            _require(raw_d, "expected_net", path=path),
            path=f"{path}.expected_net",
        ),
        transfer_key=_load_identifier_list(
            _require(raw_d, "transfer_key", path=path),
            path=f"{path}.transfer_key",
            allow_empty=False,
        ),
        completion=completion,
        leg_rails=_load_identifier_list(
            _require(raw_d, "leg_rails", path=path),
            path=f"{path}.leg_rails",
            allow_empty=False,
        ),
        description=_load_description(
            raw_d.get("description"), path=f"{path}.description",
        ),
    )


def _load_chain_entry(raw: object, *, path: str) -> ChainEntry:
    raw_d = _as_mapping(raw, path=path, what="chain entry")
    return ChainEntry(
        parent=_load_identifier(
            _require(raw_d, "parent", path=path), path=f"{path}.parent",
        ),
        child=_load_identifier(
            _require(raw_d, "child", path=path), path=f"{path}.child",
        ),
        required=bool(_require(raw_d, "required", path=path)),
        xor_group=_load_identifier(raw_d["xor_group"], path=f"{path}.xor_group")
        if "xor_group" in raw_d else None,
        description=_load_description(
            raw_d.get("description"), path=f"{path}.description",
        ),
    )


def _load_limit_schedule(raw: object, *, path: str) -> LimitSchedule:
    raw_d = _as_mapping(raw, path=path, what="limit_schedule")
    return LimitSchedule(
        parent_role=_load_identifier(
            _require(raw_d, "parent_role", path=path),
            path=f"{path}.parent_role",
        ),
        transfer_type=_load_string(
            _require(raw_d, "transfer_type", path=path),
            path=f"{path}.transfer_type",
        ),
        cap=_load_money(_require(raw_d, "cap", path=path), path=f"{path}.cap"),
        description=_load_description(
            raw_d.get("description"), path=f"{path}.description",
        ),
    )


# -- Public API --------------------------------------------------------------


def _capture_to_run_dir(raw_text: str, instance_prefix: str) -> None:
    """Y.2.gate.c.12 — copy every loaded L2 YAML into ``$QS_GEN_RUN_DIR/l2/``.

    No-op when ``QS_GEN_RUN_DIR`` is unset (direct ``pytest`` /
    ``quicksight-gen`` invocations are unchanged). When set, writes
    the raw YAML bytes to ``<run-dir>/l2/<instance-prefix>.yaml`` so
    the runner's per-run snapshot captures every L2 the test session
    touched — fuzzed AND static (the fuzzer constructs a YAML on disk
    and feeds the path through ``load_instance``, same code path).

    Idempotent: same prefix loaded twice in one session writes the
    same bytes to the same file (cheap overwrite). Capture failures
    (disk full, permission denied, registry validator rejecting a
    bad path) are swallowed — the YAML load must never fail because
    the sidecar can't write.
    """
    # Sidecar contract — swallow EnvVarInvalid too: a misconfigured
    # registry value (must_be_dir failing because env points at a
    # regular file) must not break ``load_instance``.
    from quicksight_gen.common.env_keys import EnvVarInvalid
    try:
        run_dir = QS_GEN_RUN_DIR.get_or_none()
    except EnvVarInvalid:
        return
    if run_dir is None:
        return
    try:
        target_dir = run_dir / "l2"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{instance_prefix}.yaml"
        target.write_text(raw_text)
    except OSError:
        # Sidecar — never break the load.
        pass


def load_instance(path: Path | str, *, validate: bool = True) -> L2Instance:
    """Load + validate an L2 YAML file into an ``L2Instance``.

    By default (``validate=True``), runs the full cross-entity
    validation pass (``common.l2.validate.validate``) before returning,
    so a malformed instance fails at YAML load time rather than at
    first render. This is the M.2d.2 contract — every cross-entity
    SHOULD-constraint listed in the SPEC's Validation Rules section
    is a parse-time error by default.

    Pass ``validate=False`` to skip the cross-entity pass — useful for
    narrow loader tests that intentionally exercise partial fixtures
    without satisfying every reference-resolution rule.

    Loader-side errors (malformed YAML, missing required fields, type
    shapes, identifier format, Money coercion) raise ``L2LoaderError``.
    Validator-side errors (reference resolution, uniqueness,
    cardinality, vocabulary, Origin resolution) raise
    ``L2ValidationError``.

    Y.2.gate.c.12 — when ``QS_GEN_RUN_DIR`` is set, the raw YAML is
    captured to ``<run-dir>/l2/<instance-prefix>.yaml`` for per-run
    debugging. See ``_capture_to_run_dir``.
    """
    yaml_path = Path(path)
    try:
        raw_text = yaml_path.read_text()
    except OSError as exc:
        raise L2LoaderError(f"could not read {yaml_path}: {exc}") from exc

    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise L2LoaderError(
            f"YAML syntax error in {yaml_path}: {exc}"
        ) from exc

    if raw is None:
        raise L2LoaderError(f"{yaml_path}: file is empty")
    raw_d = _as_mapping(raw, path=str(yaml_path), what="top-level")

    instance = _load_instance_prefix(
        _require(raw_d, "instance", path="instance"), path="instance",
    )
    _capture_to_run_dir(raw_text, str(instance))

    accounts = tuple(
        _load_account(item, path=f"accounts[{i}]")
        for i, item in enumerate(_as_list(raw_d.get("accounts"), path="accounts"))
    )
    account_templates = tuple(
        _load_account_template(item, path=f"account_templates[{i}]")
        for i, item in enumerate(
            _as_list(raw_d.get("account_templates"), path="account_templates")
        )
    )
    rails = tuple(
        _load_rail(item, path=f"rails[{i}]")
        for i, item in enumerate(_as_list(raw_d.get("rails"), path="rails"))
    )
    transfer_templates = tuple(
        _load_transfer_template(item, path=f"transfer_templates[{i}]")
        for i, item in enumerate(
            _as_list(raw_d.get("transfer_templates"), path="transfer_templates")
        )
    )
    chains = tuple(
        _load_chain_entry(item, path=f"chains[{i}]")
        for i, item in enumerate(_as_list(raw_d.get("chains"), path="chains"))
    )
    limit_schedules = tuple(
        _load_limit_schedule(item, path=f"limit_schedules[{i}]")
        for i, item in enumerate(
            _as_list(raw_d.get("limit_schedules"), path="limit_schedules")
        )
    )

    inst = L2Instance(
        instance=instance,
        accounts=accounts,
        account_templates=account_templates,
        rails=rails,
        transfer_templates=transfer_templates,
        chains=chains,
        limit_schedules=limit_schedules,
        description=_load_description(
            raw_d.get("description"), path="description",
        ),
        role_business_day_offsets=_load_role_business_day_offsets(
            raw_d.get("role_business_day_offsets"),
            path="role_business_day_offsets",
        ),
        theme=_load_theme(
            raw_d.get("theme"), path="theme", base_dir=yaml_path.parent,
        ),
        persona=_load_persona(raw_d.get("persona"), path="persona"),
    )
    if validate:
        # Local import dodges loader↔validate import-cycle.
        from .validate import validate as _validate
        _validate(inst)
    return inst
