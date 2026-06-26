# How do I reskin the dashboards for my brand?

*Customization walkthrough — Developer / Product Owner. Reskinning + extending.*

## The story

You've stood up the dashboards against your data. Marketing
walks by, sees the demo's "Sasquatch National Bank" forest-green
palette in production and asks the obvious question: *can we
get our actual brand colors on this?*

Yes, and it takes about ten minutes. The visual layer never
references hex colors directly — every accent, foreground,
background and link tint resolves at generate time from the L2
institution YAML's inline ``theme:`` block. To rebrand you add a
``theme:`` block to your L2 YAML, regenerate, deploy. The
analysis name, KPI accent colors, table-cell tints and
conditional formatting all flip together.

The rebrand surface is SMALL by design: one block on one YAML.
The same constraint that keeps the visual layer rebrand-friendly
(no hardcoded hex in app code) is what keeps this walkthrough
short.

## The question

"My bank's brand book says navy blue and silver, our typography
is Inter and our analysis names need to read 'SNB Treasury' not
'L1 Reconciliation'. Where do I drop those in?"

## Where to look

Two reference points:

- **Your L2 institution YAML's ``theme:`` block** — the per-instance
  brand declaration. See ``tests/l2/spec_example.yaml`` (default
  generic palette) and ``tests/l2/sasquatch_pr.yaml`` (full forest
  green for the demo persona) for examples.
- **`src/recon_gen/common/l2/theme.py`** — the ``ThemePreset``
  dataclass that validates the YAML block at load time. The
  field list there is the contract for the YAML's ``theme:`` block.

There is no longer a global preset registry to extend. Each L2
institution carries its own theme. The CLI no longer accepts a
``--theme-preset`` flag.

## What you'll see in the demo

Look at any L2 YAML's ``theme:`` block to see the color token
contract. ``tests/l2/spec_example.yaml`` carries the canonical
generic palette:

```yaml
theme:
  theme_name: "QuickSight Gen Theme"
  version_description: "Auto-generated dashboard theme"
  analysis_name_prefix: null
  data_colors:
    - "#2E5090"     # primary brand
    - "#E07B39"     # accent series 2
    - "#3A9E6F"     # accent series 3
    # ... 5 more bulk-fill colors
  empty_fill_color: "#E5E7EB"
  gradient: ["#D6E4F5", "#2E5090"]
  primary_bg: "#FFFFFF"
  secondary_bg: "#F8F9FA"
  primary_fg: "#1F2933"
  accent: "#2E5090"
  accent_fg: "#FFFFFF"
  link_tint: "#E8EFF9"
  danger: "#C62828"
  warning: "#E65100"
  success: "#2E7D32"
  # ... rest of the tokens (foreground variants, dimension/measure)
```

Every token has a job. The three that change the most when you
swap in your colors:

- **`accent`** — the dashboard's primary brand color. Drives
  KPI value text, clickable cell text, accent-colored bar fills,
  filter pill backgrounds. The single token most strongly tied
  to "this looks like our brand."
- **`primary_fg`** — body text color. Almost always a near-black
  on a near-white background. Pick a true neutral; an off-spec
  primary_fg can make the whole dashboard feel "off" without
  the user being able to name why.
- **`link_tint`** — the pale-accent background applied to table
  cells whose click target is a right-click menu (drill-action
  cells). Should be ~12% opacity of `accent` on white. The
  ``#E8EFF9`` in the spec_example default is exactly that for
  ``#2E5090``.

The rest (data palette, gradient, semantic colors, dimension /
measure colors) are bulk fills — they affect chart series order
and KPI tile chrome, but brand recognition rides on the three
above.

See it live: https://recon-gen-spec.hotchkiss.io/

## What it means

Reskinning is two steps:

### Step 1 — Declare your theme block on your L2 YAML

Add (or edit) the ``theme:`` block on your L2 institution YAML.
Pattern, using a hypothetical ACME Treasury palette:

```yaml
# acme_treasury.yaml — your institution YAML
description: |
  ACME Treasury — internal cash concentration + customer DDA reconciliation.

# ... accounts / account_templates / rails / etc.

theme:
  theme_name: "ACME Treasury Theme"
  version_description: "ACME Treasury brand palette"
  analysis_name_prefix: null              # production: no prefix
  data_colors:
    - "#0A2647"     # _SNB_NAVY — primary brand
    - "#D4A017"     # _SNB_GOLD — accent series 2
    - "#A4B0BE"     # _SNB_SILVER — accent series 3
    - "#5E8B7E"
    - "#B85C38"
    - "#6B4C8A"
    - "#3A9E6F"
    - "#7A7A72"
  empty_fill_color: "#D9D9D9"
  gradient: ["#D6E4F5", "#0A2647"]
  primary_bg: "#FFFFFF"
  secondary_bg: "#FBF7EE"                 # parchment
  primary_fg: "#1F2933"                   # ink
  secondary_fg: "#A4B0BE"
  accent: "#0A2647"
  accent_fg: "#FFFFFF"
  link_tint: "#E5EAF2"                    # ~12% navy on white
  danger: "#C62828"
  danger_fg: "#FFFFFF"
  warning: "#E65100"
  warning_fg: "#FFFFFF"
  success: "#2E7D32"
  success_fg: "#FFFFFF"
  dimension: "#0A2647"
  dimension_fg: "#FFFFFF"
  measure: "#1F2933"
  measure_fg: "#FFFFFF"
```

