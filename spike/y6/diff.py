"""Y.6 — diff wide-open vs narrow-7d to surface the date-pushdown win.

Reads runs/y6/wide-open.md + runs/y6/narrow-7d.md (both produced by
measure.py against the same Aurora PG seed) and writes a side-by-side
table to runs/y6/summary.md showing per-dataset row + time reduction.

The "wide-open" scenario is functionally equivalent to pre-Y behavior:
post-Y dataset SQL with sentinel date binds (1900-01-01 .. 9999-12-31)
fetches the same rows pre-Y's no-WHERE SQL would have. The "narrow-7d"
scenario fires the date pushdown — analyst applies a 7-day filter, post-Y
SQL pushes that into the WHERE clause, DB returns narrower set. The
difference IS the headline Y.6 win.
"""

from __future__ import annotations

import re
from pathlib import Path

ROW_PATTERN = re.compile(
    r"\| `(?P<name>[^`]+)` \| (?P<rows>[\d,—]+) \| (?P<ms>[\d,.\-—]+) \|"
)


def parse(path: Path) -> dict[str, tuple[int | None, float | None]]:
    """Return {dataset_id: (rows, median_ms)} from a measure.py output."""
    out: dict[str, tuple[int | None, float | None]] = {}
    for line in path.read_text().splitlines():
        m = ROW_PATTERN.search(line)
        if not m:
            continue
        name = m.group("name")
        rows_s = m.group("rows").replace(",", "")
        ms_s = m.group("ms").replace(",", "")
        rows = int(rows_s) if rows_s.isdigit() else None
        try:
            ms = float(ms_s)
        except ValueError:
            ms = None
        out[name] = (rows, ms)
    return out


def main() -> None:
    base = parse(Path("runs/y6/wide-open.md"))
    narrow = parse(Path("runs/y6/narrow-7d.md"))

    out: list[str] = [
        "# Y.6 — date pushdown win (wide-open vs narrow-7d)",
        "",
        "**Methodology.** Both scenarios run the SAME post-Y dataset SQL",
        "against the SAME seeded Aurora PG (sasquatch_pr, 68,879 transactions",
        "spanning 90 days). The difference is the URL params:",
        "",
        "- **wide-open** — empty `:date_from` / `:date_to`; sentinel binds",
        "  (`1900-01-01` .. `9999-12-31`) match every row. This is",
        "  functionally equivalent to pre-Y behavior — pre-Y dataset SQL had",
        "  no WHERE clause at all, fetched the full set, then QS engine",
        "  narrowed in-process.",
        "- **narrow-7d** — `:date_from = today-7d`, `:date_to = today`. Post-Y",
        "  pushes those binds into the dataset SQL WHERE; the DB returns the",
        "  narrowed set. THIS is what an analyst sees after they pick a",
        "  date range from the universal filter at the top of the dashboard.",
        "",
        "Each query ran 3 times; median ms reported. Aurora PG, us-east-1.",
        "",
        "## Per-dataset deltas",
        "",
        "| Dataset | Wide rows | Narrow rows | Δ rows | Wide ms | Narrow ms | Δ ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]

    big_wins: list[tuple[str, int, int, float]] = []
    no_change: list[str] = []
    total_wide_rows = 0
    total_narrow_rows = 0
    total_wide_ms = 0.0
    total_narrow_ms = 0.0

    for name in sorted(base):
        if name not in narrow:
            continue
        wr, wm = base[name]
        nr, nm = narrow[name]
        if wr is None or nr is None or wm is None or nm is None:
            continue

        d_rows = nr - wr
        d_rows_pct = (d_rows / wr * 100) if wr > 0 else 0.0
        d_ms = nm - wm
        d_ms_pct = (d_ms / wm * 100) if wm > 0 else 0.0

        total_wide_rows += wr
        total_narrow_rows += nr
        total_wide_ms += wm
        total_narrow_ms += nm

        if wr > 0 and d_rows < 0 and abs(d_rows_pct) >= 50:
            big_wins.append((name, wr, nr, d_rows_pct))
        elif wr == nr and wr > 100:
            no_change.append(name)

        d_rows_str = (
            f"{d_rows:+,} ({d_rows_pct:+.0f}%)"
            if wr > 0 else "—"
        )
        d_ms_str = f"{d_ms:+,.0f} ({d_ms_pct:+.0f}%)" if wm > 0 else "—"

        out.append(
            f"| `{name}` | {wr:,} | {nr:,} | {d_rows_str} "
            f"| {wm:,.0f} | {nm:,.0f} | {d_ms_str} |"
        )

    total_d_rows_pct = (
        (total_narrow_rows - total_wide_rows) / total_wide_rows * 100
        if total_wide_rows else 0.0
    )
    total_d_ms_pct = (
        (total_narrow_ms - total_wide_ms) / total_wide_ms * 100
        if total_wide_ms else 0.0
    )

    out += [
        "",
        "## Headline numbers",
        "",
        f"- **Total rows on the wire**: {total_wide_rows:,} → {total_narrow_rows:,} "
        f"({total_narrow_rows - total_wide_rows:+,}; {total_d_rows_pct:+.1f}%)",
        f"- **Total query time**: {total_wide_ms:,.0f} ms → {total_narrow_ms:,.0f} ms "
        f"({total_narrow_ms - total_wide_ms:+,.0f} ms; {total_d_ms_pct:+.1f}%)",
        "",
        "## Big wins (≥50% row reduction)",
        "",
    ]
    if big_wins:
        out.append("| Dataset | Wide rows | Narrow rows | Δ |")
        out.append("|---|---:|---:|---:|")
        for name, wr, nr, pct in sorted(big_wins, key=lambda x: x[3]):
            out.append(f"| `{name}` | {wr:,} | {nr:,} | {pct:+.0f}% |")
    else:
        out.append("_(none)_")

    out += [
        "",
        "## Datasets that didn't narrow (>100 rows, no row delta)",
        "",
        "These are either non-date-dimensional matviews (Investigation",
        "fanout dataset SQL doesn't reference posting; L2FT meta values",
        "table is dimension-tag exhaustive) or operational datasets",
        "(facets, ID enumerations) that intentionally span the full",
        "history. Future-Y candidates if perf regresses against bigger",
        "seeds.",
        "",
    ]
    if no_change:
        for name in sorted(no_change):
            out.append(f"- `{name}`")
    else:
        out.append("_(none)_")

    Path("runs/y6/summary.md").write_text("\n".join(out))
    print("wrote runs/y6/summary.md")
    print(f"\nTotal rows: {total_wide_rows:,} → {total_narrow_rows:,} ({total_d_rows_pct:+.1f}%)")
    print(f"Total time: {total_wide_ms:,.0f} → {total_narrow_ms:,.0f} ({total_d_ms_pct:+.1f}%)")
    print(f"Big wins: {len(big_wins)} datasets")


if __name__ == "__main__":
    main()
