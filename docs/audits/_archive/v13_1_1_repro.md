# Studio drift / limit_breach plant "failure" — repro & diagnosis (upstream-safe)

A trainer Apply reports **"N plant(s) failed: drift, limit_breach_outbound"** and the clean/violation tour dashboards come out identical (byte-for-byte). This is **not a duckdb bug** and **not a v13.1.1 regression** — it's the auto-scenario target picker failing to materialize those plants on an L2 whose alphabetically-first template role lacks the rail shape they need.

## Evidence it's not duckdb / not a regression
- The recorded failure (persisted by `apply_plants` to the `_v` overlay kv `<prefix>_v_config_kv`, key `trainer_failed_plants`) is a **`ValueError` raised before any SQL** — so it's **dialect-independent**:
  ```json
  {
    "drift": "ValueError: drift plant: no 2-leg Rail with destination matching the template role declared in this L2.",
    "limit_breach_outbound": "ValueError: outbound limit_breach plant: no Outbound LimitSchedule whose rail matches an outbound 2-leg Rail with an external counterparty in this L2."
  }
  ```
- `common/l2/auto_scenario.py` (the target picker) is **byte-identical between v12.0.2 and v13.1.1**, and the `common/l2/plant_registry.py` diff between those tags is *only* the `Dialect.SQLITE → Dialect.DUCKDB` default-fallback swap — nothing touches the drift/limit-breach target logic.

## Minimal repro — no DB, no studio, no seed (pure L2 logic)

Reproduces on any L2 instance whose first-sorted `AccountTemplate` role has no inbound 2-leg rail:

```python
from datetime import date
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2 import auto_scenario as A

inst = load_instance("<your-l2-instance>.yaml")
tmpl = A._pick_template(inst)                       # "first AccountTemplate sorted by role name"
print(tmpl.role)                                    # -> the alphabetically-first role
print(A._pick_inbound_2leg_rail(inst, tmpl.role))   # -> None  => DriftPlant omitted
print(A._pick_breach_inputs(inst, tmpl.role))       # -> None  => LimitBreachPlant omitted
for kind, why in A.default_scenario_for(inst, today=date(2026,6,4)).omitted:
    print(kind, "::", why)
# -> DriftPlant :: no 2-leg Rail with destination matching template role
# -> LimitBreachPlant :: no Outbound LimitSchedule whose rail matches an outbound 2-leg Rail with external counter
```

Code path: `auto_scenario.py:150` picks the template → `:159` `_pick_inbound_2leg_rail(...)` returns `None` → `:161` omits `DriftPlant`. The operator then enabling drift hits `plant_registry.py:410`, which raises the `ValueError` above. `apply_plants` (`common/l2/v_overlay.py:458`) catches it per-kind and records it into `trainer_failed_plants`.

## Why the picked role fails — and that the L2 *can* support the plant

`_pick_template` takes `roles[0]` (alphabetical). In the instance under test, scanning all template roles for an inbound 2-leg rail:

| Template role (alphabetical order; picker takes the first) | `_pick_inbound_2leg_rail` |
|---|---|
| **Role-1 ← picker takes this (first)** | **None** → drift omitted |
| Role-2 | None |
| Role-3 | ✓ (an inbound 2-leg rail) |
| Role-4 | ✓ (an inbound 2-leg rail) |
| Role-5 | ✓ (an inbound 2-leg rail) |

So a picker that chose any of the three rail-bearing roles (or fell back when the first yielded nothing) would materialize the drift plant on this exact L2. `_pick_breach_inputs` returns `None` for **every** role here — `limit_breach_outbound` is a separate "this L2 declares no matching outbound LimitSchedule" gap, independent of the picker issue.

## Upstream-actionable

**A — Picker robustness (`auto_scenario._pick_template`).** Selecting the alphabetically-first `AccountTemplate` regardless of whether that role can support the requested L1-invariant plants is fragile: on any L2 whose first-sorted role lacks an inbound 2-leg rail, drift + limit-breach silently can't materialize even when a sibling role would work. Suggest: pick the template that maximizes materializable plants, or fall back across templates when the first yields no target.

**B — Surface the omission reason in the studio.** Apply reports "N plant(s) failed: \<kinds\>" but the actual cause (`no 2-leg Rail with destination matching the template role`) is only in the `trainer_failed_plants` kv. An operator can't self-diagnose. Render the per-kind reason (already captured), and ideally disable/annotate plant checkboxes the current L2 can't materialize.

*(The L2-shape part — that the first-sorted role has no inbound 2-leg rail — is a property of the specific instance under test, not an upstream defect.)*
