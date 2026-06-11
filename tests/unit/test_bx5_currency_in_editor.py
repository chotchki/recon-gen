"""BX.5 — Inline currency formatting via FieldSpec.

Per the BX overnight plan + CLAUDE.md "Mark money measures with
currency=True so the emitter formats $1,234.56 instead of 1234.56":
the L2 editor read cards previously rendered `cap` /
`expected_eod_balance` / `expected_net` as bare Decimal strings
("5000.00"). They now format as USD ("$5,000.00") on the read card,
keying off the FieldSpec's existing `kind="money"` typed primitive —
no redundant `currency=True` flag needed (the kind IS the typed
signal, per `[feedback_invariants_in_types]`).

Tests cover:

- The pure-format helper `_format_money_for_display` (positive,
  negative, thousand-separators, sub-dollar, malformed).
- `_render_read_value` integration — money FieldSpec → escaped
  formatted output; non-money FieldSpec → unchanged behavior.
- End-to-end through the LimitSchedule read card so we catch
  contract drift (FieldSpec.kind rename → these tests break first).
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html._studio_editor_routes import (
    FieldSpec,
    _format_money_for_display,
    _render_read_value,
)
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from tests._test_helpers import make_test_config


REPO_ROOT = Path(__file__).parent.parent.parent
FIXTURES = REPO_ROOT / "tests" / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    src = FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


def _build_app(yaml_path: Path) -> object:
    cache = L2InstanceCache.from_path(yaml_path)
    cfg = make_test_config()
    tree_app, sheet = build_smoke_app(cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    return make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(cache),
    )


# ---------------------------------------------------------------------------
# Pure formatter — covers the edge cases without touching the renderer.
# ---------------------------------------------------------------------------


def test_format_money_for_display_positive_with_thousands_separator() -> None:
    """`1234.56` becomes `$1,234.56` — the canonical CLAUDE.md
    example. Thousand-separator is mandatory; bare `$1234.56` is the
    pre-BX.5 shape we're trying to leave behind."""
    assert _format_money_for_display("1234.56") == "$1,234.56"


def test_format_money_for_display_zero() -> None:
    """Zero is `$0.00`, not `$0` — the 2dp quantization is fixed by
    the Money primitive's contract."""
    assert _format_money_for_display("0") == "$0.00"


def test_format_money_for_display_negative_sign_before_symbol() -> None:
    """Negative values render as `-$X` (sign before `$`), matching the
    QS `currency=True` USD convention + tree-fields parity."""
    assert _format_money_for_display("-500.5") == "-$500.50"


def test_format_money_for_display_large_value() -> None:
    """Million-scale value carries two thousand-separators."""
    assert _format_money_for_display("1234567.89") == "$1,234,567.89"


def test_format_money_for_display_sub_dollar() -> None:
    """Sub-dollar value still shows `$0.XX` — not just `XX cents`."""
    assert _format_money_for_display("0.05") == "$0.05"


def test_format_money_for_display_returns_none_on_garbage() -> None:
    """Non-numeric input returns ``None`` so the caller falls through
    to the plain-escape path; the loader rejects malformed money
    upstream so this should never fire in practice."""
    assert _format_money_for_display("not a number") is None


def test_format_money_for_display_quantizes_3dp_input() -> None:
    """Over-precision input (3dp) rounds to 2dp via Decimal.quantize.
    Operator never sees this case because the loader normalizes to
    2dp, but the formatter handles it without raising."""
    # Decimal default rounding is ROUND_HALF_EVEN — 1.235 → 1.24
    assert _format_money_for_display("1.235") == "$1.24"


# ---------------------------------------------------------------------------
# _render_read_value branch — covers integration with FieldSpec.kind
# ---------------------------------------------------------------------------


def test_render_read_value_money_kind_formats_as_currency() -> None:
    """A `kind="money"` FieldSpec routes through the currency formatter.
    Drives the `LimitSchedule.cap`, `Account.expected_eod_balance`,
    `TransferTemplate.expected_net` read cards in the editor."""
    spec = FieldSpec(
        name="cap", label="Cap", helper="", kind="money", required=True,
    )
    rendered = _render_read_value(spec, Decimal("5000.00"))
    assert "$5,000.00" in rendered


def test_render_read_value_money_kind_handles_negative() -> None:
    """Negative money value renders with the sign before the dollar
    symbol, matching the formatter contract."""
    spec = FieldSpec(name="bal", label="Bal", helper="", kind="money")
    rendered = _render_read_value(spec, Decimal("-1234.56"))
    assert "-$1,234.56" in rendered


def test_render_read_value_money_kind_empty_renders_em_dash() -> None:
    """An unset money field renders the same `—` placeholder as every
    other empty field — operator sees a consistent "no value" glyph."""
    spec = FieldSpec(name="cap", label="Cap", helper="", kind="money")
    assert _render_read_value(spec, None) == "—"


def test_render_read_value_text_kind_unchanged_by_money_branch() -> None:
    """A `kind="text"` FieldSpec doesn't go through the currency
    formatter — even if the value happens to be Decimal-shaped. The
    money branch keys strictly off `spec.kind`."""
    spec = FieldSpec(name="role", label="Role", helper="", kind="text")
    rendered = _render_read_value(spec, "1234.56")
    # No `$` prefix, no thousand-separator inserted.
    assert "$" not in rendered
    assert "1234.56" in rendered


def test_render_read_value_money_value_escaped() -> None:
    """The formatted money string passes through `escape()` so any
    rogue formatting characters (impossible from a Decimal, but a
    contract-level safety) can't break out of the dl cell."""
    spec = FieldSpec(name="cap", label="Cap", helper="", kind="money")
    rendered = _render_read_value(spec, Decimal("1234.56"))
    # The output is plain text — no markup beyond what escape() emits.
    # No `<script>` or other HTML can survive escape().
    assert "<" not in rendered
    assert ">" not in rendered


# ---------------------------------------------------------------------------
# End-to-end through the LimitSchedule read card.
# ---------------------------------------------------------------------------


def test_limit_schedule_read_card_renders_cap_as_currency(
    writable_l2_yaml: Path,
) -> None:
    """The LimitSchedule read card body shows `cap` formatted as USD.
    spec_example.yaml has caps of 5000.00 + 3000.00; both should
    render with thousand-separators + dollar sign.

    Post-CG.5 cards are collapsed-by-default for all kinds, so the
    body fragment is fetched via `?body_only=1` rather than rendered
    inline on the list page (same shape as CF.4.g's chip-list test)."""
    import re  # noqa: PLC0415 — keep import local to the e2e test
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        list_body = c.get("/l2_shape/limit_schedule/").text
        ids = re.findall(r'data-entity-id="([^"]+)"', list_body)
        assert ids, "fixture has no limit_schedules"
        # Fetch every LS body — spec_example only has 2 + we want both
        # caps to show up so a one-off matching false positive (e.g.
        # the fixture's 3000.00 sneaking into a description) doesn't
        # mask a real regression.
        bodies = [
            c.get(f"/l2_shape/limit_schedule/{lid}?body_only=1").text
            for lid in ids
        ]
    combined = "\n".join(bodies)
    # Pre-BX.5 the cap rendered as plain "5000.00"; post-BX.5 it's
    # "$5,000.00". This assertion would fail if we ever revert.
    assert "$5,000.00" in combined
    assert "$3,000.00" in combined
