"""X.4.a.6 — ``L2InstanceCache`` + ``save_yaml_atomic`` unit tests.

Locks the Studio source-of-truth contract:

- ``L2InstanceCache.from_path`` loads + caches an L2Instance.
- ``get()`` returns the cached value identity-stable (no per-call re-parse).
- ``replace(new)`` swaps the cached instance without touching disk.
- ``save_yaml_atomic`` writes through a same-dir tempfile + rename so a
  partial write never lands on the target path.

The cache's eventual ``save()`` method (compose ``serialize_l2`` +
``save_yaml_atomic`` + ``replace``) lands in X.4.d.3 with the serializer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quicksight_gen.common.l2.cache import (
    L2InstanceCache,
    save_yaml_atomic,
)


_FIXTURES = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE = _FIXTURES / "spec_example.yaml"


# -- save_yaml_atomic --------------------------------------------------------


def test_save_yaml_atomic_writes_text(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    save_yaml_atomic("hello: world\n", target)
    assert target.read_text() == "hello: world\n"


def test_save_yaml_atomic_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("old\n")
    save_yaml_atomic("new\n", target)
    assert target.read_text() == "new\n"


def test_save_yaml_atomic_does_not_leave_tempfiles(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    save_yaml_atomic("a: 1\n", target)
    leftover = [p.name for p in tmp_path.iterdir() if p.name != "config.yaml"]
    assert leftover == [], (
        f"save_yaml_atomic left stray files: {leftover!r}"
    )


def test_save_yaml_atomic_cleans_up_temp_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the rename raises, the temp file must be cleaned up."""
    target = tmp_path / "config.yaml"

    real_replace = Path.replace

    def boom(self: Path, _other: Path | str) -> Path:  # type: ignore[no-untyped-def]: pytest stub matches Path.replace contract
        raise OSError("simulated rename failure")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="simulated"):
        save_yaml_atomic("x\n", target)
    monkeypatch.setattr(Path, "replace", real_replace)
    leftover = list(tmp_path.iterdir())
    assert leftover == [], (
        f"failed save left stray files: {[p.name for p in leftover]!r}"
    )


# -- L2InstanceCache ---------------------------------------------------------


def test_cache_from_path_loads_spec_example() -> None:
    cache = L2InstanceCache.from_path(_SPEC_EXAMPLE)
    inst = cache.get()
    assert inst.accounts  # smoke: spec_example loaded with content
    assert cache.path == _SPEC_EXAMPLE


def test_cache_get_is_stable_across_calls() -> None:
    """``get()`` must NOT re-parse the YAML — same object identity."""
    cache = L2InstanceCache.from_path(_SPEC_EXAMPLE)
    a = cache.get()
    b = cache.get()
    assert a is b


def test_cache_replace_swaps_in_memory_only(tmp_path: Path) -> None:
    """``replace`` updates the cached instance; no disk write."""
    fixture_text = _SPEC_EXAMPLE.read_text()
    sandboxed = tmp_path / "spec_example.yaml"
    sandboxed.write_text(fixture_text)
    cache = L2InstanceCache.from_path(sandboxed)
    original = cache.get()
    # A trivial dataclass.replace produces a new L2Instance object.
    from dataclasses import replace
    bumped = replace(original, description="from a Studio mutate")
    cache.replace(bumped)
    assert cache.get() is bumped
    # Disk is unchanged.
    assert sandboxed.read_text() == fixture_text
