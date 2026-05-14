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
from datetime import date, timedelta

from quicksight_gen.common.config import (
    Config,
    PlantKind,
    ScopeKind,
    TestGeneratorConfig,
)
from quicksight_gen.common.l2.seed import DEFAULT_BASELINE_WINDOW_DAYS


_UNSET: object = object()


class TestGeneratorCache:
    # Class name starts with "Test" so pytest collection emits a
    # PytestCollectionWarning ("cannot collect: has __init__"). Same
    # ``__test__ = False`` opt-out ``TestGeneratorConfig`` uses.
    __test__ = False

    """Studio-owned in-memory cache of ``TestGeneratorConfig`` + window.

    Three pieces of mutable state:

    - ``_state: TestGeneratorConfig`` — the frozen generator config
      (plants / scope / seed / end_date — where end_date is the
      simulation "up to" cutoff, mirroring tg.end_date).
    - ``_window_start: date`` — left edge of the trainer's scenario
      window. Defaults to ``today - (DEFAULT_BASELINE_WINDOW_DAYS - 1)``.
    - ``_window_end: date`` — right edge of the trainer's scenario
      window. Defaults to ``today``.

    The window is **Studio-only** — purely a UI concern for the timeline
    panel. It does NOT round-trip through the generator (Deploy reads
    only ``tg.end_date`` as the simulation anchor; the window doesn't
    affect what gets emitted). The trainer picks a window of interest
    and then scrubs ``up_to`` (= ``tg.end_date``) within it.

    ``patched_config(cfg)`` resolves ``end_date=None`` → ``window_end``
    so Deploy always sees a concrete date. CLI invocations keep the
    None-means-today semantic (see ``cli/_helpers.py``); the
    Studio-resolved value lives only inside the patched-cfg clone.
    """

    __slots__ = ("_state", "_window_start", "_window_end")

    def __init__(
        self,
        state: TestGeneratorConfig,
        window_start: date | None = None,
        window_end: date | None = None,
    ) -> None:
        if window_end is None:
            window_end = date.today()  # typing-smell: ignore[no-datetime-now]: trainer-mode default window — wall-clock today is the operator-friendly anchor for "last 90 days"; not a determinism path
        if window_start is None:
            window_start = window_end - timedelta(
                days=DEFAULT_BASELINE_WINDOW_DAYS - 1,
            )
        self._state = state
        self._window_start = window_start
        self._window_end = window_end

    @classmethod
    def from_config(cls, cfg: Config) -> TestGeneratorCache:
        """Snapshot ``cfg.test_generator`` + materialize default window.

        Window default = ``[today - (DEFAULT_BASELINE_WINDOW_DAYS - 1),
        today]`` — the last 90 days. Trainer-mode UI is not a
        determinism path, so the wall-clock anchor is honest here.
        """
        return cls(cfg.test_generator)

    def get(self) -> TestGeneratorConfig:
        """Return the current generator state.

        ``TestGeneratorConfig`` is frozen, so the returned reference
        is safe to share without defensive-copy concerns.
        """
        return self._state

    def get_window(self) -> tuple[date, date]:
        """Return ``(window_start, window_end)`` — always concrete dates."""
        return (self._window_start, self._window_end)

    def get_up_to(self) -> date:
        """Resolve the "up to" / scrub-head date.

        ``tg.end_date`` is the cached value; when it's None the cache
        falls back to ``window_end`` (the trainer's intent: "render up
        through the right edge of my scenario window").
        """
        return self._state.end_date or self._window_end

    def replace(self, new_state: TestGeneratorConfig) -> None:
        """Swap the cached generator state (window untouched)."""
        self._state = new_state

    def update(
        self,
        *,
        scope: ScopeKind | object = _UNSET,
        end_date: date | None | object = _UNSET,
        seed: int | None | object = _UNSET,
        plants: tuple[PlantKind, ...] | object = _UNSET,
    ) -> TestGeneratorConfig:
        """Partial update of the generator state — window is separate.

        ``None`` is a valid value for ``end_date`` and ``seed`` (it
        means "use the locked default"), so a `_UNSET` sentinel is
        the only way to express "leave this field alone". Returns
        the new generator state for the caller to inspect / log.
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

    def update_window(
        self,
        *,
        start: date | object = _UNSET,
        end: date | object = _UNSET,
    ) -> tuple[date, date]:
        """Partial update of the trainer's scenario window.

        Both bounds optional — pass only what changed. After update
        if ``start > end``, swap them (operator typed in a confusing
        order; preserve the intent rather than reject). Returns the
        new ``(start, end)`` tuple for caller logging.

        Window changes do NOT touch ``end_date`` (the up_to scrub head)
        — it stays where the operator set it. The renderer clamps
        out-of-window up_to values for display; the next click in the
        panel will overwrite to a valid date anyway.
        """
        new_start = (
            start if isinstance(start, date) else self._window_start
        )
        new_end = end if isinstance(end, date) else self._window_end
        if new_start > new_end:
            new_start, new_end = new_end, new_start
        self._window_start = new_start
        self._window_end = new_end
        return (new_start, new_end)

    def patched_config(self, cfg: Config) -> Config:
        """Return a clone of ``cfg`` with ``test_generator`` swapped in.

        Trainer "scrub head" model — ``end_date`` and ``cutoff_date``
        play different roles in the patched cfg:

        - ``end_date`` becomes ``window_end`` (the scenario anchor).
          Generator anchors at this date so plants land at fixed
          calendar positions regardless of where the trainer's scrub
          head is. Stable scenario.
        - ``cutoff_date`` becomes the trainer's ``up_to`` (the scrub
          head). Deploy's ``_build_generator_sql`` appends DELETE
          statements after the generator emits to truncate rows past
          this date. None when up_to == window_end (no truncation).

        This decouples "what scenario am I rendering?" (anchor =
        window_end) from "how far through it am I?" (cutoff = up_to).
        Click in the timeline → up_to changes, plants stay put,
        emission cuts off at the new scrub head. Matches the trainer's
        mental model end-to-end.

        CLI invocations of ``data apply`` don't go through this
        method — they read ``cfg.test_generator`` directly, where
        ``end_date`` keeps its legacy "anchor" meaning and
        ``cutoff_date`` defaults to None (no truncation, current
        byte-identical-to-locked-seeds behavior).
        """
        cfg_anchor = self._window_end
        cfg_cutoff: date | None = (
            self._state.end_date
            if self._state.end_date is not None
            and self._state.end_date < self._window_end
            else None
        )
        resolved = dataclasses.replace(
            self._state,
            end_date=cfg_anchor,
            cutoff_date=cfg_cutoff,
        )
        return dataclasses.replace(cfg, test_generator=resolved)
