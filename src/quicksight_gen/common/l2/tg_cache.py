"""In-memory ``TestGeneratorConfig`` cache for Studio's data-shaping panel.

X.4.h.2 introduces this cache as the in-memory authority for the
trainer's knob state (plants / scope / end_date / seed). Mirrors the
``L2InstanceCache`` shape: constructed once at Studio startup from
``cfg.test_generator``; mutated in-place by the ``/data/knobs/*`` PUT
routes (h.2-h.5); read by the Studio ``/deploy`` route which patches a
fresh ``Config`` clone with ``cache.get()`` before calling
``run_deploy_pipeline``.

No disk persistence here — h.7 layers ``cfg.yaml`` save on top via the
same atomic-write primitive ``L2InstanceCache.save`` uses.

Severability: Studio-only. Dashboards (``quicksight-gen dashboards``)
does NOT instantiate this cache — it has no knobs to mutate. The
absent-cache path is the unit-test surface (``make_studio_routes`` with
``tg_cache=None``); routes that mutate it are mounted only when the
cache exists.
"""

from __future__ import annotations

import dataclasses
from datetime import date

from quicksight_gen.common.config import (
    Config,
    PlantKind,
    ScopeKind,
    TestGeneratorConfig,
)


_UNSET: object = object()


class TestGeneratorCache:
    # Class name starts with "Test" so pytest collection emits a
    # PytestCollectionWarning ("cannot collect: has __init__"). Same
    # ``__test__ = False`` opt-out ``TestGeneratorConfig`` uses.
    __test__ = False

    """Studio-owned in-memory cache of one ``TestGeneratorConfig``.

    Constructed once at Studio startup via
    ``TestGeneratorCache.from_config(cfg)``; the initial state is a
    snapshot of ``cfg.test_generator``. Mutations land via
    ``replace`` (full state swap) or ``update`` (partial — None is a
    valid value for ``end_date`` / ``seed``, so a sentinel marks
    "leave unchanged").

    Read on every Studio render so widgets reflect the current state
    (h.1's renderer + h.2-h.5's widget templates) and on every
    ``/deploy`` invocation so the next pipeline run sees the latest
    knob values.
    """

    __slots__ = ("_state",)

    def __init__(self, state: TestGeneratorConfig) -> None:
        self._state = state

    @classmethod
    def from_config(cls, cfg: Config) -> TestGeneratorCache:
        """Snapshot ``cfg.test_generator`` into a fresh cache."""
        return cls(cfg.test_generator)

    def get(self) -> TestGeneratorConfig:
        """Return the current state.

        ``TestGeneratorConfig`` is frozen, so the returned reference
        is safe to share without defensive-copy concerns.
        """
        return self._state

    def replace(self, new_state: TestGeneratorConfig) -> None:
        """Swap the cached state with a caller-built replacement."""
        self._state = new_state

    def update(
        self,
        *,
        scope: ScopeKind | object = _UNSET,
        end_date: date | None | object = _UNSET,
        seed: int | None | object = _UNSET,
        plants: tuple[PlantKind, ...] | object = _UNSET,
    ) -> TestGeneratorConfig:
        """Partial update — fields left at sentinel are preserved.

        ``None`` is a valid value for ``end_date`` and ``seed`` (it
        means "use the locked default"), so a `_UNSET` sentinel is
        the only way to express "leave this field alone". Returns
        the new state for the caller to inspect / log.
        """
        kwargs: dict[str, object] = {}
        if scope is not _UNSET:
            kwargs["scope"] = scope
        if end_date is not _UNSET:
            kwargs["end_date"] = end_date
        if seed is not _UNSET:
            kwargs["seed"] = seed
        if plants is not _UNSET:
            kwargs["plants"] = plants
        new_state = dataclasses.replace(self._state, **kwargs)
        self._state = new_state
        return new_state

    def patched_config(self, cfg: Config) -> Config:
        """Return a clone of ``cfg`` with ``test_generator`` swapped in.

        Used by the ``/deploy`` route to feed the latest knob state
        into ``run_deploy_pipeline`` without mutating the startup
        ``Config`` (which is shared with the rest of the studio).
        """
        return dataclasses.replace(cfg, test_generator=self._state)
