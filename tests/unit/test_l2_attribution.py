"""DZ.10 — the L2 ``attribution:`` block (loader + serializer round-trip).

The override dataclass (``Attribution``) lives in ``common/attribution.py``
so the dependency-free credit module stays its single home; the L2 model
carries it as a sibling of ``theme:``. These pin the parse rules
(field-optional, unknown-key reject, bool ``enabled``, empty-string
reject) + the all-default→None normalization that keeps the serializer
round-trip field-equal, plus one full load→serialize→load through a real
fixture so the emitted block survives.
"""

from __future__ import annotations

import dataclasses
import os
import tempfile
from pathlib import Path

import pytest

from recon_gen.common.attribution import Attribution
from recon_gen.common.l2.loader import (
    L2LoaderError,
    _load_attribution,
    load_instance,
)
from recon_gen.common.l2.serializer import serialize_l2

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "l2"


# -- loader parse rules ------------------------------------------------------


def test_load_full_block() -> None:
    a = _load_attribution(
        {
            "name": "Acme Recon",
            "url": "https://acme.example",
            "prefix": "Built by",
            "enabled": True,
        },
        path="attribution",
    )
    assert a == Attribution(
        name="Acme Recon", url="https://acme.example", prefix="Built by", enabled=True,
    )


def test_load_partial_block_leaves_other_fields_none() -> None:
    a = _load_attribution({"url": "mailto:ops@acme.example"}, path="attribution")
    assert a == Attribution(url="mailto:ops@acme.example")


def test_load_suppress_block() -> None:
    a = _load_attribution({"enabled": False}, path="attribution")
    assert a == Attribution(enabled=False)


def test_missing_block_is_none() -> None:
    assert _load_attribution(None, path="attribution") is None


def test_all_default_block_normalizes_to_none() -> None:
    # ``attribution: {}`` (or every field omitted) is equivalent to no
    # block — normalized to None so serialize stays compact + round-trips.
    assert _load_attribution({}, path="attribution") is None
    assert _load_attribution({"enabled": True}, path="attribution") is None


def test_unknown_key_rejected() -> None:
    with pytest.raises(L2LoaderError, match="unknown keys"):
        _load_attribution({"naem": "typo"}, path="attribution")


def test_non_bool_enabled_rejected() -> None:
    with pytest.raises(L2LoaderError, match="enabled"):
        _load_attribution({"enabled": "yes"}, path="attribution")


def test_empty_string_field_rejected() -> None:
    # Consistent with description: omit the key to drop a field, don't blank it.
    with pytest.raises(L2LoaderError):
        _load_attribution({"name": ""}, path="attribution")


# -- serializer round-trip through a real fixture ----------------------------


@pytest.mark.parametrize(
    "attribution",
    [
        Attribution(name="Acme Recon", url="https://acme.example", prefix="Built by"),
        Attribution(url="mailto:ops@acme.example"),  # partial
        Attribution(enabled=False),  # suppressed
    ],
)
def test_round_trip_preserves_attribution(attribution: Attribution) -> None:
    original = dataclasses.replace(
        load_instance(_FIXTURES_DIR / "spec_example.yaml"),
        attribution=attribution,
    )
    fd, tmp_str = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    tmp = Path(tmp_str)
    try:
        tmp.write_text(serialize_l2(original))
        round_tripped = load_instance(tmp)
    finally:
        tmp.unlink()
    assert round_tripped.attribution == attribution
    assert round_tripped == original


def test_serialize_omits_attribution_when_none() -> None:
    inst = load_instance(_FIXTURES_DIR / "spec_example.yaml")
    assert inst.attribution is None  # the fixture declares no block
    assert "attribution:" not in serialize_l2(inst)
