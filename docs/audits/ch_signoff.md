# CH phase — autonomous overnight sign-off

Branch: `cg-autonomous-CH`. Base: `4f816789` (main pre-branch).
2 commits → `cg-autonomous-CH` HEAD (`90a86696`).

## Shipped

- **CH.0** (`docs/audits/ch_0_replan.md`) — replan + audit
  baseline. Decided: tabular-nums + right-align stay where they
  live (renderTable per-cell class; CF.7 owns the broader sweep
  over hand-written Studio tables); CH primitive owns zebra +
  sticky + header-bg.
- **CH.1+.2** (`90a86696`) — `<thead>` swapped from
  `bg-surface-bg` (matched the page fill, didn't visually stand
  out against body zebra) to `bg-surface-alt` (the slightly-
  darker theme token defined in input.css). `<th>` carries the
  same fill so it extends past padding gaps. `sticky top-0 z-10`
  keeps the header in view during scroll. 6 unit tests pin the
  contract end-to-end (renderTable source markers + CSS token
  presence in input.css + utility compile in output.css).
- **CH.3** — audit-flagged dense tables (Daily Statement detail,
  Limit Breach, Drift detail, ledger tables) all flow through
  `renderTable` → inherit the fix automatically. Verified by
  grep: the only hand-written `<table>` markup in
  `common/html/` is `_studio_routes.py::_contract_table` (the
  ETL contract picker — Studio chrome, NOT a dashboard ledger
  surface; out of CH scope).

## Deferred + flagged for operator review

- **CH.x — QS-side TableCellStyle adapter** — the QS emit
  doesn't have a per-column right-align + tabular-nums knob
  matching the App2 contract. Adding it needs a typed primitive
  expansion on `common/models.py::TableConfiguration` (probably
  `TableInlineVisualOptions` or
  `TableConditionalFormatting`). ~1 day spike; touches the
  deploy probe too. **Not blocking** — operator runs against
  App2 locally; the QS-side gap only surfaces on the deployed
  bundle and the existing currency-format rendering is at
  least readable.
- **CH.y — CF.7 cross-cutting tabular-nums sweep** — the
  hand-written `<table>` in `_studio_routes.py::_contract_table`
  doesn't carry `tabular-nums` on its numeric column. Belongs
  in CF.7 per the lock; the CH primitive scope is dashboard-
  surface tables only.

## Drive-by

- Pre-existing biome `useOptionalChain` FIXABLE warning at
  line ~706 fixed during CH commit (`s.values && s.values[ci]`
  → `s.values?.[ci]`). The pre-CH baseline carried 7 such
  warnings; 5 remain (out of CH scope; would be a separate
  drive-by sweep).

## Verification done

- `RECON_GEN_SKIP_BIOME=0 .venv/bin/biome check src/recon_gen/
  common/html/assets/js/` — 5 warnings (pre-existing), 0
  errors. Pre-CH baseline was 7 warnings — net 2 fewer because
  of the drive-by fix + a deleted dead branch in renderTable.
- `.venv/bin/pytest tests/unit -k "studio or editor or cf3 or
  cf4 or cg or etl or ch_table" -p no:cacheprovider -q` — 748
  passed, 1 skipped.
- Static unit test on bootstrap.js source markers (CH.1/2
  classes present in renderTable + theme tokens defined in
  input.css + utilities compiled into output.css).

## Verification NOT done (flagged for morning)

- **Playwright browser test** (`tests/js/test_render_table.py`)
  — would exercise the rendered DOM end-to-end against a real
  browser. Gated on `QS_GEN_E2E=1` + Playwright install. Not
  run on the autonomous branch because the browser tier
  requires the WSL2 runner (powered off for the night).
- **QS deploy probe** — would verify the App2-side change
  hasn't accidentally diverged from QS in some shared contract.
  Requires AWS credentials.
- **Visual dogfood** — none. The probe + unit tests pin the
  class contract; a live drive against `/dashboards/<...>/`
  with a ledger table would confirm the sticky header behavior
  visually (browser-specific quirks on `sticky top-0` are rare
  in modern browsers but worth a 30-second check post-CH).

## Recommended next steps for morning review

1. Read this doc + `docs/audits/ch_0_replan.md` for context.
2. Open a dashboard with a dense ledger table
   (`/dashboards/recon-l1-dashboard/sheets/.../visuals/...`) +
   scroll it visually. Header should now stand out with the
   slightly-darker fill and stay sticky on scroll.
3. Optional: run `./run_tests.sh up_to=db` for the layered
   sanity check (couldn't run with CI box off).
4. Merge to main via fast-forward.
5. File CH.x (QS-side adapter) + CH.y (CF.7 sweep) into the
   appropriate phases when ready to revisit.

## Self-assessment

Risk profile: very low. Single 2-line class-string change on the
App2 table renderer; the rest is a drive-by lint fix that biome
itself flagged as safe. No logic / data-shape / dependency
changes. All theme tokens already exist; Tailwind compile picks
them up automatically.

Confidence: high that nothing broke; high that the audit's #5
highest-leverage fix (the dense-ledger-tables ask) closes for
App2. QS-side parity stays open as a typed-primitive spike.

CK + CH branches both queued for morning merge.
