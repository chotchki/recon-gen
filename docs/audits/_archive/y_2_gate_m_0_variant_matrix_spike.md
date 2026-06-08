# Y.2.gate.m.0 — Variant matrix composition (LOCKED 2026-05-08)

> **Status: LOCKED.** This is the spec for `Y.2.gate.m.{1..5}` implementation.
> **Out-of-scope:** the broader `config.yaml` redesign explored during iteration (multi-database cfg, deploy.local + deploy.aws blocks, signing integration, etc.) — not blocking m.* work; revisit when it earns its own gate.

## Model

Variants are a **3-axis matrix** `scenario × dialect × target`. The matrix only matters at the test layer; production deploys are single-cell.

## Naming convention (run-internal only)

Variant codes are `<sc>_<di>_<ta>` — three short components joined by `_`. Operators don't type these except for single-cell triage (copy from artifact path).

| Axis     | Values                                                  | Codes                          |
|----------|---------------------------------------------------------|--------------------------------|
| scenario | spec_example, sasquatch_pr, fuzz seed N, user-supplied  | `sp`, `sq`, `f<n>`, `us`       |
| dialect  | postgres, oracle, sqlite                                | `pg`, `or`, `sl`               |
| target   | local container, AWS (operator's external Aurora)       | `lo`, `aw`                     |

Examples:
- `sp_pg_lo` — spec_example × postgres × local container
- `f42_or_lo` — fuzz seed 42 × oracle × local container
- `us_sl_lo` — user-supplied × sqlite × local container
- `sq_pg_aw` — sasquatch_pr × postgres × AWS

Codes live under `runs/<run_id>/<variant>/`. Run-scoped, not durable across runs (different runs may pick different fuzz seeds → different codes). Variant codes also flow into DB schema prefixes + AWS QS resource tags (`L2Instance:<variant>`) for cross-cell deconfliction during parallel runs.

## Invalid cells

The matrix expander knows which cells are not valid and skips them automatically:

- **`<any>_sl_aw`** — SQLite is file-based; QuickSight can't reach it via a remote DataSource. Generator errors at runtime: "sqlite has no aws target."
- **`us_*_*`** excluded from `full` — operator must opt in by passing `--scenarios=us:path/foo.yaml` explicitly (a `us` cell can't form without operator-supplied yaml).

## `full` matrix definition

`full` (the default when no narrowing flags) = the union of:

- `{sp, sq} × {pg, or, sl} × {lo}` — 6 cells (named scenarios × all dialects × local)
- `{sp, sq} × {pg, or} × {aw}` — 4 cells (named scenarios × non-sqlite × AWS)
- `{f<one-default-seed>} × {pg, or, sl} × {lo}` — 3 cells (one fuzz seed × all dialects × local)

Total **13 cells**. Excludes `us` (operator opt-in) and excludes fuzz-on-aws (cost-control default; reachable via explicit narrowing).

## Operator-facing CLI

```bash
./run_tests.sh up_to=<layer>                          # default = full matrix
./run_tests.sh up_to=browser --scenarios=sp,sq        # narrow scenario axis
./run_tests.sh up_to=browser --dialects=pg,or         # narrow dialect axis
./run_tests.sh up_to=browser --targets=lo             # narrow target axis
./run_tests.sh up_to=browser --scenarios=us:run/customer_acme.yaml  # opt-in user-supplied
./run_tests.sh up_to=browser --scenarios=fuzz:5       # 5 fuzz seeds (default 1 within `full`)
./run_tests.sh up_to=browser --variants=sp_pg_lo      # explicit single cell (triage)
```

`up_to=<layer>` (existing) still gates how far the chain goes per cell. Sub-flags compose multiplicatively to narrow the cell set; `--variants=<code>` is the escape hatch for "give me exactly this one cell."

## Migration shape (for m.1+ implementation)

```python
# common/variant.py (new)
ScenarioCode: TypeAlias = str  # "sp" | "sq" | f"f{n}" | "us"
DialectCode: TypeAlias = Literal["pg", "or", "sl"]
TargetCode: TypeAlias = Literal["lo", "aw"]


@dataclass(frozen=True)
class VariantSpec:
    scenario: ScenarioCode
    dialect: DialectCode
    target: TargetCode

    # Resolution metadata — set during parse, not part of the code identity
    fuzz_seed: int | None = None    # set when scenario.startswith("f")
    user_yaml: Path | None = None   # set when scenario == "us"

    @property
    def name(self) -> str:
        return f"{self.scenario}_{self.dialect}_{self.target}"

    def is_valid(self) -> bool:
        # SQLite has no AWS target; user-supplied requires opt-in (caller checks)
        if self.dialect == "sl" and self.target == "aw":
            return False
        return True


def expand_full() -> list[VariantSpec]:
    """The 13-cell `full` matrix. Excludes us + fuzz-on-aws by default."""
    cells: list[VariantSpec] = []
    for sc in ("sp", "sq"):
        for di in ("pg", "or", "sl"):
            cells.append(VariantSpec(sc, di, "lo"))
        for di in ("pg", "or"):
            cells.append(VariantSpec(sc, di, "aw"))
    # Default 1 fuzz seed × 3 dialects × local
    default_seed = derive_default_fuzz_seed()  # commit-SHA-derived, stable per chain
    for di in ("pg", "or", "sl"):
        cells.append(VariantSpec(f"f{default_seed}", di, "lo", fuzz_seed=default_seed))
    return cells
```

`KNOWN_VARIANTS` retires after m.4 — all variant strings flow through the new parser; the legacy `local-pg`/`local-oracle`/etc. names become a back-compat alias table during transition, then delete.

## Implementation order (m.1+ landing)

1. **m.1** — `VariantSpec` dataclass + parser + `expand_full` + sub-flag composer (`--scenarios` / `--dialects` / `--targets`). NewType-wrap codes. Pyright-strict scope. Unit tests cover each axis + cross-product + `--variants=<code>` escape hatch + invalid-cell rejection.
2. **m.2** — Scenario fan-out: `_run_one_variant(spec)` reads from spec; verify AWS resource isolation via `L2Instance:<variant>` tag holds for parallel cells in same dialect.
3. **m.3** — Fuzz scenario: `--scenarios=fuzz:N` actually instantiates N variants with seeds from a deterministic per-commit-SHA pool. `f<n>` parser route resolves to the synthesized L2.
4. **m.4** — `full` default + invalid-cell skip + `audit-dashboard-agreement` re-enable. The currently-skipped `tests/e2e/test_audit_dashboard_agreement.py` finds both `sp_pg_lo` and `sp_or_lo` in the same chain and unblocks.
5. **m.5** — Live validation: 5 acceptance criteria from PLAN sub-task.

## Open follow-ups (not blocking m.*)

- **`sasquatch_ar` re-introduction** — when it returns, gets code `sa` and joins the matrix.
- **CI cell budget** — `k.1` decides whether CI runs the full 13 cells or a subset.
- **`config.yaml` redesign** — Chris's iteration sketch (multi-database cfg, deploy.local/aws blocks, signing integration) is its own gate when it earns one.
- **Per-variant artifact-path collisions** — `runs/<run_id>/<variant>/` uses `_` already (FS-safe everywhere); no slug needed.
