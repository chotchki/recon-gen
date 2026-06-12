"""no-raw-enum-equality smoke fixture — planted raw-string comparisons
against the canonical enum values, plus negative-control shapes that
the lint MUST NOT flag.

This file lives OUTSIDE the lint's normal ``tests/`` scope (per the
fixtures-dir filter in ``_build_checks``) so the production run isn't
affected; the smoke test invokes the visitor directly on this file.
"""

from __future__ import annotations


# -- Planted violations (lint MUST flag) ---------------------------------


def planted_eq_posted() -> bool:
    status = "Posted"
    return status == "Posted"  # planted


def planted_neq_internal_initiated() -> bool:
    origin = "InternalInitiated"
    return origin != "InternalInitiated"  # planted


def planted_in_tuple_external_force_posted() -> bool:
    origin = "ExternalForcePosted"
    return origin in ("ExternalForcePosted", "InternalInitiated")  # planted x2


def planted_external_aggregated_membership() -> bool:
    origin = "ExternalAggregated"
    return origin in {"ExternalAggregated"}  # planted


def planted_reversed_order() -> bool:
    status = "Posted"
    return "Posted" == status  # planted (raw on the LEFT side of comparison)


def planted_eq_debit() -> bool:
    direction = "Debit"
    return direction == "Debit"  # planted (AmountDirection)


def planted_neq_credit() -> bool:
    direction = "Credit"
    return direction != "Credit"  # planted (AmountDirection)


def planted_eq_internal() -> bool:
    scope = "internal"
    return scope == "internal"  # planted (Scope)


def planted_neq_external() -> bool:
    scope = "external"
    return scope != "external"  # planted (Scope)


def planted_eq_inflight() -> bool:
    supersedes = "Inflight"
    return supersedes == "Inflight"  # planted (SupersedeReason)


def planted_neq_bundle_assignment() -> bool:
    supersedes = "BundleAssignment"
    return supersedes != "BundleAssignment"  # planted (SupersedeReason)


def planted_eq_technical_correction() -> bool:
    supersedes = "TechnicalCorrection"
    return supersedes == "TechnicalCorrection"  # planted (SupersedeReason)


# -- Negative controls (lint MUST NOT flag) ------------------------------


def negative_constructor_call_input() -> dict[str, str]:
    """Raw-string AS A KEYWORD ARG to the function under test — wire-shape
    input, not internal-state comparison. NOT flagged."""
    return {"status": "Posted", "origin": "InternalInitiated"}


def negative_dict_literal_value() -> dict[str, str]:
    """Raw-string as a dict VALUE (not in a Compare context). NOT
    flagged."""
    return {"Posted": "the canonical status"}


def negative_unrelated_string() -> bool:
    """Raw-string equality against a non-enum value. NOT flagged."""
    s = "PostedYesterday"
    return s == "PostedYesterday"


def negative_substring() -> bool:
    """Substring match is in ``Compare(In)`` shape but the literal is a
    longer string that contains "Posted" — NOT one of the exact enum
    values; lint MUST NOT flag."""
    s = "TodaysPostings"
    return "Postings" in s  # not exact "Posted"
