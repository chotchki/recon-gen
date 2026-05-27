"""Unit tests for ``ColumnSpec.human_name`` (v8.5.0).

Pre-v8.5.0 QuickSight Table visuals rendered raw snake_case column
names as the visible header (``account_id``, ``business_day_start``,
etc.). v8.5.0 adds ``ColumnSpec.display_name`` + a ``human_name``
property that returns either the override or a smart-title-cased
form of the column name (with common initialisms preserved as
uppercase: ``id`` → ``ID``, ``eod`` → ``EOD``).
"""

from __future__ import annotations

from recon_gen.common.dataset_contract import (
    ColumnSpec,
    _smart_title,
)


class TestSmartTitle:
    def test_simple_snake_case(self) -> None:
        assert _smart_title("account_role") == "Account Role"

    def test_three_words(self) -> None:
        assert _smart_title("business_day_start") == "Business Day Start"

    def test_id_initialism_preserved(self) -> None:
        assert _smart_title("account_id") == "Account ID"
        assert _smart_title("transfer_id") == "Transfer ID"

    def test_eod_initialism_preserved(self) -> None:
        assert _smart_title("expected_eod_balance") == "Expected EOD Balance"

    def test_url_initialism_preserved(self) -> None:
        assert _smart_title("source_url") == "Source URL"

    def test_initialism_at_start_word(self) -> None:
        # First word can also be an initialism.
        assert _smart_title("id_column") == "ID Column"

    def test_single_word(self) -> None:
        assert _smart_title("amount") == "Amount"

    def test_already_lowercase_initialism_only(self) -> None:
        assert _smart_title("id") == "ID"

    def test_empty_string(self) -> None:
        assert _smart_title("") == ""


class TestColumnSpecHumanName:
    def test_default_uses_smart_title_of_name(self) -> None:
        c = ColumnSpec(name="account_id", type="STRING")
        assert c.human_name == "Account ID"

    def test_display_name_override(self) -> None:
        # Author can override when title-case is awkward.
        c = ColumnSpec(
            name="amount_money",
            type="DECIMAL",
            display_name="Amount",
        )
        assert c.human_name == "Amount"

    def test_display_name_can_be_unrelated_to_column_name(self) -> None:
        # Override is a free-form string — no validation against
        # the underlying name. Trust the author.
        c = ColumnSpec(
            name="signed_amount",
            type="DECIMAL",
            display_name="$ Net Movement",
        )
        assert c.human_name == "$ Net Movement"

    def test_three_word_default(self) -> None:
        c = ColumnSpec(name="business_day_start", type="DATETIME")
        assert c.human_name == "Business Day Start"

    def test_default_for_calc_field_shaped_name(self) -> None:
        # Calc field-shaped names (no underscores) round-trip cleanly.
        c = ColumnSpec(name="drift", type="DECIMAL")
        assert c.human_name == "Drift"  # typing-smell: ignore[no-inline-production-constants]: column-title-case output of _smart_title("drift"); coincidentally matches _DRIFT_NAME (L1 sheet name) — different concept
