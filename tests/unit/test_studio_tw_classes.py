"""AM.1 step 2 — pin the Tailwind utility-string helpers.

Per AM.0 lock **L2.a** (helpers return ONE utility string + zero/
one-bool param; variants compose at the call site, NOT internally).
These tests catch:

1. Accidental class-string drift (any change to a helper's return
   value is a deliberate edit, not a stray refactor).
2. L2.a violations — every helper must be a zero-param `def name()
   -> str` (or at most one bool param). If a helper grows beyond
   that, it's reinventing `@apply`'s component-class problem in
   Python.
3. Utility-presence in the compiled output.css (every utility a
   helper references MUST exist in the compiled CSS, otherwise the
   class is dead text + the page renders unstyled).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path

import pytest

from recon_gen.common.html._studio_assets import tw_classes


# All 10 helpers from the AM.0.2 mapping doc. If you add a new
# helper to tw_classes.py, add it here too — the test ensures every
# public helper is exercised + locked.
_HELPERS = (
    tw_classes.entity_card_classes,
    tw_classes.field_row_classes,
    tw_classes.field_input_classes,
    tw_classes.primary_button_classes,
    tw_classes.chrome_button_classes,
    tw_classes.ghost_button_classes,
    tw_classes.compact_input_classes,
    tw_classes.knob_wrapper_classes,
    tw_classes.timeline_day_classes,
    tw_classes.timeline_chip_base_classes,
)


@pytest.mark.parametrize("helper", _HELPERS, ids=lambda h: h.__name__)
def test_helper_returns_nonempty_utility_string(helper: "Callable[[], str]") -> None:
    """Every helper returns a non-empty `str` of space-separated
    Tailwind utilities."""
    result = helper()
    assert isinstance(result, str), (
        f"{helper.__name__} returned {type(result).__name__}; "
        f"helpers must return str per L2.a"
    )
    assert result.strip(), (
        f"{helper.__name__} returned empty string; that's a smell"
    )
    # Sanity: looks like space-separated utilities, not commas or
    # other delimiters that would suggest a CSS-class-list shape.
    assert "," not in result, (
        f"{helper.__name__} returned a comma-separated value "
        f"{result!r}; HTML class= attributes are space-separated"
    )


@pytest.mark.parametrize("helper", _HELPERS, ids=lambda h: h.__name__)
def test_helper_takes_zero_params(helper: "Callable[[], str]") -> None:
    """L2.a guardrail enforcement — every helper is zero-param.

    A `card_classes(variant="editing")` that switches internally
    reinvents `@apply`'s component-class problem in Python. Compose
    variants at the call site instead:

    >>> cls = entity_card_classes()
    >>> if editing: cls += " border-accent ring-2 ring-accent/15"

    Allowed exception (rare): one bool param. If a helper needs
    more than that, the L2.a guardrail says split it up — that's
    the smell signalling a fork is starting.
    """
    sig = inspect.signature(helper)
    n_params = len(sig.parameters)
    assert n_params <= 1, (
        f"{helper.__name__} takes {n_params} parameters — L2.a "
        f"caps helpers at zero-param (preferred) or one bool param. "
        f"More than that means we're reinventing the component-class "
        f"abstraction Tailwind exists to avoid. Split the helper or "
        f"move the state into the caller (compose variants at the "
        f"call site)."
    )
    if n_params == 1:
        only_param = next(iter(sig.parameters.values()))
        ann = only_param.annotation
        assert ann is bool or ann == "bool", (
            f"{helper.__name__}'s single param has annotation "
            f"{ann!r}; L2.a allows the single-param exception ONLY "
            f"for `bool`. Other types (enums, strings) signal "
            f"branching state that should compose at the call site."
        )


def test_every_utility_in_output_css() -> None:
    """Every utility class referenced by a helper MUST exist in
    the compiled `output.css`. Catches missed `@source` paths +
    typos like `text-acent` that would silently render unstyled.

    Tailwind v4 escapes special chars in class names via `\\` —
    e.g. `bg-accent/12` lands as `.bg-accent\\/12{...}`. The match
    accounts for this. Pseudo-prefixed utilities (`hover:`, `focus:`,
    `sm:`, `disabled:`, `last:`) also get escaped.
    """
    output_css_path = (
        Path(__file__).resolve().parents[2]
        / "src" / "recon_gen" / "common" / "html"
        / "assets" / "output.css"
    )
    assert output_css_path.exists(), (
        f"output.css not found at {output_css_path}; "
        "AM.1 step 1 prereq missing — run scripts/build_app2_css.py"
    )
    output_css = output_css_path.read_text()

    missing: list[tuple[str, str]] = []
    for helper in _HELPERS:
        utilities = helper().split()
        for util in utilities:
            # CSS escapes special chars in class names (`/`, `:`,
            # `.`) with `\`; apply the same escaping Tailwind v4
            # emits when looking for the rule in the compiled output.
            # E.g. `py-0.5` → `.py-0\.5{...}`, `bg-accent/12` →
            # `.bg-accent\/12{...}`, `hover:opacity-85` →
            # `.hover\:opacity-85:hover{...}`.
            escaped = (
                util.replace("/", r"\/")
                .replace(":", r"\:")
                .replace(".", r"\.")
            )
            needle = f".{escaped}"
            # Match the utility followed by `{`, `,`, ` ` (descendant),
            # `:` (own pseudo like `:hover`), or whitespace — anything
            # that's a valid selector-terminator. Conservative: match
            # the class anywhere it appears in the CSS.
            if needle not in output_css:
                missing.append((helper.__name__, util))

    assert not missing, (
        f"Utilities referenced by helpers but absent from output.css "
        f"({len(missing)}):\n" + "\n".join(
            f"  {h}() → {u}" for h, u in missing[:20]
        )
        + (
            f"\n  ... + {len(missing) - 20} more"
            if len(missing) > 20 else ""
        )
        + "\nRebuild output.css via scripts/build_app2_css.py "
          "(check input.css @source directives cover the routes the "
          "helpers serve)."
    )
