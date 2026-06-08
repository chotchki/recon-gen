"""CQ.3.e smoke fixture — planted L2-derived StaticValues callsite.

The lint must flag the planted call below; if it doesn't, the AST
walker drifted and the real-corpus zero-hit run is meaningless.

This file lives OUTSIDE the lint's normal apps/ scope so the production
run isn't affected; the smoke test invokes the visitor directly on
this file.
"""

from __future__ import annotations


# Stub: stand-in for the real ``StaticValues`` + helper functions.
class StaticValues:  # noqa: D101
    def __init__(self, *, values: list[str]) -> None:
        self.values = values


def some_helper(l2_instance: object) -> list[str]:
    return ["one", "two"]


def planted_violation() -> None:
    """The exact shape CQ.3 forbids: StaticValues whose values argument
    reads from `l2_instance` via a helper call. The lint flags this."""
    l2_instance = object()
    _ = StaticValues(values=some_helper(l2_instance))


def fixed_form_acceptable() -> None:
    """StaticValues with a fixed-code-enum source — no `l2_instance`
    reference anywhere in the arg subtree. Lint must NOT flag this."""
    _ = StaticValues(values=["Posted", "Reversed", "Pending"])
