"""Studio session-state sidefile (X.4.h.7).

Trainer-mode UI mutations (data-shaping panel knobs + scenario window +
etl_hook toggle) persist to a sibling ``.studio-state.yaml`` next to
``cfg.yaml`` so they survive Studio restarts. The main ``cfg.yaml``
stays sacred — operator-authored, comments preserved, never
re-formatted by Studio.

**Why a sidefile, not in-place rewrite of cfg.yaml.** Operator-authored
``cfg.yaml`` carries structured ``# comments`` per section that explain
each field's purpose to humans. Studio's ``serialize_l2`` /
``serialize_cfg`` round-trip would obliterate every comment on first
knob mutation (per the SPEC's "freeform comments dropped on serialize"
contract). The sidefile pattern keeps that contract intact for cfg.yaml
while still giving Studio a place to persist its session state.

**Sidefile contract:**

- Path: ``<cfg.parent>/.studio-state.yaml`` (sibling to cfg.yaml).
  Leading dot signals "internal / not source-controlled" and matches
  conventional dotfile gitignore patterns.
- Holds the trainer-mutable subset of ``TestGeneratorConfig`` fields
  (``scope`` / ``end_date`` / ``seed`` / ``plants`` / ``only_template``
  / ``derive_balances``) plus the trainer's scenario ``window`` (start
  + end) and the ``etl_hook.enabled`` toggle.
- Missing file ⇒ "use ``cfg.test_generator`` defaults" (no error,
  silent first-run).
- Malformed file ⇒ same fallback, with a warning to stderr — operator
  can delete the file to reset.
- Atomic write via ``save_yaml_atomic`` (same primitive ``L2InstanceCache``
  uses), so a crash mid-write leaves the prior state intact.

**No debouncing in v1.** Save fires synchronously per mutation. The
file is small (~10 lines), the atomic-write is sub-millisecond, and
trainer controls are clicks (not continuous slider drags), so there's
no hot-loop pressure to coalesce. Add debouncing if the trainer ever
gains a continuous-input control.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast, get_args

import yaml

from quicksight_gen.common.config import (
    PlantKind,
    ScopeKind,
    TestGeneratorConfig,
)
from quicksight_gen.common.l2.cache import save_yaml_atomic


__all__ = [
    "SIDEFILE_NAME",
    "StudioState",
    "load_studio_state",
    "save_studio_state",
    "sidefile_path_for",
]


SIDEFILE_NAME = ".studio-state.yaml"


@dataclass(frozen=True)
class StudioState:
    """Trainer-mode persistent state. Inverse of the ``TestGeneratorCache``
    mutable surface.

    All fields default to None / sentinel so a brand-new sidefile (or
    one written by an older Studio version that didn't know about a
    field) loads cleanly — missing keys mean "use cfg defaults."
    """

    # TestGeneratorConfig fields the trainer mutates. All Optional —
    # None means "the sidefile didn't override; cfg.test_generator's
    # value wins."
    scope: ScopeKind | None = None
    end_date: date | None = None
    seed: int | None = None
    # Tuple is the field-level marker; () means "trainer set 'all kinds'"
    # which is meaningfully distinct from None ("never touched").
    plants: tuple[PlantKind, ...] | None = None
    only_template: str | None = None
    derive_balances: bool | None = None

    # Scenario window — Studio-only concept (not in TestGeneratorConfig).
    # Both must be set together or both None.
    window_start: date | None = None
    window_end: date | None = None

    # X.4.h.etl-toggle — etl_hook + etl_datasource pair enable/disable.
    # None = "trainer never touched, use cfg-as-written behavior."
    etl_hook_enabled: bool | None = None


def sidefile_path_for(cfg_path: Path | str) -> Path:
    """Resolve the sidefile path from the cfg.yaml path."""
    return Path(cfg_path).parent / SIDEFILE_NAME


def load_studio_state(path: Path | str) -> StudioState | None:
    """Load the sidefile from disk; None if missing or malformed.

    Malformed = YAML parse error OR top-level isn't a dict. Logs a
    warning to stderr in that case so the operator notices that
    their session state didn't survive (vs. silently falling back).
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        text = p.read_text()
        raw = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as exc:
        print(
            f"warning: studio sidefile {p} unreadable ({type(exc).__name__}: "
            f"{exc}); falling back to cfg.test_generator defaults",
            file=sys.stderr,
        )
        return None
    if raw is None:
        return StudioState()
    if not isinstance(raw, dict):
        print(
            f"warning: studio sidefile {p} top-level is {type(raw).__name__} "
            f"(expected dict); falling back to cfg.test_generator defaults",
            file=sys.stderr,
        )
        return None
    return _from_yaml_dict(cast(dict[str, Any], raw))


def save_studio_state(state: StudioState, path: Path | str) -> None:
    """Atomically write the sidefile."""
    p = Path(path)
    text = _to_yaml_text(state)
    save_yaml_atomic(text, p)


# ----- yaml encoders / decoders -----


