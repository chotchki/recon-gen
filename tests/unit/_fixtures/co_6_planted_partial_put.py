"""CO.6 planted fixture — partial-form PUT in a test body.

This file sits under tests/unit/_fixtures/ which is EXCLUDED from the
real ``no-partial-form-PUT-in-tests`` lint scope (see ``_build_checks``)
so it doesn't self-trip the rule during normal pytest runs. The
companion smoke test ``test_co_6_no_partial_form_put_in_tests_finds_planted``
invokes the lint directly on this file and asserts the visitor flags
exactly the one planted call below.

If the lint regresses (the visitor stops walking, the URL regex
drifts, the required-field count import breaks), the smoke test goes
red even when the real-corpus run reports 0 — which it always should,
since CO.5 already converted the original test-591 single-field PUT
to a full-body one. CO.6's job is to prevent regression.
"""
from __future__ import annotations

import httpx


def planted_partial_put_smell() -> None:
    """Should trip the lint — 1 field on an entity that requires ≥2."""
    client = httpx.Client()
    # Account requires multiple fields (account_id + account_role + account_name +
    # account_scope at minimum); sending just description is partial.
    client.put(
        "/l2_shape/account/some-id",
        data={"description": "partial body — missing required fields"},
    )


def planted_full_put_no_smell() -> None:
    """Should NOT trip the lint — full body."""
    client = httpx.Client()
    client.put(
        "/l2_shape/rail/some-rail",
        data={
            "rail_id": "some-rail",
            "rail_name": "Some Rail",
            "rail_subtype": "two_leg",
            "source_role": "customer-subledger",
            "destination_role": "gl-control",
            "transfer_type": "ach",
            "metadata_keys": "",
            "description": "",
        },
    )


def planted_new_url_no_smell() -> None:
    """Should NOT trip the lint — /new is the create form, body is allowed
    to be partial (it goes to the form-handler, not save)."""
    client = httpx.Client()
    client.post("/l2_shape/account/new", data={"account_id": "test"})