If you omit the ``theme:`` block entirely, ``build_theme`` returns
``None`` and the deploy skips emitting a custom Theme resource —
AWS QuickSight CLASSIC takes over for that institution
(silent-fallback contract). Useful for quick smoke tests where
brand colors don't matter yet.

### Step 2 — Regenerate and deploy

```bash
recon-gen json apply --l2 acme_treasury.yaml -c config.yaml -o out/
recon-gen json apply --l2 acme_treasury.yaml -c config.yaml -o out/ --execute
```

The deploy delete-then-creates the theme + analyses + dashboards
with your new tokens. Existing user-saved bookmarks survive (the
dashboard ID is stable across re-deploys); the visual chrome
flips on next load.

## Drilling in

A few tokens to know about beyond the obvious accent:

- **`analysis_name_prefix`** — set to ``null`` for production
  (analysis reads "L1 Reconciliation Dashboard"). Set to
  ``"Demo"`` to name analyses "Demo — L1 Reconciliation
  Dashboard" and visually distinguish demo vs production
  analyses in the QuickSight authoring UI.
- **`data_colors`** — the eight-color series palette. First
  three are most prominent (single-series KPIs, two-series bar
  charts, three-segment stacked charts). Pick three brand
  colors; the remaining five are bulk fills used only on
  high-cardinality breakdowns.
- **`gradient`** — `[light, dark]` for any min/max gradient
  fills (currently the aging bar chart shaded by bucket). Pick
  a tinted version of `accent` for `dark` and a near-white
  tinted version for `light`. Don't pick a complementary color;
  the visual coherence depends on the gradient reading as "more
  of the same brand color."
- **`link_tint`** — keep this paler than you think. The pattern
  guideline (CLAUDE.md "Conventions") is that accent text on a
  pale-tint background signals a right-click drill action.
  Customers who pick a saturated link_tint accidentally make
  every drill cell look like a primary CTA — the cell becomes
  hard to read and competes with the accent KPIs. ~12% opacity
  of accent on white is the safe target.

## Next step

Once your theme block is declared and the deploy reflects your
brand:

1. **Spot-check the three brand-defining surfaces.** Open the L1
   Exceptions sheet and check: KPI text colors (`accent`), table
   cells with right-click drills (`link_tint` background) and the
   aging bar chart (gradient + data_colors series order). These
   are the three places where a wrong token surfaces most
   visibly.
2. **Confirm the analysis name.** With
   ``analysis_name_prefix: null``, the analysis in QuickSight
   reads its app's display name (no prefix). Demo themes
   often use ``"Demo"`` to flag demo deploys.
3. **Multi-instance note.** Each L2 YAML carries its own
   ``theme:``. Deploying L1 against ``sasquatch_pr.yaml`` and
   ``acme_treasury.yaml`` simultaneously gives you two
   dashboards with two distinct themes — no per-app preset
   juggling required.

## The docs site follows the same theme block

The docs site (the mkdocs handbook published from `docs export` /
`docs apply`) reads the SAME `theme:` block — both the colors
above and an optional pair of brand-asset fields.

**Colors.** The handbook chrome — header bar, the hero block on
the landing pages, the walkthrough cards — derives from the L2
theme's `accent` / `accent_fg` / `dimension` / `primary_fg` /
`secondary_fg` / `primary_bg` / `secondary_bg` / `link_tint` /
`warning` tokens (same source-of-truth rule as the dashboards: no
hard-coded hex). Build with your themed L2 and the site picks it
up automatically; no separate mkdocs config edit. When the L2
carries no `theme:` block, the site falls back to a neutral
navy/grey (`common/theme.py::DEFAULT_PRESET`), not a persona
palette.

**Logo / favicon** — two optional fields:

```yaml
theme:
  # ... colors above ...
  logo: "https://example.com/your-logo.svg"
  favicon: "https://example.com/your-favicon.ico"
```

Both accept either:

- **A URL** (`http://`, `https://` or protocol-relative `//`) —
  passed through verbatim to mkdocs `theme.logo` /
  `theme.favicon`.
- **An absolute file path** (must start with `/`) — copied into
  the docs build at render time as
  `<docs_dir>/img/_l2_logo<ext>` and `<docs_dir>/img/_l2_favicon<ext>`,
  with the theme keys rewritten to the docs-relative path.

Relative paths are rejected (their resolution would depend on
the integrator's working directory at build time). When either
field is omitted or `null`, the docs site renders text-only
navigation (no logo) — `mkdocs.yml` ships no default mark.

## Related walkthroughs

- [How do I configure the deploy for my AWS account?](how-do-i-configure-the-deploy.md) —
  the ``config.yaml`` deploy contract; theme declarations now live
  on the L2 YAML, not in this file.
- [How do I publish docs against my L2?](how-do-i-publish-docs-against-my-l2.md) —
  the end-to-end docs export + render flow.
