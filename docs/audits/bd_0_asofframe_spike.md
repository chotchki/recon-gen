# BD.0 — `AsOfFrame` rollout design spike

**Status:** spike for sign-off — BD.1+ implementation gated on the decisions below.
**Date:** 2026-05-24.
**Prompted by:** Phase BD (AO.11 frame rollout). The `(as_of, window, seed)` frame was introduced as a doc in AO.11; the rollout was never scheduled. BC ships the value-types (`DateInterval`, `SingleDayPlant`, `MultiDayPlant`); BD threads the frame through every temporal callsite using them.

---

## Headline recommendation

**Extend the existing `AsOfFrame` in place; don't introduce a parallel `RunContext`.** The codebase already has 109 references to `AsOfFrame` across `src/` and `tests/`. Replacing with a new name forces 109 import-line rewrites for zero semantic gain. Extending in place lets BD ship as an additive shape (`window_days: int` → `window: DateInterval`; add `seed: int | None`) — existing callers can still read `frame.as_of` unchanged.

The user's PLAN draft offered the "RunContext vs AsOfFrame" naming question. Given the existing surface, **AsOfFrame stays the name**; the dataclass grows.

## Existing shape (`common/as_of_frame.py`)

```python
@dataclass(frozen=True)
class AsOfFrame:
    as_of: date
    window_days: int

    @classmethod
    def locked(cls, *, window_days: int = 0) -> "AsOfFrame": ...
    @classmethod
    def live(cls, *, window_days: int = 0) -> "AsOfFrame": ...

    @property
    def window_start(self) -> date: ...
    def contains(self, day: date) -> bool: ...
```

Two fields, two named constructors, two derived helpers (`window_start` property + `contains()`). Tight by design — the AP.0 spike intentionally kept the surface minimal.

109 callsites in src/ + tests/, dominant uses:
- `AsOfFrame.live().as_of` — the canonical wall-clock seam (the only blessed `date.today()` read post-AQ).
- `cfg.as_of_frame(window_days=...)` — Config helper that constructs the frame from `cfg.end_date` (locked vs live vs explicit-anchor).
- `frame.window_start` / `frame.contains(day)` — invariant + scheduling helpers.

The shape works. The expansion needs to preserve every existing semantic.

## Target shape (BD.1)

```python
@dataclass(frozen=True)
class AsOfFrame:
    as_of: date
    window: DateInterval        # was: window_days: int (computed end was as_of)
    seed: int | None = None     # NEW — third leg of AO.11's frame

    # Existing constructors stay (window_days→window translated at the seam):
    @classmethod
    def locked(cls, *, window: DateInterval | None = None) -> "AsOfFrame": ...
    @classmethod
    def live(cls, *, window: DateInterval | None = None) -> "AsOfFrame": ...

    # New constructors (BD's expansion):
    @classmethod
    def for_audit(cls, today: date, *, lookback_days: int) -> "AsOfFrame":
        """Common audit shape: window = trailing_days_ending_yesterday(today, lookback_days)."""
    @classmethod
    def for_test(cls, *, window: DateInterval, seed: int, as_of: date | None = None) -> "AsOfFrame":
        """Test shape: explicit window + seed; as_of defaults to window.end."""

    # Existing helpers stay:
    @property
    def window_start(self) -> date: ...   # = self.window.start
    def contains(self, day: date) -> bool: ...
```

Three semantic changes:

1. **`window_days: int` → `window: DateInterval`.** The current shape encodes the window as a single-int count rolled forward from `as_of`. That works because the window-end is always `as_of`. The new shape decouples: `window` is its own closed interval, NOT constrained to end at `as_of`. This matters for audit (window ends at yesterday; `as_of` is today) and trainer (window is the operator's scenario span; `as_of` is the scrub head).

   Migration: `frame.window_start` is now a property `→ self.window.start`. `frame.window_days` is a derived `→ self.window.days - 1` (closed-closed off-by-one) — or drop it; check call sites.

2. **`seed: int | None`** added as the third frame leg. Today plant emit takes `seed` as a separate arg; BD folds it into the frame so the `(as_of, window, seed)` triple is one value. `None` means "no override" (the spine's internal default applies); explicit int pins the RNG for byte-identity.

3. **Two new constructors** for the dominant call shapes:
   - `for_audit(today, *, lookback_days)` — audit CLI's "last N days ending yesterday" pattern. Maps to `DateInterval.trailing_days_ending_yesterday(today, lookback_days)`.
   - `for_test(*, window, seed, as_of=None)` — test fixtures' explicit-window pattern. `as_of` defaults to `window.end` (matches today's `for_audit`-like usage); pinning RNG is required because tests should be deterministic.

   Existing `locked()` + `live()` stay for backward compat. Their `window_days` arg is replaced with an optional `window: DateInterval` (None preserves the "anchor only" original behavior via `DateInterval.single_day(as_of)`).

## Open decisions for operator review

- **D1 — Extend `AsOfFrame` in place; don't rename.** 109 callsites stay valid (modulo the `window_days` migration). Naming continuity matters. **Recommendation: extend.**
- **D2 — `as_of: date` or `as_of: datetime`?** Current is `date`. Audit-PDF generated_at is `datetime`. Plant wall-clock anchor (noon-of-anchor-day) is `datetime`. But the frame's PRIMARY job is "what calendar day does this run anchor to?" — that's a date. **Recommendation: `as_of: date` stays; downstream constructs `datetime(as_of.year, as_of.month, as_of.day, 12, 0, 0)` for noon-anchored wall-clock reads (audit CLI already does this).**

- **D3 — `window: DateInterval` not `DateInterval | None`.** Today's `window_days: int = 0` defaults the window to a zero-width span (start == as_of). With BC's `DateInterval` requiring `start <= end`, that becomes `DateInterval.single_day(as_of)`. Existing callers that pass `window_days=0` get the equivalent shape. Callers that pass an explicit `window_days=N` get `DateInterval.trailing_days_ending_today(as_of, N+1)` or similar. **Recommendation: `window: DateInterval` non-optional, default at constructor level to `single_day(as_of)`.** Avoids the `Optional` cost across 109 read-sites.

- **D4 — `seed: int | None`.** Optional because some callers (live trainer, ad-hoc deploys) don't need a deterministic seed; the spine's internal default is fine. Test fixtures + locked seed paths set it explicitly. **Recommendation: `int | None`, default `None`.**

- **D5 — Where does it live?** Extending in place → still `common/as_of_frame.py`. Renaming the module to `common/frame.py` or `common/run_context.py` triggers 109 import-line edits for the same reason as the class rename (no semantic gain, big diff). **Recommendation: keep `common/as_of_frame.py`.**

- **D6 — Backward-compat: keep `window_days` accessor?** Some call sites read `frame.window_days` directly. Two options:
  - **(a)** Drop the field; callers migrate to `frame.window.days - 1` (or `frame.window.days` if the semantic is closed-closed).
  - **(b)** Keep as a `@property` derived from `window`. Callers stay unchanged; the field is just deprecated as a free attribute.
  **Recommendation: (b) — keep as property.** Minimizes diff churn; future cleanup can drop the property in a later sweep.

- **D7 — `for_test` requires `seed`?** Currently the spine's `scenario_to_generators` accepts `seed: int | None` and falls through to a deterministic default. If `for_test` requires `seed`, every test fixture must declare one. **Recommendation: optional with sensible default** (e.g., `for_test(window, seed=0, as_of=None)` — `0` is a fine deterministic default; explicit `None` opts into the spine's internal default).