def _from_yaml_dict(raw: dict[str, Any]) -> StudioState:  # typing-smell: ignore[explicit-any]: parsed-yaml shape is heterogeneous; per-key narrowing happens in the body
    tgen = raw.get("test_generator")
    tgen_dict: dict[str, Any] = (  # typing-smell: ignore[explicit-any]: same as outer — parsed yaml
        cast(dict[str, Any], tgen) if isinstance(tgen, dict) else {}
    )

    scope_raw = tgen_dict.get("scope")
    scope: ScopeKind | None = None
    if isinstance(scope_raw, str) and scope_raw in get_args(ScopeKind):
        scope = cast(ScopeKind, scope_raw)

    end_date_raw = tgen_dict.get("end_date")
    end_date_v: date | None = (
        end_date_raw if isinstance(end_date_raw, date) else None
    )
    if isinstance(end_date_raw, str):
        try:
            end_date_v = date.fromisoformat(end_date_raw)
        except ValueError:
            end_date_v = None

    seed_raw = tgen_dict.get("seed")
    seed = seed_raw if isinstance(seed_raw, int) else None

    plants_raw = tgen_dict.get("plants")
    plants: tuple[PlantKind, ...] | None = None
    if isinstance(plants_raw, list):
        known: set[str] = set(get_args(PlantKind))
        picked: list[PlantKind] = []
        plants_list = cast(list[Any], plants_raw)  # typing-smell: ignore[explicit-any]: yaml list-items are heterogeneous; per-item isinstance narrows
        for p in plants_list:
            if isinstance(p, str) and p in known:
                picked.append(cast(PlantKind, p))
        plants = tuple(picked)  # may be () — that's a meaningful "all kinds" override

    only_template_raw = tgen_dict.get("only_template")
    only_template = (
        only_template_raw if isinstance(only_template_raw, str) else None
    )

    derive_raw = tgen_dict.get("derive_balances")
    derive_balances = derive_raw if isinstance(derive_raw, bool) else None

    window = raw.get("trainer_window")
    window_start: date | None = None
    window_end: date | None = None
    if isinstance(window, dict):
        window_dict = cast(dict[str, Any], window)  # typing-smell: ignore[explicit-any]: parsed yaml dict
        ws_raw = window_dict.get("start")
        we_raw = window_dict.get("end")
        if isinstance(ws_raw, date):
            window_start = ws_raw
        elif isinstance(ws_raw, str):
            try:
                window_start = date.fromisoformat(ws_raw)
            except ValueError:
                pass
        if isinstance(we_raw, date):
            window_end = we_raw
        elif isinstance(we_raw, str):
            try:
                window_end = date.fromisoformat(we_raw)
            except ValueError:
                pass

    etl = raw.get("etl_hook")
    etl_hook_enabled: bool | None = None
    if isinstance(etl, dict):
        etl_dict = cast(dict[str, Any], etl)  # typing-smell: ignore[explicit-any]: parsed yaml dict
        en_raw = etl_dict.get("enabled")
        if isinstance(en_raw, bool):
            etl_hook_enabled = en_raw

    return StudioState(
        scope=scope,
        end_date=end_date_v,
        seed=seed,
        plants=plants,
        only_template=only_template,
        derive_balances=derive_balances,
        window_start=window_start,
        window_end=window_end,
        etl_hook_enabled=etl_hook_enabled,
    )


def _to_yaml_text(state: StudioState) -> str:
    """Emit the YAML payload. Compact (skip None-valued fields)."""
    tgen: dict[str, Any] = {}  # typing-smell: ignore[explicit-any]: yaml-bound dict; values are heterogeneous primitives
    if state.scope is not None:
        tgen["scope"] = state.scope
    if state.end_date is not None:
        tgen["end_date"] = state.end_date.isoformat()
    if state.seed is not None:
        tgen["seed"] = state.seed
    if state.plants is not None:
        # Empty list is meaningful (= "all kinds"); preserve it.
        tgen["plants"] = list(state.plants)
    if state.only_template is not None:
        tgen["only_template"] = state.only_template
    if state.derive_balances is not None:
        tgen["derive_balances"] = state.derive_balances

    payload: dict[str, Any] = {}  # typing-smell: ignore[explicit-any]: yaml-bound dict; values are heterogeneous primitives
    if tgen:
        payload["test_generator"] = tgen
    if state.window_start is not None and state.window_end is not None:
        payload["trainer_window"] = {
            "start": state.window_start.isoformat(),
            "end": state.window_end.isoformat(),
        }
    if state.etl_hook_enabled is not None:
        payload["etl_hook"] = {"enabled": state.etl_hook_enabled}

    header = (
        "# Studio session state — trainer-mode UI knobs.\n"
        "# Auto-generated by Studio's data-shaping panel; safe to delete\n"
        "# to reset all trainer state to cfg.yaml defaults.\n"
        "# .gitignored — not source-controlled, ephemeral session state.\n"
    )
    body = yaml.safe_dump(
        payload, default_flow_style=False, sort_keys=False,
    ) if payload else "{}\n"
    return header + body


def merge_into_test_generator(
    cfg_tgen: TestGeneratorConfig,
    state: StudioState | None,
) -> TestGeneratorConfig:
    """Apply the sidefile's overrides on top of cfg.test_generator.

    Sidefile fields that are None mean "use the cfg value." Returns a
    new ``TestGeneratorConfig`` with the merged result. Used by
    ``TestGeneratorCache.from_cfg_with_state`` to build the cache's
    initial state.
    """
    if state is None:
        return cfg_tgen
    return TestGeneratorConfig(
        enabled=cfg_tgen.enabled,
        scope=state.scope if state.scope is not None else cfg_tgen.scope,
        end_date=(
            state.end_date if state.end_date is not None
            else cfg_tgen.end_date
        ),
        seed=state.seed if state.seed is not None else cfg_tgen.seed,
        plants=(
            state.plants if state.plants is not None else cfg_tgen.plants
        ),
        only_template=(
            state.only_template if state.only_template is not None
            else cfg_tgen.only_template
        ),
        derive_balances=(
            state.derive_balances if state.derive_balances is not None
            else cfg_tgen.derive_balances
        ),
        cutoff_date=cfg_tgen.cutoff_date,
    )
