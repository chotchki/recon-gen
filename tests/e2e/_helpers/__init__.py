"""Shared e2e test helpers (Phase DL).

Utilities that walk a built ``App`` tree to enumerate testable
artifacts (drills, picker writes, etc.) for parametrized e2e gates.
Kept under ``tests/e2e/_helpers/`` so the imports stay distinct from
``tests/e2e/_drivers/`` (browser/QS automation) and the harness modules
(``tests/e2e/_harness_*.py``).
"""
