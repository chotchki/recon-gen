"""AM.1 step 2 — Tailwind utility-string helpers for the Studio editor.

Per AM.0 lock **L2** (raw utilities, no `@apply` component classes)
+ **L2.a** (helpers return ONE utility string + zero/one-bool param;
variants compose at the call site, NOT internally — prevents
reinventing `@apply`'s component-class problem in Python).

Per AM.0 lock **L5** (theme inheritance): every utility below
resolves from `--color-*` at paint time. The per-L2 runtime
`<style>:root { --color-accent: ...; }</style>` override (per
`_studio_routes.py::studio_theme_head`) propagates through every
utility automatically — no per-helper theme plumbing needed.

10 helpers absorbing 190+ class-string occurrences across the
editor / diagram chrome / data panel renderers. Each is a
zero-param `def name() -> str` returning a literal utility string;
the only state branch in this module is the documented exception
(see L2.a guardrail in PLAN.md AM.0 — if any helper grows beyond
one bool param or starts switching on enum-like state, that's a
smell to break it up).

Variant composition at the call site:

```python
from recon_gen.common.html._studio_assets.tw_classes import (
    entity_card_classes,
)

cls = entity_card_classes()
if editing:
    cls += " border-accent ring-2 ring-accent/15"
html = f'<article class="{cls}">...</article>'
```
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Editor screens (AM.1)
# ---------------------------------------------------------------------------


def entity_card_classes() -> str:
    """Read-card container (`_render_read_card`). 15+ uses across the
    editor list page. Variants composed at call site:
    `.editing` → append `border-accent ring-2 ring-accent/15`."""
    return (
        "bg-white border border-surface-border rounded-md p-4 text-sm"
    )


def field_row_classes() -> str:
    """Form field wrapper (`<div class="field-row">`). 30+ uses
    across every editor form field — every `<label>` + `<input>` /
    `<select>` / `<textarea>` lives in one of these."""
    return "flex flex-col gap-1 mb-3"


def field_input_classes() -> str:
    """`<input>` / `<select>` / `<textarea>` styling. 30+ uses
    across every editor form. Variant for textarea: append
    ` resize-y min-h-16` at call site."""
    return (
        "px-2 py-2 border border-surface-border rounded-sm text-sm "
        "bg-white focus:outline-2 focus:outline-accent "
        "focus:-outline-offset-1 focus:border-accent"
    )


def primary_button_classes() -> str:
    """Form submit buttons + deploy-btn. 6 uses across the editor
    forms + the diagram-header deploy button. Disabled variant:
    append ` disabled:opacity-60 disabled:cursor-not-allowed`."""
    return (
        "bg-accent text-accent-fg border border-accent px-4 py-2 "
        "rounded-sm cursor-pointer text-sm hover:opacity-85"
    )


# ---------------------------------------------------------------------------
# Diagram chrome (AM.2)
# ---------------------------------------------------------------------------


def chrome_button_classes() -> str:
    """Diagram-chrome utility buttons (Reset / engine-link / etc.).
    5+ uses across the diagram toolbar. Active variant: append
    ` bg-accent text-white border-accent`."""
    return (
        "bg-link-tint text-accent border border-surface-border "
        "px-3 py-1 rounded-sm cursor-pointer text-sm "
        "hover:bg-accent hover:text-white"
    )


# ---------------------------------------------------------------------------
# Data panel — trainer mode (AM.2)
# ---------------------------------------------------------------------------


def ghost_button_classes() -> str:
    """Minimal chrome buttons (window-reset / end-date-step /
    seed-roll / seed-clear). 5 uses across the trainer panel.
    Primary-action variant (e.g. seed-roll): append
    ` border-accent`."""
    return (
        "appearance-none bg-white border border-surface-border "
        "rounded-sm px-2 py-0.5 text-sm cursor-pointer "
        "hover:bg-surface-bg"
    )


def compact_input_classes() -> str:
    """Trainer-panel compact `<input>`s (window-input / end-date-input
    / seed-input). 4 uses. Char-count widths apply at call site:
    `seed-input` → append ` w-[9ch] tabular-nums`."""
    return (
        "text-sm px-1 py-0.5 border border-surface-border "
        "rounded-sm bg-white text-inherit"
    )


def knob_wrapper_classes() -> str:
    """Trainer-panel knob wrapper (`.data-knob`). 5+ uses; each
    knob (window / end-date / seed / scope / etl-hook) wraps its
    label + inputs in one of these. Spacing-override variants
    (e.g. `gap-1` for tighter knobs) compose at call site."""
    return (
        "flex items-center gap-2 px-2 py-1 border "
        "border-surface-border rounded-sm bg-surface-bg"
    )


def timeline_day_classes() -> str:
    """Trainer-panel timeline row. **90×/page** — highest-leverage
    helper in the module. Variants at call site:
    - `--empty` / `--future`: append ` py-px px-2 border-transparent text-secondary-fg`
    - `--anchor`: append ` border-accent border-2 px-1.5 py-1.5 bg-accent/6 font-semibold relative hover:bg-accent/10`
    """
    # Note: `font-inherit` / `text-inherit` for the underlying
    # `<button>` element are redundant — Tailwind v4 preflight
    # already applies `font: inherit` + `color: inherit` to form
    # controls. Skipped here for that reason.
    return (
        "flex items-center gap-2 px-2 py-1 border "
        "border-surface-border rounded-sm bg-white cursor-pointer "
        "text-left transition-colors scroll-m-4 hover:bg-surface-bg "
        "hover:border-accent"
    )


def timeline_chip_base_classes() -> str:
    """Trainer-panel timeline-row chips. Base for 4 kind variants
    (drift / overdraft / stuck / supersession). Variant additions
    at call site:
    - `--drift`: append ` bg-accent/12 text-accent border-accent/25`
    - `--overdraft / --limit_breach`: append ` bg-danger/12 text-danger border-danger/25`
    - `--stuck_pending / --stuck_unbundled`: append ` bg-warning/12 text-warning border-warning/25`
    - `--supersession`: append ` bg-success/12 text-success border-success/25`
    """
    return (
        "text-xs font-semibold tracking-wide px-1.5 py-0.5 "
        "rounded-sm bg-surface-bg text-secondary-fg "
        "border border-surface-border"
    )
