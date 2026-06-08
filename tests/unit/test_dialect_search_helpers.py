"""CQ.2.b — Per-dialect case-insensitive substring + LIKE-escape helpers.

Pin the SQL shape every dialect emits + the LIKE meta-character escape
ordering. Without these gates a future refactor could re-introduce the
LIKE-injection bug ("user types `5%` matches every row containing 5")
or break the PG/DuckDB collapse + Oracle UPPER divergence.
"""

from __future__ import annotations

import pytest

from recon_gen.common.sql import (
    Dialect,
    case_insensitive_substring_match,
    escape_like_pattern,
)


class TestEscapeLikePattern:
    def test_passes_through_normal_string(self) -> None:
        assert escape_like_pattern("Acme Corp") == "Acme Corp"

    def test_escapes_percent(self) -> None:
        """Without escape: ``5%`` matches every row containing ``5``."""
        assert escape_like_pattern("5%") == "5\\%"

    def test_escapes_underscore(self) -> None:
        """Without escape: ``_`` matches every row with any char at that
        position."""
        assert escape_like_pattern("ab_cd") == "ab\\_cd"

    def test_escapes_backslash_FIRST(self) -> None:
        """Backslash MUST be escaped first — otherwise the later ``%``
        / ``_`` escapes' own backslashes get double-escaped, and the
        SQL ``ESCAPE '\\'`` clause then mis-parses everything."""
        assert escape_like_pattern("a\\b") == "a\\\\b"

    def test_escape_order_when_string_has_all_three(self) -> None:
        """Combined: input ``\\%_`` becomes ``\\\\\\%\\_``.

        Order trace:
          ``\\%_`` → (escape backslash) → ``\\\\%_``
                  → (escape %)          → ``\\\\\\%_``
                  → (escape _)          → ``\\\\\\%\\_``
        """
        assert escape_like_pattern("\\%_") == "\\\\\\%\\_"

    def test_empty_string(self) -> None:
        assert escape_like_pattern("") == ""


class TestCaseInsensitiveSubstringMatch:
    def test_postgres_uses_ilike(self) -> None:
        result = case_insensitive_substring_match(
            "account_display", "q", Dialect.POSTGRES,
        )
        assert result == (
            "account_display ILIKE '%' || :q || '%' ESCAPE '\\'"
        )

    def test_duckdb_collapses_with_postgres(self) -> None:
        """DuckDB has native ILIKE — same branch as PG per
        dialect-convergence preference."""
        pg = case_insensitive_substring_match(
            "account_display", "q", Dialect.POSTGRES,
        )
        duck = case_insensitive_substring_match(
            "account_display", "q", Dialect.DUCKDB,
        )
        assert pg == duck

    def test_oracle_uppers_both_sides(self) -> None:
        """Oracle has no ILIKE — UPPER on both column and bind."""
        result = case_insensitive_substring_match(
            "ACCOUNT_DISPLAY", "q", Dialect.ORACLE,
        )
        assert result == (
            "UPPER(ACCOUNT_DISPLAY) LIKE '%' || UPPER(:q)"
            " || '%' ESCAPE '\\'"
        )

    @pytest.mark.parametrize("dialect", list(Dialect))
    def test_every_dialect_carries_escape_clause(
        self, dialect: Dialect,
    ) -> None:
        """Without ``ESCAPE '\\'`` the escape transform is silently
        ignored at parse time. The clause MUST appear in every dialect
        branch."""
        result = case_insensitive_substring_match(
            "col", "q", dialect,
        )
        assert "ESCAPE '\\'" in result

    def test_bind_name_threads_through(self) -> None:
        for dialect in Dialect:
            result = case_insensitive_substring_match(
                "col", "search_term", dialect,
            )
            assert ":search_term" in result
