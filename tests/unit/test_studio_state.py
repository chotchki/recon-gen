"""Studio sidefile (X.4.h.7) — load/save round-trip + edge cases."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from quicksight_gen.common.config import TestGeneratorConfig
from quicksight_gen.common.l2.studio_state import (
    SIDEFILE_NAME,
    StudioState,
    load_studio_state,
    merge_into_test_generator,
    save_studio_state,
    sidefile_path_for,
)


def test_sidefile_path_for_is_sibling(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    assert sidefile_path_for(cfg_path) == tmp_path / SIDEFILE_NAME


def test_load_missing_file_returns_none(tmp_path: Path) -> None:
    """First-run on a fresh repo — sidefile doesn't exist yet."""
    p = tmp_path / SIDEFILE_NAME
    assert load_studio_state(p) is None


def test_round_trip_full_state(tmp_path: Path) -> None:
    """Every persisted field round-trips — load(save(x)) == x."""
    p = tmp_path / SIDEFILE_NAME
    state = StudioState(
        scope="exceptions_only",
        end_date=date(2026, 5, 14),
        seed=12345,
        plants=("drift", "overdraft"),
        only_template="MerchantSettlementCycle",
        derive_balances=True,
        window_start=date(2026, 2, 14),
        window_end=date(2026, 5, 14),
        etl_hook_enabled=False,
    )
    save_studio_state(state, p)
    reloaded = load_studio_state(p)
    assert reloaded == state


def test_round_trip_empty_state(tmp_path: Path) -> None:
    """An empty state (all None) writes a stub file that reloads cleanly."""
    p = tmp_path / SIDEFILE_NAME
    state = StudioState()
    save_studio_state(state, p)
    reloaded = load_studio_state(p)
    assert reloaded == state


def test_empty_plants_tuple_preserved(tmp_path: Path) -> None:
    """`plants=()` means "all kinds" per the SPEC — distinct from None
    ("trainer never touched"). Round-trip must preserve."""
    p = tmp_path / SIDEFILE_NAME
    state = StudioState(plants=())
    save_studio_state(state, p)
    reloaded = load_studio_state(p)
    assert reloaded is not None
    assert reloaded.plants == ()


def test_load_malformed_yaml_returns_none(tmp_path: Path) -> None:
    p = tmp_path / SIDEFILE_NAME
    p.write_text("not: valid: yaml: [unclosed")
    assert load_studio_state(p) is None


def test_load_non_dict_top_level_returns_none(tmp_path: Path) -> None:
    """A list at top-level is malformed (we expect a dict)."""
    p = tmp_path / SIDEFILE_NAME
    p.write_text("- foo\n- bar\n")
    assert load_studio_state(p) is None


def test_load_empty_file_returns_default_state(tmp_path: Path) -> None:
    """An empty file is parseable (yaml.safe_load returns None) — treat
    as an empty state, not malformed."""
    p = tmp_path / SIDEFILE_NAME
    p.write_text("")
    assert load_studio_state(p) == StudioState()


def test_load_unknown_scope_silently_drops(tmp_path: Path) -> None:
    """Trash data in a single field doesn't kill the whole load — just
    that field defaults to None (= "use cfg value")."""
    p = tmp_path / SIDEFILE_NAME
    p.write_text("test_generator:\n  scope: garbage_value\n")
    state = load_studio_state(p)
    assert state is not None
    assert state.scope is None  # garbage rejected; cfg wins on merge


def test_load_iso_date_strings_parse(tmp_path: Path) -> None:
    """Dates land as ISO strings in the YAML; loader parses back to
    date objects."""
    p = tmp_path / SIDEFILE_NAME
    p.write_text(
        "test_generator:\n  end_date: '2026-05-14'\n"
        "trainer_window:\n  start: '2026-02-14'\n  end: '2026-05-14'\n",
    )
    state = load_studio_state(p)
    assert state is not None
    assert state.end_date == date(2026, 5, 14)
    assert state.window_start == date(2026, 2, 14)
    assert state.window_end == date(2026, 5, 14)


def test_load_invalid_iso_date_drops_silently(tmp_path: Path) -> None:
    p = tmp_path / SIDEFILE_NAME
    p.write_text("test_generator:\n  end_date: 'not-a-date'\n")
    state = load_studio_state(p)
    assert state is not None
    assert state.end_date is None


def test_save_atomic_via_save_yaml_atomic(tmp_path: Path) -> None:
    """No leftover .tmp file after save (the atomic primitive cleans up)."""
    p = tmp_path / SIDEFILE_NAME
    state = StudioState(scope="full", seed=42)
    save_studio_state(state, p)
    leftovers = [
        f for f in tmp_path.iterdir()
        if f.name.startswith(f".{SIDEFILE_NAME}.")
    ]
    assert leftovers == []


def test_yaml_emit_includes_header_comment(tmp_path: Path) -> None:
    """The auto-generated file carries a header comment so an operator
    eyeballing it knows it's safe to delete."""
    p = tmp_path / SIDEFILE_NAME
    save_studio_state(StudioState(scope="full"), p)
    text = p.read_text()
    assert text.startswith("#")
    assert "Studio session state" in text
    assert "safe to delete" in text


# ----- merge_into_test_generator -----


def test_merge_none_state_returns_cfg_unchanged() -> None:
    cfg_tgen = TestGeneratorConfig(scope="full", seed=42)
    assert merge_into_test_generator(cfg_tgen, None) is cfg_tgen


def test_merge_sidefile_overrides_cfg() -> None:
    """Sidefile field set ⇒ wins. Sidefile field None ⇒ cfg wins."""
    cfg_tgen = TestGeneratorConfig(
        scope="full",
        seed=42,
        plants=(),
        only_template=None,
        derive_balances=False,
    )
    sidefile = StudioState(
        scope="exceptions_only",
        # seed left None — cfg's 42 wins
        plants=("drift",),
        # only_template left None — cfg's None wins
        derive_balances=True,
    )
    merged = merge_into_test_generator(cfg_tgen, sidefile)
    assert merged.scope == "exceptions_only"
    assert merged.seed == 42  # cfg wins
    assert merged.plants == ("drift",)
    assert merged.only_template is None  # cfg wins
    assert merged.derive_balances is True


def test_merge_preserves_cutoff_date_from_cfg() -> None:
    """cutoff_date is Studio-only (no UI), comes from cfg (or None)."""
    cfg_tgen = TestGeneratorConfig(cutoff_date=date(2026, 5, 1))
    merged = merge_into_test_generator(
        cfg_tgen, StudioState(scope="full"),
    )
    assert merged.cutoff_date == date(2026, 5, 1)


# ----- StudioState struct -----


def test_studio_state_default_factory_all_none() -> None:
    state = StudioState()
    assert state.scope is None
    assert state.end_date is None
    assert state.seed is None
    assert state.plants is None
    assert state.only_template is None
    assert state.derive_balances is None
    assert state.window_start is None
    assert state.window_end is None
    assert state.etl_hook_enabled is None


def test_studio_state_frozen() -> None:
    """Frozen dataclass — mutation raises FrozenInstanceError."""
    import dataclasses
    state = StudioState()
    try:
        state.scope = "full"  # type: ignore[misc]: intentionally testing the runtime FrozenInstanceError that pyright won't allow at compile time
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("StudioState should be frozen")


# ----- Suppress unused-import warnings on test-only imports -----
_ = Any  # silence
