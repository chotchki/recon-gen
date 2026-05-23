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

Severability: Studio-only. Dashboards (``recon-gen dashboards``)
does NOT instantiate this cache — it has no knobs to mutate. The
absent-cache path is the unit-test surface (``make_studio_routes`` with
``tg_cache=None``); routes that mutate it are mounted only when the
cache exists.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from pathlib import Path

from recon_gen.common.as_of_frame import AsOfFrame
from recon_gen.common.config import (
    Config,
    PlantKind,
    ScopeKind,
    TestGeneratorConfig,
)
from recon_gen.common.l2.seed import DEFAULT_BASELINE_WINDOW_DAYS
from recon_gen.common.l2.studio_state import (
    StudioState,
    load_studio_state,
    merge_into_test_generator,
    save_studio_state,
    sidefile_path_for,
)


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

    __slots__ = (
        "_state", "_window_start", "_window_end", "_etl_hook_enabled",
        "_state_path",
    )

    def __init__(
        self,
        state: TestGeneratorConfig,
        window_start: date | None = None,
        window_end: date | None = None,
        *,
        etl_hook_enabled: bool = True,
        state_path: Path | None = None,
    ) -> None:
        if window_end is None:
            # The scenario-end / plant-projection anchor. Defaults to
            # wall-clock today for the live trainer (scenario ends "now");
            # it is DISTINCT from the load-up-to scrub head (up_to =
            # state.end_date), which the trainer slides independently
            # (start early to show good days, advance to reveal the issue).
            # Deterministic surfaces (tests, authored scenarios) pin it via
            # the window_end arg rather than relying on wall-clock.
            # AQ.3 funnel: live trainer ends at "now" via AsOfFrame.live()
            # — the sole blessed wall-clock site. Deterministic surfaces
            # (tests, authored scenarios) still pin via the window_end arg.
            window_end = AsOfFrame.live().as_of
        if window_start is None:
            window_start = window_end - timedelta(
                days=DEFAULT_BASELINE_WINDOW_DAYS - 1,
            )
        self._state = state
        self._window_start = window_start
        self._window_end = window_end
        # X.4.h.etl-toggle — when False, patched_config nukes
        # cfg.etl_hook for that deploy without erasing the configured
        # command. Lets the trainer skip the upstream re-seed step on
        # iterative deploys (faster) while preserving the YAML config
        # for the next "fresh start" deploy.
        self._etl_hook_enabled = etl_hook_enabled
        # X.4.h.7 — when set, every mutation method writes the cache's
        # persistent state to this sidefile (atomic, single-file). When
        # None (unit-test surface, plain `from_config` flow), no
        # persistence happens. Studio CLI sets this via
        # `from_cfg_with_state`.
        self._state_path = state_path

    @classmethod
    def from_config(cls, cfg: Config) -> TestGeneratorCache:
        """Snapshot ``cfg.test_generator`` + materialize default window.

        Window default = ``[today - (DEFAULT_BASELINE_WINDOW_DAYS - 1),
        today]`` — the last 90 days. Trainer-mode UI is not a
        determinism path, so the wall-clock anchor is honest here.

        No sidefile persistence — see ``from_cfg_with_state`` for the
        Studio-CLI flow that loads + saves to disk.
        """
        return cls(cfg.test_generator)

    @classmethod
    def from_cfg_with_state(
        cls, cfg: Config, cfg_path: Path | str,
    ) -> TestGeneratorCache:
        """X.4.h.7 — Studio-CLI factory. Load the sidefile if present,
        merge its overrides on top of cfg.test_generator defaults, wire
        the cache to write to that sidefile on every mutation.

        Sidefile path is ``<cfg_path.parent>/.studio-state.yaml``
        (sibling of cfg.yaml). Missing sidefile ⇒ pristine cfg defaults
        + empty Studio state. Malformed sidefile ⇒ same fallback with
        a warning to stderr (per ``load_studio_state``).
        """
        path = sidefile_path_for(cfg_path)
        sidefile = load_studio_state(path)
        merged = merge_into_test_generator(cfg.test_generator, sidefile)
        if sidefile is None:
            return cls(merged, state_path=path)
        return cls(
            merged,
            window_start=sidefile.window_start,
            window_end=sidefile.window_end,
            etl_hook_enabled=(
                sidefile.etl_hook_enabled
                if sidefile.etl_hook_enabled is not None else True
            ),
            state_path=path,
        )

    def _persist(self) -> None:
        """Write the current state to the sidefile when wired.

        Called by every mutation method. No-op when ``_state_path is
        None`` (the unit-test surface). All trainer-mutable fields
        flow through here — the sidefile is the snapshot, not a diff.
        """
        if self._state_path is None:
            return
        snapshot = StudioState(
            scope=self._state.scope,
            end_date=self._state.end_date,
            seed=self._state.seed,
            plants=self._state.plants,
            only_template=self._state.only_template,
            derive_balances=self._state.derive_balances,
            window_start=self._window_start,
            window_end=self._window_end,
            etl_hook_enabled=self._etl_hook_enabled,
        )
        save_studio_state(snapshot, self._state_path)

    def get(self) -> TestGeneratorConfig:
        """Return the current generator state.

        ``TestGeneratorConfig`` is frozen, so the returned reference
        is safe to share without defensive-copy concerns.
        """
        return self._state

    def get_window(self) -> tuple[date, date]:
        """Return ``(window_start, window_end)`` — always concrete dates."""
        return (self._window_start, self._window_end)

    def is_etl_hook_enabled(self) -> bool:
        """Return whether ``cfg.etl_hook`` will run on the next Deploy.

        True (default) ⇒ ``patched_config`` keeps ``cfg.etl_hook`` as
        configured. False ⇒ ``patched_config`` clears it to None for
        that deploy (the cfg's stored command is unaffected — the
        operator can flip the toggle back on without re-typing).
        """
        return self._etl_hook_enabled

    def set_etl_hook_enabled(self, enabled: bool) -> None:
        """Toggle ``cfg.etl_hook`` execution on the next Deploy."""
        self._etl_hook_enabled = enabled
        self._persist()

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
        self._persist()

    def update_only_template(self, value: str | None) -> None:
        """X.4.i.3 — set the only_template name (template-scope target).

        ``None`` clears the field. Validation against the L2 instance's
        actual templates happens at deploy time in
        `_only_template_rails`, not here — the UI accepts any string
        so the trainer can hold an inconsistent state mid-edit.
        """
        self._state = dataclasses.replace(self._state, only_template=value)
        self._persist()

    def update_derive_balances(self, enabled: bool) -> None:
        """X.4.i.3 — toggle the derive_balances post-step-3 flag."""
        self._state = dataclasses.replace(
            self._state, derive_balances=enabled,
        )
        self._persist()

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
        self._persist()
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
        self._persist()
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
        # X.4.h.etl-toggle — "upstream re-seed" is a coupled pair:
        # step_1_etl_hook (the shell command, usually fetches /
        # refreshes the upstream source) AND step_2_pull (copies
        # from cfg.etl_datasource into the demo DB). When the
        # trainer flips the toggle off, BOTH are nuked on the
        # patched cfg — step 1 + step 2-pull both no-op for this
        # deploy. Decoupling them produces 500s when the operator's
        # etl_datasource only exists *because* the hook started it
        # (e.g. local postgres the hook brings up). The original
        # cfg's stored fields are untouched — re-enable + re-deploy
        # restores both.
        new_etl_hook = (
            cfg.etl_hook if self._etl_hook_enabled else None
        )
        new_etl_datasource = (
            cfg.etl_datasource if self._etl_hook_enabled else None
        )
        return dataclasses.replace(
            cfg,
            test_generator=resolved,
            etl_hook=new_etl_hook,
            etl_datasource=new_etl_datasource,
        )