- **D8 — AST lint?** The user's PLAN draft mentions `no-naked-runcontext-ctor` mirroring BC.1's `no-naked-interval-ctor`. AsOfFrame already has 109 callsites; the bare `AsOfFrame(as_of=X, window=Y)` form is widely used. Two stances:
  - **(a)** No lint. The bare constructor is fine; named constructors are optional.
  - **(b)** Lint, allow the bare constructor inside `common/as_of_frame.py` itself + a small whitelist for legacy callsites; new code must use a named constructor.
  **Recommendation: (a) no lint at first.** Different from intervals — there's no risk of "the operator picks the wrong endpoint convention" because AsOfFrame's fields are unambiguous. Re-evaluate if BD.6 sweep surfaces a pattern that wants enforcement.

- **D9 — `RoleAsOfFrame` / view-side frames** (mentioned in `common/tree/date_view.py`). Out of scope for BD.0. BD.4 (QS / App2 dashboard defaults) will reconcile, but the design for view-side frames stays with the existing `RoleAsOfFrame` / `date_view.py` module.

## What BD doesn't do

- **No `RunContext` introduction.** Naming continuity wins.
- **No `as_of` change to `datetime`.** Calendar date is the right primary; datetime constructs at the seam.
- **No new module file.** Extend `common/as_of_frame.py`.
- **No deprecation of `live()` / `locked()`.** They stay; the new `for_audit` + `for_test` constructors add to the surface.

## Sequencing for BD.1+

1. **BD.1** lands the expanded `AsOfFrame` (window: DateInterval, seed: int | None, for_audit, for_test) + the `window_days` property compatibility shim + property tests. AST lint deferred per D8.
2. **BD.2** audit CLI threads frame. Today the audit calls `AsOfFrame.live()` + `DateInterval` separately; the frame swap consolidates `(period, as_of)` arg pairs into one `frame: AsOfFrame`.
3. **BD.3** plant emit threads frame. `apply_db_seed(frame=...)`, `scenario_to_generators(frame=...)`. `today: date | None` + `plant_window: DateInterval | None` + `seed: int | None` collapse into one `frame: AsOfFrame` arg.
4. **BD.4** dashboard defaults derive from `frame.window` via `View` primitives (Phase AR's promise, deferred until now). `apps/*/app.py` parameter defaults stop being hand-written `RollingDate` strings and become functions of `frame.window`.
5. **BD.5** trainer cache + studio state thread frame. `tg_cache.get_window() → frame.window`. The persistent state on disk becomes the frame's three fields rather than `window_start` + `window_end` + scattered seed configs.
6. **BD.6** sweep dead constants (`DEFAULT_SEED_TODAY` / `_CANONICAL_LOCK_ANCHOR` / `_DATE_START_DEFAULT_EXPR` / etc.).
7. **BD.7** verify + commit + tag (v11.20.0).

## Conclusion

Extending the existing `AsOfFrame` minimizes diff churn while delivering the `(as_of, window, seed)` frame contract AO.11 designed. Three field-level changes (`window: DateInterval`, `seed: int | None`, two new named constructors); 109 existing callsites stay valid via a `window_days` property shim + the unchanged `live()` / `locked()` surface. The architecture lands; the codebase upgrade is sweep-shaped, not rewrite-shaped.

**Operator decision needed:** sign off on D1-D9, then BD.1 implements + tests. Surface any constraints I'm missing — especially any callsite where `frame.window_days` is used in ways the property shim won't satisfy (e.g., dataclass field comparison, JSON serialization).
