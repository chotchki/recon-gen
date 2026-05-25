# BD.6 — Retired temporal constants

BD.6 sweeps temporal constants that duplicated `AsOfFrame` / `LOCKED_ANCHOR`
state, leaving every callsite to either consume a frame or construct one via a
named factory. Catalog of what came out:

## Retired constants

### `cli/data.py::_CANONICAL_LOCK_ANCHOR`

**Was:** `_CANONICAL_LOCK_ANCHOR = LOCKED_ANCHOR` — an alias kept "for caller compat"
after AQ.3 funneled the value onto `common/as_of_frame.py::LOCKED_ANCHOR`.

**Why retired:** The AQ.3 funnel made `LOCKED_ANCHOR` the sole source. The alias
was a transition shim — the only callers were one self-reference in
`cli/data.py::data_semantic_lock` and one identity-assertion in
`test_as_of_frame.py`. Both migrated to import `LOCKED_ANCHOR` directly.
No-compat-shim posture per the BD.0 spike's D6 revision.

**Migration:**

- `cli/data.py::data_semantic_lock` now calls
  `_build_fresh_semantic_lock_sqlite(instance, LOCKED_ANCHOR, ...)` directly.
- `test_as_of_frame.py::test_locked_anchor_is_the_single_source_of_truth`
  replaced with `test_locked_anchor_value_pinned` — locks the value
  (`date(2030, 1, 1)`) rather than the (now-gone) alias-identity.

### `tests/e2e/_seed_helpers.py::DEFAULT_SEED_TODAY`

**Was:** `DEFAULT_SEED_TODAY = date(2030, 1, 1)` — the test-helper's default
`today` value, same magic date as `LOCKED_ANCHOR`.

**Why retired:** Duplicate of `LOCKED_ANCHOR`. The two had identical values
since M.2a.8 first introduced the helper; the helper module just predated
the AQ.3 funnel.

**Migration:** `apply_db_seed`'s `today_ref = today or LOCKED_ANCHOR` reads
from the canonical anchor directly. No external callers imported the symbol
(verified via grep).

## NOT retired (deliberate keep)

### `AsOfFrame.locked(window_days=N)` / `AsOfFrame.live(window_days=N)`

The `window_days: int = 0` ergonomic kwarg on the named constructors stays
per BD.0's D6 ("construction-time ergonomics ≠ runtime escape hatch"). The
kwarg is the typed-frame factory's input shape, not a stored field. Reads
still go through `frame.window` (the typed `DateInterval`), never an int.

### `Config.as_of_frame(*, window_days=0)`

Same justification — it's a thin pass-through to `AsOfFrame.locked/live`.

### Studio `_studio_routes.py` wall-clock reads

Three `date.today()` reads in studio routes that are explicitly NOT a
determinism path (trainer URL default-detection, "last 90 days" reset).
Each carries a `typing-smell: ignore[no-datetime-now]` with the rationale
inline. Determinism contract: studio is operator-facing, not test-pinned.

### `cli/audit/__init__.py:1466::generated_at = datetime.now()`

Wall-clock PDF generation timestamp on the audit cover page. Distinct from
the audit's `as_of` (which routes through `AsOfFrame.live()` per BD.2's
`_resolve_frame`). The timestamp is "when this PDF was made", a separate
concept from "what date is being audited".

## Coverage

After BD.6 the only canonical demo anchor in the codebase is
`common/as_of_frame.py::LOCKED_ANCHOR`. Every locked seed, every locked
semantic-lock, every `AsOfFrame.locked()` constructor reads it. Grep
confirms no parallel `date(2030, 1, 1)` literal outside the locked-seed
fixtures themselves (which encode it as-is in their byte content).
